"""Tests für den Config-Flow und den Optionen-Flow."""

from __future__ import annotations

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.bfs_odl.const import (
    CONF_KENN,
    CONF_PERCENT_ABOVE_MEAN,
    CONF_SCAN_INTERVAL,
    CONF_STATION_NAME,
    CONF_THRESHOLD,
    DOMAIN,
    WFS_URL,
)

from .conftest import (
    WEHRHEIM,
    collection,
    mock_full_list,
    mock_latest_cql,
    mock_timeseries,
    wehrheim,
)

pytestmark = pytest.mark.usefixtures("mock_setup_entry")


async def _start(hass: HomeAssistant, option: str) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["odl", "grid"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "odl"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["location", "manual"]
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": option}
    )


async def test_location_lists_nearest_stations(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Option A: die nächsten Sonden werden nach Entfernung sortiert angeboten."""
    hass.config.latitude = 50.3000
    hass.config.longitude = 8.5700
    mock_full_list(aioclient_mock)

    result = await _start(hass, "location")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "location"

    selector = result["data_schema"].schema[CONF_KENN]
    options = selector.config["options"]
    values = [option["value"] for option in options]
    # Kassel ist weit weg und steht ganz hinten, Wehrheim ganz vorn
    assert values[0] == WEHRHEIM
    assert values[-1] == "066110000"
    assert "km" in options[0]["label"]
    assert "[defekt]" in options[-1]["label"]
    assert "[Testbetrieb]" in next(
        o["label"] for o in options if o["value"] == "064340003"
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_KENN: WEHRHEIM}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Wehrheim"
    assert result["data"] == {CONF_KENN: WEHRHEIM, CONF_STATION_NAME: "Wehrheim"}
    assert result["options"] == {
        CONF_SCAN_INTERVAL: 30,
        CONF_THRESHOLD: 0.3,
        CONF_PERCENT_ABOVE_MEAN: 50,
    }
    assert result["result"].unique_id == WEHRHEIM


async def test_location_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Ist der BfS-Dienst nicht erreichbar, wird sauber abgebrochen."""
    aioclient_mock.get(WFS_URL, exc=aiohttp.ClientError())
    result = await _start(hass, "location")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_manual_by_kenn(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Option B: Einrichtung über die Kennung (CQL-Filter, keine Gesamtliste)."""
    mock_latest_cql(aioclient_mock, WEHRHEIM, [wehrheim()])

    result = await _start(hass, "manual")
    assert result["step_id"] == "manual"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"query": f" {WEHRHEIM} "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_KENN] == WEHRHEIM
    assert aioclient_mock.call_count == 1


async def test_manual_kenn_not_found(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Unbekannte Kennung: Fehlermeldung im Formular."""
    mock_latest_cql(aioclient_mock, "099999999", [])
    mock_full_list(aioclient_mock)

    result = await _start(hass, "manual")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"query": "099999999"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "station_not_found"}


async def test_manual_name_search_multiple(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Namenssuche mit mehreren Treffern führt zur Auswahl."""
    mock_full_list(aioclient_mock)

    result = await _start(hass, "manual")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"query": "wehrheim"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select"
    assert result["description_placeholders"] == {"count": "2"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_KENN: "064340003"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Wehrheim-Pfaffenwiesbach"


async def test_manual_name_search_single(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Genau ein Treffer wird direkt übernommen."""
    mock_full_list(aioclient_mock)

    result = await _start(hass, "manual")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"query": "Usingen"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_KENN] == "064340002"


@pytest.mark.parametrize(
    ("query", "error"),
    [("Atlantis", "no_results"), ("x", "query_too_short")],
)
async def test_manual_search_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    query: str,
    error: str,
) -> None:
    """Kein Treffer oder zu kurze Eingabe."""
    mock_full_list(aioclient_mock)

    result = await _start(hass, "manual")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"query": query}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_manual_cannot_connect_then_recover(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Verbindungsfehler wird angezeigt; ein zweiter Versuch klappt."""
    aioclient_mock.get(WFS_URL, exc=TimeoutError())
    result = await _start(hass, "manual")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"query": WEHRHEIM}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    mock_latest_cql(aioclient_mock, WEHRHEIM, [wehrheim()])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"query": WEHRHEIM}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_already_configured(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Dieselbe Sonde kann nicht zweimal eingerichtet werden."""
    mock_config_entry.add_to_hass(hass)
    mock_latest_cql(aioclient_mock, WEHRHEIM, [wehrheim()])

    result = await _start(hass, "manual")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"query": WEHRHEIM}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_multiple_stations_as_separate_entries(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Mehrere Sonden lassen sich als getrennte Einträge anlegen."""
    mock_full_list(aioclient_mock)
    for query in ("Usingen", "Bad Homburg"):
        result = await _start(hass, "manual")
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"query": query}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
    entries = hass.config_entries.async_entries(DOMAIN)
    assert {entry.unique_id for entry in entries} == {"064340002", "064340001"}


async def test_options_flow(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Optionen ändern Intervall und Schwellen."""
    mock_config_entry.add_to_hass(hass)
    mock_latest_cql(aioclient_mock, WEHRHEIM, [wehrheim()])
    mock_timeseries(aioclient_mock, WEHRHEIM, text=collection([]))

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 15.0, CONF_THRESHOLD: 0.25, CONF_PERCENT_ABOVE_MEAN: 80.0},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {
        CONF_SCAN_INTERVAL: 15,
        CONF_THRESHOLD: 0.25,
        CONF_PERCENT_ABOVE_MEAN: 80.0,
    }


async def test_options_flow_rejects_short_interval(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Weniger als 10 Minuten werden abgelehnt."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_SCAN_INTERVAL: 5, CONF_THRESHOLD: 0.3, CONF_PERCENT_ABOVE_MEAN: 50},
        )
