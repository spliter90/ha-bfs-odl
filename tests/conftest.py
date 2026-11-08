"""Gemeinsame Fixtures und Hilfsfunktionen für die Tests."""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.bfs_odl.const import (
    CONF_ENABLE_FREQUENCY,
    CONF_ENABLE_POWER,
    CONF_ENABLE_SIGNAL,
    CONF_ENTRY_TYPE,
    CONF_FREQUENCY_INTERVAL,
    CONF_FREQUENCY_STALE_AFTER,
    CONF_FREQUENCY_THRESHOLD,
    CONF_KENN,
    CONF_PERCENT_ABOVE_MEAN,
    CONF_POSTAL_CODE,
    CONF_POWER_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SIGNAL_INTERVAL,
    CONF_STATION_NAME,
    CONF_THRESHOLD,
    DOMAIN,
    ENTRY_TYPE_GRID,
    GRID_UNIQUE_ID,
    LAYER_LATEST,
    LAYER_TIMESERIES,
    WFS_URL,
)

WEHRHEIM = "064340121"

OWS_EXCEPTION = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows" version="1.0.0">'
    '<ows:Exception exceptionCode="InvalidParameterValue" locator="CQL_FILTER">'
    "<ows:ExceptionText>Filter not supported</ows:ExceptionText>"
    "</ows:Exception></ows:ExceptionReport>"
)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_feature(
    kenn: str,
    name: str,
    lon: float,
    lat: float,
    *,
    value: float | None = 0.12,
    cosmic: float | None = 0.045,
    terrestrial: float | None = 0.075,
    site_status: int = 1,
    end: datetime | None = None,
    plz: str = "61273",
) -> dict[str, Any]:
    """Ein GeoJSON-Feature im Format des BfS-Layers odlinfo_odl_1h_latest."""
    end = end or dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    status_text = {1: "in Betrieb", 2: "defekt", 3: "Testbetrieb"}[site_status]
    return {
        "type": "Feature",
        "id": f"odlinfo_odl_1h_latest.fid-{kenn}",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "geometry_name": "geom",
        "properties": {
            "id": f"DEZ{kenn}",
            "kenn": kenn,
            "plz": plz,
            "name": name,
            "site_status": site_status,
            "site_status_text": status_text,
            "kid": 6,
            "height_above_sea": 330.0,
            "start_measure": _iso(end - timedelta(hours=1)),
            "end_measure": _iso(end),
            "value": value,
            "value_cosmic": cosmic,
            "value_terrestrial": terrestrial,
            "unit": "µSv/h",
            "validated": 1,
            "nuclide": "Gamma-ODL-Brutto",
            "duration": "1h",
        },
    }


def collection(features: list[dict[str, Any]]) -> str:
    """Eine GeoJSON FeatureCollection als JSON-Text."""
    return json.dumps(
        {
            "type": "FeatureCollection",
            "features": features,
            "totalFeatures": len(features),
            "numberMatched": len(features),
            "numberReturned": len(features),
        }
    )


def wehrheim(**kwargs: Any) -> dict[str, Any]:
    """Die Beispielsonde Wehrheim."""
    return make_feature(WEHRHEIM, "Wehrheim", 8.5683, 50.3027, **kwargs)


def all_features() -> list[dict[str, Any]]:
    """Eine kleine Sondenliste rund um den Taunus."""
    return [
        wehrheim(),
        make_feature("064340001", "Bad Homburg", 8.6100, 50.2290, value=0.098),
        make_feature("064340002", "Usingen", 8.5370, 50.3360, value=0.105),
        make_feature("064120000", "Frankfurt am Main", 8.6820, 50.1100, value=0.087),
        make_feature("066110000", "Kassel", 9.4797, 51.3127, value=None, site_status=2),
        make_feature(
            "064340003", "Wehrheim-Pfaffenwiesbach", 8.5400, 50.3200, site_status=3
        ),
    ]


def timeseries(kenn: str, value: float, hours: int = 72) -> str:
    """Zeitreihe im Format von odlinfo_timeseries_odl_1h (neueste zuerst)."""
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    features = [
        {
            "type": "Feature",
            "id": f"ts.{kenn}.{i}",
            "geometry": {"type": "Point", "coordinates": [8.5683, 50.3027]},
            "properties": {
                "id": f"DEZ{kenn}",
                "kenn": kenn,
                "name": "Wehrheim",
                "start_measure": _iso(now - timedelta(hours=i + 1)),
                "end_measure": _iso(now - timedelta(hours=i)),
                "value": value,
                "unit": "µSv/h",
                "validated": 1,
                "nuclide": "Gamma-ODL-Brutto",
                "duration": "1h",
            },
        }
        for i in range(1, hours + 1)
    ]
    return collection(features)


def mock_latest_cql(
    aioclient_mock: AiohttpClientMocker, kenn: str, features: list[dict[str, Any]]
) -> None:
    """Antwort auf die CQL-gefilterte Abfrage einer Sonde."""
    aioclient_mock.get(
        WFS_URL,
        params={"typeName": LAYER_LATEST, "CQL_FILTER": f"kenn='{kenn}'"},
        text=collection(features),
    )


def mock_timeseries(
    aioclient_mock: AiohttpClientMocker,
    kenn: str,
    *,
    text: str | None = None,
    status: int = 200,
) -> None:
    """Antwort auf die Zeitreihenabfrage."""
    aioclient_mock.get(
        WFS_URL,
        params={"typeName": LAYER_TIMESERIES, "viewparams": f"kenn:{kenn}"},
        text=text if text is not None else collection([]),
        status=status,
    )


def mock_full_list(
    aioclient_mock: AiohttpClientMocker,
    features: list[dict[str, Any]] | None = None,
) -> None:
    """Antwort auf die ungefilterte Abfrage (muss zuletzt registriert werden)."""
    aioclient_mock.get(
        WFS_URL,
        params={"typeName": LAYER_LATEST},
        text=collection(features if features is not None else all_features()),
    )


def requests_with(aioclient_mock: AiohttpClientMocker, key: str) -> int:
    """Zählt die Abfragen, die einen bestimmten Parameter enthalten."""
    return sum(1 for _, url, _, _ in aioclient_mock.mock_calls if key in url.query)


def full_list_requests(aioclient_mock: AiohttpClientMocker) -> int:
    """Zählt die ungefilterten Abfragen des Latest-Layers."""
    return sum(
        1
        for _, url, _, _ in aioclient_mock.mock_calls
        if url.query.get("typeName") == LAYER_LATEST
        and "CQL_FILTER" not in url.query
        and "filter" not in url.query
    )


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Custom Integrations in allen Tests erlauben."""
    yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Ein Eintrag für die Sonde Wehrheim."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Wehrheim",
        unique_id=WEHRHEIM,
        data={CONF_KENN: WEHRHEIM, CONF_STATION_NAME: "Wehrheim"},
        options={
            CONF_SCAN_INTERVAL: 30,
            CONF_THRESHOLD: 0.3,
            CONF_PERCENT_ABOVE_MEAN: 50,
        },
    )


@pytest.fixture
def mock_setup_entry() -> Generator[None]:
    """Verhindert das echte Setup nach dem Config-Flow."""
    with patch("custom_components.bfs_odl.async_setup_entry", return_value=True):
        yield


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
