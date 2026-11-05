"""Tests für das Stromnetz (Energy-Charts): Menü-Flow, drei Endpunkte, Optionen."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.bfs_odl.const import (
    CONF_ENABLE_FREQUENCY,
    CONF_ENABLE_POWER,
    CONF_ENABLE_SIGNAL,
    CONF_ENTRY_TYPE,
    CONF_FREQUENCY_INTERVAL,
    CONF_FREQUENCY_STALE_AFTER,
    CONF_FREQUENCY_THRESHOLD,
    CONF_POSTAL_CODE,
    CONF_POWER_INTERVAL,
    CONF_SIGNAL_INTERVAL,
    DOMAIN,
    ENERGY_CHARTS_URL,
    ENTRY_TYPE_GRID,
    GRID_DEVICE_NAME,
    GRID_UNIQUE_ID,
)
from custom_components.bfs_odl.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.bfs_odl.energy_charts import parse_power, parse_signal

from .conftest import mock_full_list

NOW = "2026-11-05 10:07:30+00:00"
SIGNAL_URL = f"{ENERGY_CHARTS_URL}/signal"
FREQUENCY_URL = f"{ENERGY_CHARTS_URL}/frequency"
TOTAL_POWER_URL = f"{ENERGY_CHARTS_URL}/total_power"
PUBLIC_POWER_URL = f"{ENERGY_CHARTS_URL}/public_power"


# ---------------------------------------------------------------------------
# Gemockte API-Antworten
# ---------------------------------------------------------------------------


def _quarter(now: datetime) -> datetime:
    return now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)


def signal_payload(now: datetime) -> dict[str, Any]:
    """Ampel ab 1 h vor jetzt: jetzt gelb, ab dem nächsten Fenster 1 h grün."""
    start = _quarter(now) - timedelta(hours=1)
    times, shares, signals = [], [], []
    for i in range(4 * 28):
        slot = start + timedelta(minutes=15 * i)
        offset = i - 4  # 0 = aktuelles Zeitfenster
        if 1 <= offset <= 4:
            signal, share = 2, 82.0
        elif offset == 10:
            signal, share = -1, 55.0
        elif 20 <= offset <= 30:
            signal, share = 0, 31.0
        else:
            signal, share = 1, 58.5
        times.append(int(slot.timestamp()))
        shares.append(share)
        signals.append(signal)
    return {
        "unix_seconds": times,
        "share": shares,
        "signal": signals,
        "substitute": False,
        "deprecated": False,
    }


def _utc_ts(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp())


# Einmaliger Frequenzeinbruch um 08:45:00 UTC (in der letzten Stunde der Daten)
DIP_SECOND = _utc_ts("2026-11-05T08:45:00+00:00")
# Energy-Charts liefert die Frequenz mit rund 50 Minuten Verzug
LIVE_DELAY = 50 * 60


def frequency_value(second: int, dip: int = DIP_SECOND) -> float:
    """Gleichmäßige Schwankung um 50 Hz mit einem Einbruch."""
    if second == dip:
        return 49.93
    return round(50.0 + 0.02 * math.sin(second / 120), 4)


def frequency_side_effect(
    delay: int = LIVE_DELAY, offset_hz: float = 0.0, dip: int = DIP_SECOND
):
    """Liefert für start/end aus der Anfrage sekundengenaue Werte mit Verzug."""

    async def _side_effect(
        method: str, url: Any, data: Any
    ) -> AiohttpClientMockResponse:
        query = parse_qs(url.query_string)
        start = int(query["start"][0])
        end = min(int(query["end"][0]), int(dt_util.utcnow().timestamp()) - delay)
        seconds = list(range(start, end + 1))
        payload = {
            "unix_seconds": seconds,
            "data": [round(frequency_value(s, dip) + offset_hz, 4) for s in seconds],
            "deprecated": False,
        }
        return AiohttpClientMockResponse(method, url, text=json.dumps(payload))

    return _side_effect


POWER_SERIES = {
    "Load": 60000.0,
    "Residual load": 30000.0,
    "Wind onshore": 15000.0,
    "Wind offshore": 3000.0,
    "Solar": 9000.0,
    "Biomass": 4300.0,
    "Hydro Run-of-River": 1500.0,
    "Hydro water reservoir": 100.0,
    "Hydro pumped storage": 800.0,
    "Hydro pumped storage consumption": -500.0,
    "Fossil brown coal / lignite": 9800.0,
    "Fossil hard coal": 4100.0,
    "Fossil gas": 7400.0,
    "Fossil coal-derived gas": 700.0,
    "Cross border electricity trading": 2500.0,
    "Geothermal": 20.0,
}


def power_payload(
    now: datetime, *, with_share: bool = False, null_tail: int = 4
) -> dict[str, Any]:
    """24 h Viertelstundenwerte; die letzten Werte sind null (Verzug)."""
    start = _quarter(now) - timedelta(hours=24)
    times = [int((start + timedelta(minutes=15 * i)).timestamp()) for i in range(97)]
    types = []
    for name, value in POWER_SERIES.items():
        tail = null_tail + (4 if name == "Fossil gas" else 0)
        data: list[float | None] = [value] * (len(times) - tail) + [None] * tail
        types.append({"name": name, "data": data})
    if with_share:
        types.append(
            {
                "name": "Renewable share of load",
                "data": [55.5] * (len(times) - null_tail) + [None] * null_tail,
            }
        )
    return {"unix_seconds": times, "production_types": types, "deprecated": False}


def mock_grid_api(
    aioclient_mock: AiohttpClientMocker,
    now: datetime,
    *,
    power_status: int = 200,
    with_share: bool = False,
) -> None:
    """Registriert alle drei Endpunkte."""
    aioclient_mock.get(SIGNAL_URL, text=json.dumps(signal_payload(now)))
    aioclient_mock.get(FREQUENCY_URL, side_effect=frequency_side_effect())
    if power_status == 200:
        aioclient_mock.get(
            TOTAL_POWER_URL, text=json.dumps(power_payload(now, with_share=with_share))
        )
    else:
        aioclient_mock.get(
            TOTAL_POWER_URL, status=power_status, text='{"detail":"No data"}'
        )
        aioclient_mock.get(
            PUBLIC_POWER_URL, text=json.dumps(power_payload(now, with_share=True))
        )


def calls_to(aioclient_mock: AiohttpClientMocker, url: str) -> list[Any]:
    """Alle Anfragen an einen Endpunkt."""
    return [
        call_url
        for _, call_url, _, _ in aioclient_mock.mock_calls
        if str(call_url).startswith(url)
    ]


@pytest.fixture
def grid_entry() -> MockConfigEntry:
    """Ein Stromnetz-Eintrag mit allen drei Endpunkten."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Stromnetz Deutschland",
        unique_id=GRID_UNIQUE_ID,
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_GRID, CONF_POSTAL_CODE: "61273"},
        options={
            CONF_POSTAL_CODE: "61273",
            CONF_ENABLE_SIGNAL: True,
            CONF_ENABLE_FREQUENCY: True,
            CONF_ENABLE_POWER: True,
            CONF_SIGNAL_INTERVAL: 15,
            CONF_FREQUENCY_INTERVAL: 60,
            CONF_POWER_INTERVAL: 15,
            CONF_FREQUENCY_THRESHOLD: 100,
            CONF_FREQUENCY_STALE_AFTER: 180,
        },
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    hass.config.language = "de"
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _entity_id(hass: HomeAssistant, platform: str, unique_suffix: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{GRID_UNIQUE_ID}_{unique_suffix}"
    )
    assert entity_id is not None, unique_suffix
    return entity_id


