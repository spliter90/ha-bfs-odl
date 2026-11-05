"""DataUpdateCoordinator für eine ODL-Messsonde."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .api import (
    BfsOdlApi,
    BfsOdlApiError,
    BfsOdlStationNotFoundError,
    OdlStation,
)
from .const import (
    CONF_KENN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MEAN_MIN_VALUES,
    MEAN_WINDOW,
    MIN_SCAN_INTERVAL,
    STORAGE_KEY_PREFIX,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
    TIMESERIES_REFRESH_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

DATA_API: HassKey[BfsOdlApi] = HassKey(DOMAIN)

type BfsOdlConfigEntry = ConfigEntry[BfsOdlCoordinator]


@callback
def async_get_api(hass: HomeAssistant) -> BfsOdlApi:
    """Gemeinsamer API-Client für alle Einträge (teilt sich den Listen-Cache)."""
    if (api := hass.data.get(DATA_API)) is None:
        api = hass.data[DATA_API] = BfsOdlApi(async_get_clientsession(hass))
    return api


def history_store(hass: HomeAssistant, kenn: str) -> Store[dict[str, Any]]:
    """Persistenter Speicher für den Werteverlauf einer Sonde."""
    return Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}.{kenn}")


@dataclass(frozen=True, slots=True)
class BfsOdlData:
    """Daten, die der Coordinator den Entitäten bereitstellt."""

    station: OdlStation
    mean_7d: float | None
    mean_7d_count: int


class BfsOdlCoordinator(DataUpdateCoordinator[BfsOdlData]):
    """Ruft die Werte einer Sonde ab und führt das 7-Tage-Mittel."""

    config_entry: BfsOdlConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: BfsOdlConfigEntry, api: BfsOdlApi
    ) -> None:
        """Initialisiert den Coordinator."""
        minutes = max(
            MIN_SCAN_INTERVAL,
            int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
        )
        self.kenn: str = entry.data[CONF_KENN]
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{self.kenn}",
            update_interval=timedelta(minutes=minutes),
        )
        self.api = api
        self._store = history_store(hass, self.kenn)
        self._history: dict[datetime, float] = {}
        self._timeseries_last_attempt: datetime | None = None
        self.timeseries_available: bool | None = None

    @property
    def history(self) -> dict[datetime, float]:
        """Gespeicherte Stundenwerte (Ende der Messung -> Wert)."""
        return self._history

    async def _async_setup(self) -> None:
        """Lädt den gespeicherten Verlauf (einmalig vor dem ersten Abruf)."""
        stored = await self._store.async_load()
        if not stored:
            return
        for key, value in stored.get("history", {}).items():
            timestamp = dt_util.parse_datetime(key)
            if timestamp is None or timestamp.tzinfo is None:
                continue
            try:
                self._history[timestamp] = float(value)
            except (TypeError, ValueError):
                continue
        self._prune_history()

    async def _async_update_data(self) -> BfsOdlData:
        """Holt den aktuellen Wert der Sonde."""
        try:
            station = await self.api.async_get_station(self.kenn)
        except BfsOdlStationNotFoundError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="station_not_found",
                translation_placeholders={"kenn": self.kenn},
            ) from err
        except BfsOdlApiError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        await self._async_maybe_load_timeseries()

        if station.value is not None and station.end_measure is not None:
            self._history[station.end_measure] = station.value
        self._prune_history()
        self._store.async_delay_save(self._data_to_store, STORAGE_SAVE_DELAY)

        mean, count = self._compute_mean(exclude=station.end_measure)
        return BfsOdlData(station=station, mean_7d=mean, mean_7d_count=count)

    async def _async_maybe_load_timeseries(self) -> None:
        """Ergänzt den Verlauf aus der BfS-Zeitreihe (höchstens alle 6 Stunden)."""
        now = dt_util.utcnow()
        if (
            self._timeseries_last_attempt is not None
            and now - self._timeseries_last_attempt < TIMESERIES_REFRESH_INTERVAL
        ):
            return
        self._timeseries_last_attempt = now
        try:
            points = await self.api.async_get_timeseries(self.kenn)
        except BfsOdlApiError as err:
            if self.timeseries_available is not False:
                _LOGGER.info(
                    "Zeitreihe für Sonde %s nicht verfügbar (%s); das 7-Tage-Mittel "
                    "wird aus den eigenen Abrufen gebildet",
                    self.kenn,
                    err,
                )
            self.timeseries_available = False
            return
        self.timeseries_available = bool(points)
        for timestamp, value in points:
            self._history[timestamp] = value

    def _prune_history(self) -> None:
        cutoff = dt_util.utcnow() - MEAN_WINDOW - timedelta(hours=1)
        for timestamp in [t for t in self._history if t < cutoff]:
            del self._history[timestamp]

    def _compute_mean(self, exclude: datetime | None) -> tuple[float | None, int]:
        """Gleitendes 7-Tage-Mittel ohne den aktuellen Messwert."""
        cutoff = dt_util.utcnow() - MEAN_WINDOW
        values = [
            value
            for timestamp, value in self._history.items()
            if timestamp >= cutoff and timestamp != exclude
        ]
        if len(values) < MEAN_MIN_VALUES:
            return None, len(values)
        return sum(values) / len(values), len(values)

    @callback
    def _data_to_store(self) -> dict[str, Any]:
        return {
            "history": {
                timestamp.isoformat(): value
                for timestamp, value in sorted(self._history.items())
            }
        }

    async def async_shutdown(self) -> None:
        """Speichert den Verlauf beim Entladen sofort."""
        await super().async_shutdown()
        await self._store.async_save(self._data_to_store())
