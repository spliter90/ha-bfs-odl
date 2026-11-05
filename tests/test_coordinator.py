"""Tests für Coordinator, API-Fallbacks, Sensoren und Diagnose."""

from __future__ import annotations

from datetime import timedelta

import aiohttp
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.bfs_odl.const import (
    CONF_THRESHOLD,
    DOMAIN,
    LAYER_LATEST,
    MANUFACTURER,
    MODEL,
    UNIT_USV_H,
    WFS_URL,
)
from custom_components.bfs_odl.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import (
    OWS_EXCEPTION,
    WEHRHEIM,
    all_features,
    collection,
    full_list_requests,
    mock_full_list,
    mock_latest_cql,
    mock_timeseries,
    requests_with,
    timeseries,
    wehrheim,
)

DOSE = "sensor.wehrheim_ortsdosisleistung"
COSMIC = "sensor.wehrheim_kosmischer_anteil"
TERRESTRIAL = "sensor.wehrheim_terrestrischer_anteil"
MEAN = "sensor.wehrheim_7_tage_mittel"
TIME = "sensor.wehrheim_zeitpunkt_der_messung"
STATUS = "sensor.wehrheim_status_der_sonde"
ELEVATED = "binary_sensor.wehrheim_erhohte_strahlung"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    hass.config.language = "de"
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _refresh(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_setup_creates_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Sensoren, Attribute und Gerät einer Sonde."""
    mock_latest_cql(aioclient_mock, WEHRHEIM, [wehrheim()])
    mock_timeseries(aioclient_mock, WEHRHEIM, text=timeseries(WEHRHEIM, 0.118))
    await _setup(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    # Nur die gefilterte Abfrage, keine Gesamtliste
    assert full_list_requests(aioclient_mock) == 0

    dose = hass.states.get(DOSE)
    assert dose is not None
    assert float(dose.state) == 0.12
    assert dose.attributes["unit_of_measurement"] == UNIT_USV_H
    assert dose.attributes["state_class"] == "measurement"
    assert dose.attributes["station_name"] == "Wehrheim"
    assert dose.attributes["kenn"] == WEHRHEIM
    assert dose.attributes["height_above_sea"] == 330.0
    assert dose.attributes["validated"] is True
    assert dose.attributes["latitude"] == 50.3027
    assert dose.attributes["longitude"] == 8.5683

    assert float(hass.states.get(COSMIC).state) == 0.045
    assert float(hass.states.get(TERRESTRIAL).state) == 0.075
    assert float(hass.states.get(MEAN).state) == 0.118
    assert hass.states.get(STATUS).state == "in_operation"
    assert hass.states.get(TIME).attributes["device_class"] == "timestamp"
    assert dt_util.parse_datetime(hass.states.get(TIME).state) is not None
    assert hass.states.get(ELEVATED).state == STATE_OFF

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, WEHRHEIM)})
    assert device is not None
    assert device.manufacturer == MANUFACTURER
    assert device.model == MODEL
    assert device.configuration_url.startswith("https://odlinfo.bfs.de")


async def test_fallback_to_ogc_filter(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Lehnt der Server CQL ab, wird der OGC-Filter verwendet und gemerkt."""
    aioclient_mock.get(
        WFS_URL,
        params={"typeName": LAYER_LATEST, "CQL_FILTER": f"kenn='{WEHRHEIM}'"},
        text=OWS_EXCEPTION,
        status=400,
    )
    aioclient_mock.get(
        WFS_URL,
        params={"typeName": LAYER_LATEST, "filter": _ogc(WEHRHEIM)},
        text=collection([wehrheim()]),
    )
    mock_timeseries(aioclient_mock, WEHRHEIM)
    await _setup(hass, mock_config_entry)
    assert float(hass.states.get(DOSE).state) == 0.12

    await _refresh(hass, freezer)
    assert requests_with(aioclient_mock, "CQL_FILTER") == 1
    assert requests_with(aioclient_mock, "filter") == 2
    assert full_list_requests(aioclient_mock) == 0
    diag = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert diag["api"]["filter_mode"] == "ogc"


async def test_fallback_to_full_list(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Funktioniert kein Filter, wird die Gesamtliste lokal gefiltert."""
    aioclient_mock.get(
        WFS_URL,
        params={"typeName": LAYER_LATEST, "CQL_FILTER": f"kenn='{WEHRHEIM}'"},
        text=OWS_EXCEPTION,
        status=400,
    )
    aioclient_mock.get(
        WFS_URL,
        params={"typeName": LAYER_LATEST, "filter": _ogc(WEHRHEIM)},
        text="<html>Bad Request</html>",
        status=200,
    )
    mock_timeseries(aioclient_mock, WEHRHEIM)
    mock_full_list(aioclient_mock)
    await _setup(hass, mock_config_entry)

    assert float(hass.states.get(DOSE).state) == 0.12
    diag = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert diag["api"]["filter_mode"] == "list"


async def test_filter_ignored_by_server(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Ignoriert der Server den Filter (ganze Liste), wird lokal gefiltert."""
    mock_timeseries(aioclient_mock, WEHRHEIM)
    mock_full_list(aioclient_mock)
    await _setup(hass, mock_config_entry)
    assert float(hass.states.get(DOSE).state) == 0.12

    # Zweiter Abruf: auch der OGC-Filter wird ignoriert und als solcher erkannt
    await mock_config_entry.runtime_data.async_refresh()
    diag = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert diag["api"]["filter_mode"] == "list"

    # Weitere Abrufe nutzen die zwischengespeicherte Liste (TTL 15 min)
    calls = aioclient_mock.call_count
    freezer.tick(timedelta(minutes=5))
    await mock_config_entry.runtime_data.async_refresh()
    assert aioclient_mock.call_count == calls


async def test_unavailable_on_error_and_recovery(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Bei Ausfall des Dienstes werden die Entitäten unavailable und erholen sich."""
    mock_latest_cql(aioclient_mock, WEHRHEIM, [wehrheim()])
    mock_timeseries(aioclient_mock, WEHRHEIM)
    await _setup(hass, mock_config_entry)
    assert hass.states.get(DOSE).state != STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    aioclient_mock.get(WFS_URL, exc=aiohttp.ClientError("boom"))
    await _refresh(hass, freezer)
    for entity_id in (DOSE, COSMIC, STATUS, ELEVATED):
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    assert not mock_config_entry.runtime_data.last_update_success

    aioclient_mock.clear_requests()
    mock_latest_cql(aioclient_mock, WEHRHEIM, [wehrheim(value=0.13)])
    mock_timeseries(aioclient_mock, WEHRHEIM)
    await _refresh(hass, freezer)
    assert float(hass.states.get(DOSE).state) == 0.13


async def test_server_error_status(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """HTTP 503 beim Start: Einrichtung wird später erneut versucht."""
    aioclient_mock.get(WFS_URL, status=503, text="Service Unavailable")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_station_not_found(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Verschwindet die Sonde aus dem Datensatz, werden die Entitäten unavailable."""
    mock_latest_cql(aioclient_mock, WEHRHEIM, [wehrheim()])
    mock_timeseries(aioclient_mock, WEHRHEIM)
    await _setup(hass, mock_config_entry)

    aioclient_mock.clear_requests()
    mock_latest_cql(aioclient_mock, WEHRHEIM, [])
    mock_full_list(
        aioclient_mock,
        [f for f in all_features() if f["properties"]["kenn"] != WEHRHEIM],
    )
    await _refresh(hass, freezer)
    assert hass.states.get(DOSE).state == STATE_UNAVAILABLE
    assert "station_not_found" in repr(mock_config_entry.runtime_data.last_exception)


async def test_defective_station(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Defekte Sonde ohne Wert: Messwerte unavailable, Status bleibt sichtbar."""
    mock_latest_cql(
        aioclient_mock,
        WEHRHEIM,
        [wehrheim(value=None, cosmic=None, terrestrial=None, site_status=2)],
    )
    mock_timeseries(aioclient_mock, WEHRHEIM)
    await _setup(hass, mock_config_entry)

    assert hass.states.get(DOSE).state == STATE_UNAVAILABLE
    assert hass.states.get(COSMIC).state == STATE_UNAVAILABLE
    assert hass.states.get(ELEVATED).state == STATE_UNAVAILABLE
    assert hass.states.get(STATUS).state == "defective"


async def test_stale_value_is_unavailable(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Ein seit Tagen unveränderter Wert gilt als ausgefallen."""
    old = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(
        days=2
    )
    mock_latest_cql(aioclient_mock, WEHRHEIM, [wehrheim(end=old)])
    mock_timeseries(aioclient_mock, WEHRHEIM)
    await _setup(hass, mock_config_entry)

    assert hass.states.get(DOSE).state == STATE_UNAVAILABLE
    assert hass.states.get(TIME).state != STATE_UNAVAILABLE


async def test_elevated_by_threshold(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Über der festen Schwelle schaltet der Binärsensor ein."""
    mock_latest_cql(aioclient_mock, WEHRHEIM, [wehrheim(value=0.12)])
    mock_timeseries(aioclient_mock, WEHRHEIM, status=400, text=OWS_EXCEPTION)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=WEHRHEIM,
        data=dict(mock_config_entry.data),
        options={**mock_config_entry.options, CONF_THRESHOLD: 0.1},
    )
    await _setup(hass, entry)
    mock_config_entry = entry

    state = hass.states.get(ELEVATED)
    assert state.state == STATE_ON
    assert state.attributes["triggered_by"] == ["threshold"]
    assert state.attributes["mean_7d"] is None
    # Ohne Zeitreihe gibt es (noch) kein Mittel
    assert hass.states.get(MEAN).state == STATE_UNAVAILABLE
    assert mock_config_entry.runtime_data.timeseries_available is False


async def test_elevated_by_mean(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Mehr als 50 % über dem 7-Tage-Mittel schaltet den Binärsensor ein."""
    mock_latest_cql(aioclient_mock, WEHRHEIM, [wehrheim(value=0.16)])
    mock_timeseries(aioclient_mock, WEHRHEIM, text=timeseries(WEHRHEIM, 0.1))
    await _setup(hass, mock_config_entry)

    state = hass.states.get(ELEVATED)
    assert state.state == STATE_ON
    assert state.attributes["triggered_by"] == ["mean"]
    assert state.attributes["mean_7d"] == 0.1
    assert state.attributes["mean_limit"] == 0.15


async def test_timeseries_only_fetched_every_six_hours(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Die Zeitreihe wird nicht bei jedem Abruf geladen."""
    mock_latest_cql(aioclient_mock, WEHRHEIM, [wehrheim()])
    mock_timeseries(aioclient_mock, WEHRHEIM, text=timeseries(WEHRHEIM, 0.1))
    await _setup(hass, mock_config_entry)
    for _ in range(3):
        await _refresh(hass, freezer)
    assert requests_with(aioclient_mock, "viewparams") == 1


async def test_history_persists_across_reload(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Der eigene Verlauf überlebt einen Neustart der Integration."""
    mock_latest_cql(aioclient_mock, WEHRHEIM, [wehrheim()])
    mock_timeseries(aioclient_mock, WEHRHEIM, text=timeseries(WEHRHEIM, 0.1, 30))
    await _setup(hass, mock_config_entry)
    count = len(mock_config_entry.runtime_data.history)
    assert count == 31

    aioclient_mock.clear_requests()
    mock_latest_cql(aioclient_mock, WEHRHEIM, [wehrheim()])
    mock_timeseries(aioclient_mock, WEHRHEIM, status=400, text=OWS_EXCEPTION)
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(mock_config_entry.runtime_data.history) == count
    assert float(hass.states.get(MEAN).state) == 0.1


async def test_unload_and_diagnostics(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Diagnose liefert serialisierbare Daten; Entladen klappt."""
    mock_latest_cql(aioclient_mock, WEHRHEIM, [wehrheim()])
    mock_timeseries(aioclient_mock, WEHRHEIM, text=timeseries(WEHRHEIM, 0.1))
    await _setup(hass, mock_config_entry)

    diag = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert diag["data"]["station"]["kenn"] == WEHRHEIM
    assert isinstance(diag["data"]["station"]["end_measure"], str)
    assert diag["history"]["count"] == 73
    assert diag["api"]["filter_mode"] == "cql"

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


def _ogc(kenn: str) -> str:
    from custom_components.bfs_odl.api import _ogc_filter

    return _ogc_filter(kenn)
