"""Asynchroner Client für die ODL-Daten des Bundesamts für Strahlenschutz.

Abrufstrategie für eine einzelne Sonde (wird beim ersten Abruf ermittelt und gemerkt):

1. WFS-Abfrage mit ``CQL_FILTER=kenn='<kenn>'`` (GeoServer-Erweiterung).
2. Falls der Server das ablehnt: WFS-Abfrage mit standardisiertem OGC-XML-Filter
   (Parameter ``filter`` mit ``PropertyIsEqualTo`` auf ``kenn``), wie in der BfS-Doku.
3. Falls auch das nicht klappt: komplette Sondenliste laden (zwischengespeichert)
   und lokal filtern.

Netzwerkfehler führen nie dazu, dass eine Methode als "nicht unterstützt" markiert wird.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import aiohttp
from homeassistant.util import dt as dt_util

from .const import (
    LAYER_LATEST,
    LAYER_TIMESERIES,
    REQUEST_TIMEOUT,
    SITE_STATUS_MAP,
    STATION_LIST_CACHE_TTL,
    TIMESERIES_MAX_FEATURES,
    WFS_URL,
)

_LOGGER = logging.getLogger(__name__)

_KENN_PATTERN = re.compile(r"^[0-9A-Za-z]{1,20}$")

type FilterMode = Literal["cql", "ogc"]


class BfsOdlApiError(Exception):
    """Basisklasse für Fehler beim Abruf."""


class BfsOdlConnectionError(BfsOdlApiError):
    """Server nicht erreichbar, Timeout oder Serverfehler (5xx)."""


class BfsOdlRequestError(BfsOdlApiError):
    """Der Server hat die Anfrage abgelehnt oder ungültige Daten geliefert."""


class BfsOdlStationNotFoundError(BfsOdlApiError):
    """Die angefragte Sonde existiert nicht (mehr) im Datensatz."""


def _to_float(value: Any) -> float | None:
    """Wandelt einen Wert robust in float um."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    """Wandelt einen Wert robust in int um."""
    number = _to_float(value)
    return None if number is None else int(number)


def _to_datetime(value: Any) -> datetime | None:
    """Wandelt einen ISO-Zeitstempel in ein zeitzonenbehaftetes datetime um."""
    if not isinstance(value, str) or not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _to_validated(value: Any) -> bool | None:
    """Interpretiert das Feld ``validated`` (1 = validiert, sonst nicht)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().casefold()
        if text in ("true", "ja", "yes"):
            return True
        if text in ("false", "nein", "no"):
            return False
    return _to_int(value) == 1


@dataclass(frozen=True, slots=True)
class OdlStation:
    """Eine ODL-Messsonde mit ihrem aktuellen 1-Stunden-Wert."""

    kenn: str
    name: str
    plz: str | None
    latitude: float | None
    longitude: float | None
    height_above_sea: float | None
    site_status: int | None
    site_status_text: str | None
    start_measure: datetime | None
    end_measure: datetime | None
    value: float | None
    value_cosmic: float | None
    value_terrestrial: float | None
    unit: str | None
    validated: bool | None
    nuclide: str | None
    duration: str | None

    @property
    def status(self) -> str | None:
        """Status als Schlüssel (in_operation, defective, test_operation)."""
        if self.site_status is None:
            return None
        return SITE_STATUS_MAP.get(self.site_status)

    @classmethod
    def from_feature(cls, feature: dict[str, Any]) -> OdlStation | None:
        """Erzeugt eine Sonde aus einem GeoJSON-Feature."""
        props = feature.get("properties")
        if not isinstance(props, dict):
            return None
        kenn = props.get("kenn")
        if kenn is None or str(kenn).strip() == "":
            return None

        latitude: float | None = None
        longitude: float | None = None
        geometry = feature.get("geometry")
        if isinstance(geometry, dict):
            coords = geometry.get("coordinates")
            if isinstance(coords, list | tuple) and len(coords) >= 2:
                longitude = _to_float(coords[0])
                latitude = _to_float(coords[1])

        plz = props.get("plz")
        nuclide = props.get("nuclide")
        duration = props.get("duration")
        unit = props.get("unit")
        status_text = props.get("site_status_text")
        return cls(
            kenn=str(kenn).strip(),
            name=str(props.get("name") or kenn).strip(),
            plz=str(plz) if plz not in (None, "") else None,
            latitude=latitude,
            longitude=longitude,
            height_above_sea=_to_float(props.get("height_above_sea")),
            site_status=_to_int(props.get("site_status")),
            site_status_text=str(status_text) if status_text else None,
            start_measure=_to_datetime(props.get("start_measure")),
            end_measure=_to_datetime(props.get("end_measure")),
            value=_to_float(props.get("value")),
            value_cosmic=_to_float(props.get("value_cosmic")),
            value_terrestrial=_to_float(props.get("value_terrestrial")),
            unit=str(unit) if unit else None,
            validated=_to_validated(props.get("validated")),
            nuclide=str(nuclide) if nuclide else None,
            duration=str(duration) if duration else None,
        )