def _state(hass: HomeAssistant, platform: str, unique_suffix: str) -> Any:
    state = hass.states.get(_entity_id(hass, platform, unique_suffix))
    assert state is not None
    return state


# ---------------------------------------------------------------------------
# Config-Flow (Menü)
# ---------------------------------------------------------------------------


async def test_menu_grid_flow(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    mock_setup_entry: None,
) -> None:
    """Menü → Stromnetz; die PLZ wird aus der nächsten BfS-Sonde vorgeschlagen."""
    freezer.move_to(NOW)
    hass.config.latitude = 50.3000
    hass.config.longitude = 8.5700
    mock_full_list(aioclient_mock)
    aioclient_mock.get(SIGNAL_URL, text=json.dumps(signal_payload(dt_util.utcnow())))

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["odl", "grid"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "grid"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "grid"
    schema = result["data_schema"].schema
    suggested = {
        str(key): key.description["suggested_value"]
        for key in schema
        if key.description and "suggested_value" in key.description
    }
    assert suggested[CONF_POSTAL_CODE] == "61273"
    assert suggested[CONF_ENABLE_FREQUENCY] is True

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_POSTAL_CODE: "61273",
            CONF_ENABLE_SIGNAL: True,
            CONF_ENABLE_FREQUENCY: False,
            CONF_ENABLE_POWER: True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Stromnetz Deutschland"
    assert result["data"] == {
        CONF_ENTRY_TYPE: ENTRY_TYPE_GRID,
        CONF_POSTAL_CODE: "61273",
    }
    assert result["options"][CONF_ENABLE_FREQUENCY] is False
    assert result["options"][CONF_SIGNAL_INTERVAL] == 15
    assert result["options"][CONF_FREQUENCY_INTERVAL] == 60
    assert result["options"][CONF_FREQUENCY_THRESHOLD] == 100
    assert result["result"].unique_id == GRID_UNIQUE_ID
    assert "postal_code=61273" in str(calls_to(aioclient_mock, SIGNAL_URL)[0])


@pytest.mark.parametrize(
    ("user_input", "errors"),
    [
        (
            {CONF_POSTAL_CODE: "6127", CONF_ENABLE_SIGNAL: True},
            {CONF_POSTAL_CODE: "invalid_postal_code"},
        ),
        (
            {CONF_POSTAL_CODE: "61273", CONF_ENABLE_SIGNAL: False},
            {"base": "nothing_enabled"},
        ),
    ],
)
async def test_grid_flow_validation(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_setup_entry: None,
    user_input: dict[str, Any],
    errors: dict[str, str],
) -> None:
    """Ungültige PLZ und keine Datenquelle werden abgelehnt."""
    mock_full_list(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "grid"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ENABLE_FREQUENCY: False, CONF_ENABLE_POWER: False, **user_input},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors


async def test_grid_flow_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_setup_entry: None
) -> None:
    """Energy-Charts nicht erreichbar; BfS ebenfalls nicht (kein PLZ-Vorschlag)."""
    aioclient_mock.get(SIGNAL_URL, exc=TimeoutError())
    aioclient_mock.get("https://www.imis.bfs.de/ogc/opendata/ows", exc=TimeoutError())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "grid"}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_POSTAL_CODE: "61273",
            CONF_ENABLE_SIGNAL: True,
            CONF_ENABLE_FREQUENCY: True,
            CONF_ENABLE_POWER: True,
        },
    )
    assert result["errors"] == {"base": "cannot_connect_grid"}