def _ogc_filter(kenn: str) -> str:
    """Standardisierter OGC-Filter (Filter Encoding 1.1) auf die Kennung."""
    return (
        '<Filter xmlns="http://www.opengis.net/ogc">'
        "<PropertyIsEqualTo>"
        "<PropertyName>kenn</PropertyName>"
        f"<Literal>{kenn}</Literal>"
        "</PropertyIsEqualTo>"
        "</Filter>"
    )


class BfsOdlApi:
    """Client für den WFS-Dienst des BfS (eine Instanz für alle Einträge)."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialisiert den Client."""
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        self._list_lock = asyncio.Lock()
        self._station_cache: dict[str, OdlStation] | None = None
        self._station_cache_time: datetime | None = None
        self._unsupported_modes: set[FilterMode] = set()
        self.full_list_requests = 0

    @property
    def filter_mode(self) -> str:
        """Aktuell bevorzugte Abrufmethode (für Diagnose)."""
        for mode in ("cql", "ogc"):
            if mode not in self._unsupported_modes:
                return mode
        return "list"

    def diagnostics(self) -> dict[str, Any]:
        """Interner Zustand für die Diagnose."""
        return {
            "filter_mode": self.filter_mode,
            "unsupported_modes": sorted(self._unsupported_modes),
            "station_cache_size": len(self._station_cache or {}),
            "station_cache_time": (
                self._station_cache_time.isoformat()
                if self._station_cache_time
                else None
            ),
            "full_list_requests": self.full_list_requests,
        }

    @staticmethod
    def _base_params(type_name: str) -> dict[str, str]:
        return {
            "service": "WFS",
            "version": "1.1.0",
            "request": "GetFeature",
            "typeName": type_name,
            "outputFormat": "application/json",
        }

    async def _async_get_features(self, params: dict[str, str]) -> list[dict[str, Any]]:
        """Führt eine GetFeature-Abfrage aus und liefert die Features."""
        try:
            async with self._session.get(
                WFS_URL, params=params, timeout=self._timeout
            ) as response:
                text = await response.text()
                status = response.status
        except TimeoutError as err:
            raise BfsOdlConnectionError("Zeitüberschreitung beim Abruf") from err
        except aiohttp.ClientError as err:
            raise BfsOdlConnectionError(f"Verbindungsfehler: {err}") from err

        if status >= 500:
            raise BfsOdlConnectionError(f"Serverfehler HTTP {status}")
        if status >= 400:
            raise BfsOdlRequestError(f"Anfrage abgelehnt: HTTP {status}")

        try:
            payload = json.loads(text)
        except ValueError as err:
            # GeoServer antwortet bei ungültigen Parametern mit XML (ExceptionReport)
            raise BfsOdlRequestError(
                f"Keine gültige JSON-Antwort: {text[:200]!r}"
            ) from err

        if not isinstance(payload, dict) or not isinstance(
            payload.get("features"), list
        ):
            raise BfsOdlRequestError("Antwort ist keine GeoJSON FeatureCollection")
        return [f for f in payload["features"] if isinstance(f, dict)]

    @staticmethod
    def _parse(features: list[dict[str, Any]]) -> list[OdlStation]:
        stations: list[OdlStation] = []
        for feature in features:
            station = OdlStation.from_feature(feature)
            if station is not None:
                stations.append(station)
        return stations

    def _store_cache(self, stations: list[OdlStation]) -> None:
        self._station_cache = {station.kenn: station for station in stations}
        self._station_cache_time = dt_util.utcnow()

    def _cache_valid(self) -> bool:
        return (
            self._station_cache is not None
            and self._station_cache_time is not None
            and dt_util.utcnow() - self._station_cache_time < STATION_LIST_CACHE_TTL
        )

    async def async_get_all_stations(self, *, force: bool = False) -> list[OdlStation]:
        """Liefert alle Sonden; die Liste wird zwischengespeichert."""
        async with self._list_lock:
            if force or not self._cache_valid():
                features = await self._async_get_features(
                    self._base_params(LAYER_LATEST)
                )
                self.full_list_requests += 1
                stations = self._parse(features)
                if not stations:
                    raise BfsOdlRequestError("Die Sondenliste ist leer")
                self._store_cache(stations)
            assert self._station_cache is not None
            return list(self._station_cache.values())

    def _filter_params(self, mode: FilterMode, kenn: str) -> dict[str, str]:
        params = self._base_params(LAYER_LATEST)
        if mode == "cql":
            params["CQL_FILTER"] = f"kenn='{kenn}'"
        else:
            params["filter"] = _ogc_filter(kenn)
        return params

    async def async_get_station(self, kenn: str) -> OdlStation:
        """Liefert den aktuellen Wert einer einzelnen Sonde."""
        if not _KENN_PATTERN.match(kenn):
            raise BfsOdlStationNotFoundError(f"Ungültige Kennung: {kenn!r}")

        for mode in ("cql", "ogc"):
            if mode in self._unsupported_modes:
                continue
            try:
                features = await self._async_get_features(
                    self._filter_params(mode, kenn)
                )
            except BfsOdlRequestError as err:
                _LOGGER.debug("Filtermethode %s wird nicht unterstützt: %s", mode, err)
                self._unsupported_modes.add(mode)
                continue

            stations = self._parse(features)
            matching = [s for s in stations if s.kenn == kenn]

            if len(stations) > len(matching):
                # Der Filter wurde ignoriert und wir haben (fast) die ganze Liste
                _LOGGER.debug("Filtermethode %s wird vom Server ignoriert", mode)
                self._unsupported_modes.add(mode)
                if len(stations) > 1:
                    self._store_cache(stations)
                if matching:
                    return matching[0]
                raise BfsOdlStationNotFoundError(f"Sonde {kenn} nicht gefunden")

            if matching:
                return matching[0]

            # Leeres Ergebnis: mit der Gesamtliste gegenprüfen
            all_stations = await self.async_get_all_stations()
            for station in all_stations:
                if station.kenn == kenn:
                    _LOGGER.debug(
                        "Filtermethode %s liefert unzuverlässige Ergebnisse", mode
                    )
                    self._unsupported_modes.add(mode)
                    return station
            raise BfsOdlStationNotFoundError(f"Sonde {kenn} nicht gefunden")

        # Kein Filter funktioniert: Gesamtliste (zwischengespeichert) lokal filtern
        for station in await self.async_get_all_stations():
            if station.kenn == kenn:
                return station
        raise BfsOdlStationNotFoundError(f"Sonde {kenn} nicht gefunden")

    async def async_get_timeseries(self, kenn: str) -> list[tuple[datetime, float]]:
        """Liefert die Stundenwerte der letzten Tage (neueste zuerst)."""
        if not _KENN_PATTERN.match(kenn):
            raise BfsOdlStationNotFoundError(f"Ungültige Kennung: {kenn!r}")
        params = self._base_params(LAYER_TIMESERIES)
        params["viewparams"] = f"kenn:{kenn}"
        params["sortBy"] = "end_measure D"
        params["maxFeatures"] = str(TIMESERIES_MAX_FEATURES)
        features = await self._async_get_features(params)

        points: list[tuple[datetime, float]] = []
        for feature in features:
            props = feature.get("properties")
            if not isinstance(props, dict):
                continue
            feature_kenn = props.get("kenn")
            if feature_kenn is not None and str(feature_kenn).strip() != kenn:
                continue
            end = _to_datetime(props.get("end_measure"))
            value = _to_float(props.get("value"))
            if end is not None and value is not None:
                points.append((end, value))
        return points