async def test_grid_already_configured(
    hass: HomeAssistant, grid_entry: MockConfigEntry, mock_setup_entry: None
) -> None:
    """Das Stromnetz gibt es nur einmal."""
    grid_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "grid"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "grid_already_configured"


# ---------------------------------------------------------------------------
# Netzampel
# ---------------------------------------------------------------------------


async def test_signal(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Aktuelles Zeitfenster nach Uhrzeit, Prognose, nächstes grünes Fenster."""
    freezer.move_to(NOW)
    now = dt_util.utcnow()
    mock_grid_api(aioclient_mock, now)
    await _setup(hass, grid_entry)
    assert grid_entry.state is ConfigEntryState.LOADED

    signal = _state(hass, "sensor", "signal_state")
    assert signal.entity_id == "sensor.stromnetz_deutschland_energy_charts_netzampel"
    assert signal.state == "yellow"
    assert signal.attributes["renewable_share"] == 58.5
    assert signal.attributes["slot_start"] == "2026-11-05T10:00:00+00:00"
    forecast = signal.attributes["forecast"]
    assert len(forecast) == 24 * 4 + 1
    assert forecast[0]["start"] == "2026-11-05T10:00:00+00:00"
    assert {f["state"] for f in forecast} == {
        "green",
        "yellow",
        "red",
        "grid_congestion",
    }

    assert float(_state(hass, "sensor", "signal_renewable_share").state) == 58.5
    green = _state(hass, "sensor", "signal_next_green")
    assert green.state == "2026-11-05T10:15:00+00:00"
    assert green.attributes["end"] == "2026-11-05T11:15:00+00:00"
    assert green.attributes["duration_minutes"] == 60
    assert _state(hass, "binary_sensor", "signal_good_time").state == STATE_OFF

    # Zur Viertelstunde wird ohne neuen Abruf neu berechnet
    requests = len(calls_to(aioclient_mock, SIGNAL_URL))
    freezer.move_to("2026-11-05 10:15:01+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls_to(aioclient_mock, SIGNAL_URL)) == requests
    assert _state(hass, "sensor", "signal_state").state == "green"
    assert _state(hass, "binary_sensor", "signal_good_time").state == STATE_ON
    assert _state(hass, "sensor", "signal_next_green").state == (
        "2026-11-05T10:15:00+00:00"
    )

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, GRID_UNIQUE_ID)})
    assert device is not None
    assert device.name == GRID_DEVICE_NAME
    assert device.configuration_url == "https://www.energy-charts.info/"


def test_signal_index_by_time_not_last_entry() -> None:
    """Das aktuelle Fenster wird über die Uhrzeit bestimmt."""
    now = dt_util.parse_datetime(NOW)
    assert now is not None
    data = parse_signal(signal_payload(now))
    current = data.current(now)
    assert current is not None
    assert current.start == _quarter(now)
    assert current != data.slots[-1]
    assert data.current(now - timedelta(days=3)) is None


# ---------------------------------------------------------------------------
# Netzfrequenz
# ---------------------------------------------------------------------------


async def test_frequency_50_minutes_old_is_valid(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Live-Verhalten: 50 Minuten alte Daten sind gültig; danach nur neue Werte."""
    freezer.move_to(NOW)  # 10:07:30, neuester Messwert 09:17:30
    mock_grid_api(aioclient_mock, dt_util.utcnow())
    await _setup(hass, grid_entry)

    # Erster Abruf: 4 h zurück (3 h Stale-Grenze + 1 h Fenster)
    first = calls_to(aioclient_mock, FREQUENCY_URL)[0]
    assert int(parse_qs(first.query_string)["start"][0]) == _utc_ts(
        "2026-11-05T06:07:30+00:00"
    )

    latest = _utc_ts("2026-11-05T09:17:30+00:00")
    frequency = _state(hass, "sensor", "frequency_frequency")
    assert frequency.state != STATE_UNAVAILABLE
    assert frequency.attributes["unit_of_measurement"] == "Hz"
    assert frequency.attributes["device_class"] == "frequency"
    assert frequency.attributes["state_class"] == "measurement"
    assert frequency.attributes["measured_at"] == "2026-11-05T09:17:30+00:00"
    assert frequency.attributes["data_age_minutes"] == 50.0
    assert frequency.attributes["samples_1h"] == 3601
    assert float(frequency.state) == frequency_value(latest)

    deviation = _state(hass, "sensor", "frequency_deviation")
    assert deviation.attributes["unit_of_measurement"] == "mHz"
    assert float(deviation.state) == pytest.approx(
        (frequency_value(latest) - 50) * 1000, abs=0.1
    )

    age = _state(hass, "sensor", "frequency_data_age")
    assert age.entity_id == (
        "sensor.stromnetz_deutschland_energy_charts_frequenz_datenalter"
    )
    assert float(age.state) == 50.0
    assert age.attributes["unit_of_measurement"] == "min"
    assert age.attributes["state_class"] == "measurement"
    registry = er.async_get(hass)
    assert registry.async_get(age.entity_id).entity_category == "diagnostic"

    minimum = _state(hass, "sensor", "frequency_minimum_1h")
    assert minimum.entity_id == (
        "sensor.stromnetz_deutschland_energy_charts_frequenz_minimum_1_h"
    )
    assert float(minimum.state) == 49.93
    assert minimum.attributes["window_start"] == "2026-11-05T08:17:30+00:00"
    assert minimum.attributes["window_end"] == "2026-11-05T09:17:30+00:00"
    assert minimum.attributes["window_basis"] == "latest_data"
    assert float(_state(hass, "sensor", "frequency_maximum_1h").state) == pytest.approx(
        50.02, abs=0.001
    )
    abnormal = _state(hass, "binary_sensor", "frequency_abnormal")
    assert abnormal.state == STATE_OFF
    assert abnormal.attributes["measured_at"] == "2026-11-05T09:17:30+00:00"

    # Nächster Abruf nach 60 s fragt nur ab der letzten Sekunde an
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    second = calls_to(aioclient_mock, FREQUENCY_URL)[-1]
    assert int(parse_qs(second.query_string)["start"][0]) == latest + 1
    assert (
        _state(hass, "sensor", "frequency_frequency").attributes["samples_1h"] == 3601
    )
    diag = await async_get_config_entry_diagnostics(hass, grid_entry)
    assert diag["coordinators"]["frequency"]["buffer_size"] == 3 * 3600 + 1


async def test_frequency_window_relative_to_latest_value(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Min/Max nur aus der Stunde vor dem neuesten Wert, nicht aus dem ganzen Puffer."""
    freezer.move_to(NOW)
    # Einbruch um 07:30: im 3-h-Puffer, aber vor der letzten Datenstunde (ab 08:17:30)
    dip = _utc_ts("2026-11-05T07:30:00+00:00")
    aioclient_mock.get(SIGNAL_URL, text=json.dumps(signal_payload(dt_util.utcnow())))
    aioclient_mock.get(FREQUENCY_URL, side_effect=frequency_side_effect(dip=dip))
    aioclient_mock.get(
        TOTAL_POWER_URL, text=json.dumps(power_payload(dt_util.utcnow()))
    )
    await _setup(hass, grid_entry)

    assert float(_state(hass, "sensor", "frequency_minimum_1h").state) > 49.97
    diag = await async_get_config_entry_diagnostics(hass, grid_entry)
    frequency = diag["coordinators"]["frequency"]
    assert frequency["buffer_start"] == "2026-11-05T06:17:30+00:00"
    assert frequency["buffer_end"] == "2026-11-05T09:17:30+00:00"
    assert frequency["stale_after_minutes"] == 180
    assert frequency["data"]["window_start"] == "2026-11-05T08:17:30+00:00"


async def test_frequency_abnormal(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Mehr als 100 mHz Abweichung schaltet den Binärsensor ein."""
    freezer.move_to(NOW)
    aioclient_mock.get(SIGNAL_URL, text=json.dumps(signal_payload(dt_util.utcnow())))
    aioclient_mock.get(
        FREQUENCY_URL, side_effect=frequency_side_effect(offset_hz=-0.15)
    )
    aioclient_mock.get(
        TOTAL_POWER_URL, text=json.dumps(power_payload(dt_util.utcnow()))
    )
    await _setup(hass, grid_entry)

    abnormal = _state(hass, "binary_sensor", "frequency_abnormal")
    assert abnormal.state == STATE_ON
    assert abnormal.attributes["threshold_mhz"] == 100
    assert -170 <= abnormal.attributes["deviation_mhz"] <= -130


async def test_frequency_older_than_stale_limit(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Fehlschläge allein ändern nichts; erst Daten über 3 h machen unavailable."""
    freezer.move_to("2026-11-05 10:45:00+00:00")  # neuester Wert 09:55:00
    mock_grid_api(aioclient_mock, dt_util.utcnow())
    await _setup(hass, grid_entry)
    assert _state(hass, "binary_sensor", "frequency_abnormal").state == STATE_OFF

    aioclient_mock.clear_requests()
    aioclient_mock.get(FREQUENCY_URL, exc=TimeoutError())
    aioclient_mock.get(SIGNAL_URL, text=json.dumps(signal_payload(dt_util.utcnow())))
    aioclient_mock.get(
        TOTAL_POWER_URL, text=json.dumps(power_payload(dt_util.utcnow()))
    )

    # 2 h später: Abrufe scheitern, Daten 2 h 50 min alt -> noch gültig
    freezer.tick(timedelta(hours=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert grid_entry.runtime_data.frequency.last_fetch_error is not None
    assert _state(hass, "sensor", "frequency_frequency").state != STATE_UNAVAILABLE
    assert _state(hass, "binary_sensor", "frequency_abnormal").state == STATE_OFF

    # 11 min später: Daten 3 h 1 min alt -> unavailable
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    for suffix in (
        "frequency_frequency",
        "frequency_deviation",
        "frequency_minimum_1h",
        "frequency_maximum_1h",
    ):
        assert _state(hass, "sensor", suffix).state == STATE_UNAVAILABLE, suffix
    assert (
        _state(hass, "binary_sensor", "frequency_abnormal").state == STATE_UNAVAILABLE
    )
    # Das Datenalter bleibt sichtbar
    assert float(_state(hass, "sensor", "frequency_data_age").state) == 181.0


async def test_frequency_stale_limit_is_configurable(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Mit 30 min Stale-Grenze sind 50 Minuten alte Daten unavailable."""
    freezer.move_to(NOW)
    mock_grid_api(aioclient_mock, dt_util.utcnow())
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=GRID_UNIQUE_ID,
        data=dict(grid_entry.data),
        options={**grid_entry.options, CONF_FREQUENCY_STALE_AFTER: 30},
    )
    await _setup(hass, entry)

    assert _state(hass, "sensor", "frequency_frequency").state == STATE_UNAVAILABLE
    assert (
        _state(hass, "binary_sensor", "frequency_abnormal").state == STATE_UNAVAILABLE
    )
    assert float(_state(hass, "sensor", "frequency_data_age").state) == 50.0
    # Erster Abruf trotzdem mindestens 3 h zurück
    first = calls_to(aioclient_mock, FREQUENCY_URL)[0]
    assert int(parse_qs(first.query_string)["start"][0]) == _utc_ts(
        "2026-11-05T07:07:30+00:00"
    )


# ---------------------------------------------------------------------------
# Last und Erzeugung
# ---------------------------------------------------------------------------


async def test_power(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Letzte Nicht-null-Werte in GW mit Zeitpunkt; Anteil Erneuerbare berechnet."""
    freezer.move_to(NOW)
    now = dt_util.utcnow()
    mock_grid_api(aioclient_mock, now)
    await _setup(hass, grid_entry)

    load = _state(hass, "sensor", "power_load")
    assert load.entity_id == "sensor.stromnetz_deutschland_energy_charts_netzlast"
    assert float(load.state) == 60.0
    assert load.attributes["unit_of_measurement"] == "GW"
    assert load.attributes["device_class"] == "power"
    assert load.attributes["state_class"] == "measurement"
    last_valid = _quarter(now) - timedelta(minutes=15 * 4)
    assert load.attributes["data_time"] == last_valid.isoformat()

    expected = {
        "power_residual_load": 30.0,
        "power_wind_onshore": 15.0,
        "power_wind_offshore": 3.0,
        "power_solar": 9.0,
        "power_biomass": 4.3,
        "power_hydro": 1.6,  # ohne Pumpspeicher
        "power_lignite": 9.8,
        "power_hard_coal": 4.1,
        "power_gas": 7.4,  # ohne Kokereigas
        "power_cross_border": -2.5,  # Energy-Charts +2500 MW = Import
    }
    for suffix, value in expected.items():
        assert float(_state(hass, "sensor", suffix).state) == pytest.approx(value), (
            suffix
        )

    trade = _state(hass, "sensor", "power_cross_border")
    assert trade.entity_id == (
        "sensor.stromnetz_deutschland_energy_charts_import_export_saldo"
    )
    assert trade.attributes["direction"] == "Import"

    gas = _state(hass, "sensor", "power_gas")
    assert gas.attributes["data_time"] == (last_valid - timedelta(hours=1)).isoformat()
    hydro = _state(hass, "sensor", "power_hydro")
    assert hydro.attributes["production_types"] == [
        "Hydro Run-of-River",
        "Hydro water reservoir",
    ]
    share = _state(hass, "sensor", "power_renewable_share_load")
    # (15 + 3 + 9 + 4,3 + 1,5 + 0,1 + 0,02) / 60
    assert float(share.state) == pytest.approx(54.9, abs=0.05)
    assert share.attributes["unit_of_measurement"] == "%"


async def test_power_fallback_to_public_power(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Liefert total_power nichts (404), wird public_power genutzt."""
    freezer.move_to(NOW)
    mock_grid_api(aioclient_mock, dt_util.utcnow(), power_status=404)
    await _setup(hass, grid_entry)

    share = _state(hass, "sensor", "power_renewable_share_load")
    assert float(share.state) == 55.5
    assert share.attributes["production_types"] == ["Renewable share of load"]
    diag = await async_get_config_entry_diagnostics(hass, grid_entry)
    assert diag["coordinators"]["power"]["endpoint"] == "public_power"


def test_cross_border_sign_positive_is_export() -> None:
    """Energy-Charts: negativ = Export; die Integration dreht auf positiv = Export."""
    now = dt_util.parse_datetime(NOW)
    assert now is not None
    payload = power_payload(now)
    for entry in payload["production_types"]:
        if entry["name"] == "Cross border electricity trading":
            entry["data"] = [-1800.0 if v is not None else None for v in entry["data"]]
    data = parse_power(payload)
    assert data.values["cross_border"].value == pytest.approx(1.8)


def test_parse_power_all_null() -> None:
    """Nur null-Werte ergeben keine Sensorwerte, aber keinen Absturz."""
    now = dt_util.parse_datetime(NOW)
    assert now is not None
    data = parse_power(power_payload(now, null_tail=97))
    assert data.values == {}
    assert data.renewable_share is None


# ---------------------------------------------------------------------------
# Fehler, Optionen, Diagnose
# ---------------------------------------------------------------------------


async def test_unavailable_when_energy_charts_down(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Fällt Energy-Charts aus, werden die Entitäten unavailable und erholen sich."""
    freezer.move_to(NOW)
    mock_grid_api(aioclient_mock, dt_util.utcnow())
    await _setup(hass, grid_entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(SIGNAL_URL, status=503, text="Service Unavailable")
    aioclient_mock.get(TOTAL_POWER_URL, exc=TimeoutError())
    aioclient_mock.get(PUBLIC_POWER_URL, exc=TimeoutError())
    aioclient_mock.get(FREQUENCY_URL, side_effect=frequency_side_effect())
    freezer.tick(timedelta(minutes=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert _state(hass, "sensor", "signal_state").state == STATE_UNAVAILABLE
    assert _state(hass, "binary_sensor", "signal_good_time").state == STATE_UNAVAILABLE
    assert _state(hass, "sensor", "power_solar").state == STATE_UNAVAILABLE
    # Die Frequenz läuft unabhängig weiter
    assert _state(hass, "sensor", "frequency_frequency").state != STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    mock_grid_api(aioclient_mock, dt_util.utcnow())
    freezer.tick(timedelta(minutes=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "power_solar").state == "9.0"


async def test_setup_retry_when_down(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
) -> None:
    """Ist Energy-Charts beim Start nicht erreichbar, wird es später erneut versucht."""
    aioclient_mock.get(SIGNAL_URL, exc=TimeoutError())
    grid_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(grid_entry.entry_id)
    await hass.async_block_till_done()
    assert grid_entry.state is ConfigEntryState.SETUP_RETRY


async def test_options_disable_source_removes_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Abgeschaltete Teile verschwinden; Intervalle und Schwelle sind einstellbar."""
    freezer.move_to(NOW)
    mock_grid_api(aioclient_mock, dt_util.utcnow())
    await _setup(hass, grid_entry)
    registry = er.async_get(hass)
    assert registry.async_get_entity_id(
        "sensor", DOMAIN, f"{GRID_UNIQUE_ID}_frequency_frequency"
    )

    result = await hass.config_entries.options.async_init(grid_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "grid"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_POSTAL_CODE: "60311",
            CONF_ENABLE_SIGNAL: True,
            CONF_ENABLE_FREQUENCY: False,
            CONF_ENABLE_POWER: True,
            CONF_SIGNAL_INTERVAL: 30,
            CONF_FREQUENCY_INTERVAL: 120,
            CONF_POWER_INTERVAL: 60,
            CONF_FREQUENCY_THRESHOLD: 150,
            CONF_FREQUENCY_STALE_AFTER: 240,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert grid_entry.options[CONF_FREQUENCY_THRESHOLD] == 150
    assert grid_entry.options[CONF_FREQUENCY_STALE_AFTER] == 240
    assert grid_entry.options[CONF_ENABLE_FREQUENCY] is False
    assert (
        registry.async_get_entity_id(
            "sensor", DOMAIN, f"{GRID_UNIQUE_ID}_frequency_frequency"
        )
        is None
    )
    runtime = grid_entry.runtime_data
    assert runtime.frequency is None
    assert runtime.signal.update_interval == timedelta(minutes=30)
    assert runtime.power.update_interval == timedelta(minutes=60)
    assert "postal_code=60311" in str(calls_to(aioclient_mock, SIGNAL_URL)[-1])


async def test_grid_options_reject_short_interval(
    hass: HomeAssistant, grid_entry: MockConfigEntry, mock_setup_entry: None
) -> None:
    """Frequenz-Intervall unter 30 s wird abgelehnt."""
    from homeassistant.data_entry_flow import InvalidData

    grid_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(grid_entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {**grid_entry.options, CONF_FREQUENCY_INTERVAL: 1},
        )


async def test_grid_diagnostics(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Diagnose aller Endpunkte, PLZ geschwärzt."""
    freezer.move_to(NOW)
    mock_grid_api(aioclient_mock, dt_util.utcnow())
    await _setup(hass, grid_entry)

    diag = await async_get_config_entry_diagnostics(hass, grid_entry)
    assert diag["entry"]["data"][CONF_POSTAL_CODE] == "**REDACTED**"
    assert diag["entry"]["options"][CONF_POSTAL_CODE] == "**REDACTED**"
    assert set(diag["coordinators"]) == {"signal", "frequency", "power"}
    assert diag["coordinators"]["signal"]["data"]["current"]["signal"] == 1
    assert diag["coordinators"]["frequency"]["buffer_size"] == 3 * 3600 + 1
    assert diag["coordinators"]["frequency"]["data_age_minutes"] == 50.0
    assert "Fossil gas" in diag["coordinators"]["power"]["production_types"]
    json.dumps(diag)


async def test_existing_odl_entry_unchanged_next_to_grid(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Bestehende ODL-Einträge (ohne entry_type) behalten unique_ids und Entity-IDs."""
    from .conftest import WEHRHEIM, mock_latest_cql, mock_timeseries, wehrheim

    freezer.move_to(NOW)
    assert CONF_ENTRY_TYPE not in mock_config_entry.data
    mock_latest_cql(aioclient_mock, WEHRHEIM, [wehrheim()])
    mock_timeseries(aioclient_mock, WEHRHEIM)
    mock_grid_api(aioclient_mock, dt_util.utcnow())
    await _setup(hass, mock_config_entry)
    await _setup(hass, grid_entry)

    registry = er.async_get(hass)
    odl = {
        e.unique_id: e.entity_id
        for e in er.async_entries_for_config_entry(registry, mock_config_entry.entry_id)
    }
    assert odl == {
        f"{WEHRHEIM}_dose_rate": "sensor.wehrheim_ortsdosisleistung",
        f"{WEHRHEIM}_dose_rate_cosmic": "sensor.wehrheim_kosmischer_anteil",
        f"{WEHRHEIM}_dose_rate_terrestrial": "sensor.wehrheim_terrestrischer_anteil",
        f"{WEHRHEIM}_dose_rate_mean_7d": "sensor.wehrheim_7_tage_mittel",
        f"{WEHRHEIM}_measurement_time": "sensor.wehrheim_zeitpunkt_der_messung",
        f"{WEHRHEIM}_status": "sensor.wehrheim_status_der_sonde",
        f"{WEHRHEIM}_elevated_radiation": "binary_sensor.wehrheim_erhohte_strahlung",
    }
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert grid_entry.state is ConfigEntryState.LOADED
