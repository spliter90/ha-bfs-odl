"""DataUpdateCoordinators für das Stromnetz (Energy-Charts, Fraunhofer ISE)."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .const import (
    CONF_FREQUENCY_INTERVAL,
    CONF_POSTAL_CODE,
    CONF_POWER_INTERVAL,
    CONF_SIGNAL_INTERVAL,
    DEFAULT_FREQUENCY_INTERVAL,
    DEFAULT_POWER_INTERVAL,
    DEFAULT_SIGNAL_INTERVAL,
    DOMAIN,
    FREQUENCY_WINDOW,
    MIN_FREQUENCY_INTERVAL,
    MIN_POWER_INTERVAL,
    MIN_SIGNAL_INTERVAL,
    POWER_LOOKBACK,
)
from .coordinator import BfsOdlConfigEntry
from .energy_charts import (
    EnergyChartsApi,
    EnergyChartsConnectionError,
    EnergyChartsError,
    EnergyChartsNoDataError,
    FrequencyData,
    PowerData,
    SignalData,
    parse_power,
    parse_signal,
)

_LOGGER = logging.getLogger(__name__)

DATA_ENERGY_CHARTS_API: HassKey[EnergyChartsApi] = HassKey(f"{DOMAIN}_energy_charts")

POWER_ENDPOINTS = ("total_power", "public_power")


@callback
def async_get_energy_charts_api(hass: HomeAssistant) -> EnergyChartsApi:
    """Gemeinsamer Client für alle Stromnetz-Coordinators."""
    if (api := hass.data.get(DATA_ENERGY_CHARTS_API)) is None:
        api = hass.data[DATA_ENERGY_CHARTS_API] = EnergyChartsApi(
            async_get_clientsession(hass)
        )
    return api


def _option(entry: BfsOdlConfigEntry, key: str, default: int, minimum: int) -> int:
    return max(minimum, int(entry.options.get(key, default)))


class GridCoordinator[DataT](DataUpdateCoordinator[DataT]):
    """Gemeinsame Fehlerbehandlung der Stromnetz-Coordinators."""

    config_entry: BfsOdlConfigEntry
    source: str = ""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BfsOdlConfigEntry,
        api: EnergyChartsApi,
        update_interval: timedelta,
    ) -> None:
        """Initialisiert den Coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_grid_{self.source}",
            update_interval=update_interval,
        )
        self.api = api

    async def _async_update_data(self) -> DataT:
        try:
            return await self._async_fetch()
        except EnergyChartsError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="grid_update_failed",
                translation_placeholders={"source": self.source, "error": str(err)},
            ) from err

    async def _async_fetch(self) -> DataT:
        raise NotImplementedError

    def diagnostics(self) -> dict[str, Any]:
        """Zusätzliche Diagnose-Daten."""
        return {}


class SignalCoordinator(GridCoordinator[SignalData]):
    """Netzampel, standardmäßig alle 15 Minuten."""

    source = "signal"

    def __init__(
        self, hass: HomeAssistant, entry: BfsOdlConfigEntry, api: EnergyChartsApi
    ) -> None:
        """Initialisiert den Coordinator."""
        minutes = _option(
            entry, CONF_SIGNAL_INTERVAL, DEFAULT_SIGNAL_INTERVAL, MIN_SIGNAL_INTERVAL
        )
        super().__init__(hass, entry, api, timedelta(minutes=minutes))

    async def _async_fetch(self) -> SignalData:
        entry = self.config_entry
        postal_code = entry.options.get(CONF_POSTAL_CODE) or entry.data.get(
            CONF_POSTAL_CODE
        )
        data = parse_signal(await self.api.async_get_signal(postal_code))
        if not data.slots:
            raise EnergyChartsNoDataError("Die Netzampel enthält keine Zeitfenster")
        return data


class FrequencyCoordinator(GridCoordinator[FrequencyData]):
    """Netzfrequenz, standardmäßig jede Minute; lädt nur neue Sekundenwerte."""

    source = "frequency"

    def __init__(
        self, hass: HomeAssistant, entry: BfsOdlConfigEntry, api: EnergyChartsApi
    ) -> None:
        """Initialisiert den Coordinator."""
        seconds = _option(
            entry,
            CONF_FREQUENCY_INTERVAL,
            DEFAULT_FREQUENCY_INTERVAL,
            MIN_FREQUENCY_INTERVAL,
        )
        super().__init__(hass, entry, api, timedelta(seconds=seconds))
        self._buffer: deque[tuple[int, float]] = deque()
        self.last_request_points = 0

    async def _async_fetch(self) -> FrequencyData:
        now = dt_util.utcnow()
        window_start = int((now - FREQUENCY_WINDOW).timestamp())
        start = window_start
        if self._buffer:
            start = max(window_start, self._buffer[-1][0] + 1)

        if start < int(now.timestamp()):
            points = await self.api.async_get_frequency(
                dt_util.utc_from_timestamp(start), now
            )
            self.last_request_points = len(points)
            last = self._buffer[-1][0] if self._buffer else -1
            for timestamp, value in sorted(points):
                if timestamp > last:
                    self._buffer.append((timestamp, value))
                    last = timestamp

        while self._buffer and self._buffer[0][0] < window_start:
            self._buffer.popleft()
        if not self._buffer:
            raise EnergyChartsNoDataError("Keine Frequenzwerte in der letzten Stunde")

        values = [value for _, value in self._buffer]
        latest_time, latest_value = self._buffer[-1]
        return FrequencyData(
            time=dt_util.utc_from_timestamp(latest_time),
            frequency=latest_value,
            minimum=min(values),
            maximum=max(values),
            count=len(values),
        )

    def diagnostics(self) -> dict[str, Any]:
        """Füllstand des Puffers."""
        return {
            "buffer_size": len(self._buffer),
            "buffer_start": (
                dt_util.utc_from_timestamp(self._buffer[0][0]).isoformat()
                if self._buffer
                else None
            ),
            "last_request_points": self.last_request_points,
        }


class PowerCoordinator(GridCoordinator[PowerData]):
    """Last und Erzeugung, standardmäßig alle 15 Minuten."""

    source = "power"

    def __init__(
        self, hass: HomeAssistant, entry: BfsOdlConfigEntry, api: EnergyChartsApi
    ) -> None:
        """Initialisiert den Coordinator."""
        minutes = _option(
            entry, CONF_POWER_INTERVAL, DEFAULT_POWER_INTERVAL, MIN_POWER_INTERVAL
        )
        super().__init__(hass, entry, api, timedelta(minutes=minutes))
        self.endpoint = POWER_ENDPOINTS[0]

    async def _async_fetch(self) -> PowerData:
        now = dt_util.utcnow()
        start = now - POWER_LOOKBACK
        # Bevorzugt total_power; liefert das nichts Brauchbares, public_power
        endpoints = [self.endpoint] + [e for e in POWER_ENDPOINTS if e != self.endpoint]
        last_error: EnergyChartsError | None = None
        for endpoint in endpoints:
            try:
                data = parse_power(await self.api.async_get_power(endpoint, start, now))
            except EnergyChartsConnectionError:
                raise
            except EnergyChartsError as err:
                _LOGGER.debug("Endpunkt /%s unbrauchbar: %s", endpoint, err)
                last_error = err
                continue
            if not data.values:
                last_error = EnergyChartsNoDataError(f"/{endpoint} ohne Werte")
                continue
            if endpoint != self.endpoint:
                _LOGGER.info("Erzeugungsdaten werden jetzt über /%s geladen", endpoint)
                self.endpoint = endpoint
            return data
        assert last_error is not None
        raise last_error

    def diagnostics(self) -> dict[str, Any]:
        """Verwendeter Endpunkt und gelieferte Produktionsarten."""
        return {
            "endpoint": self.endpoint,
            "production_types": list(self.data.names) if self.data else [],
        }


@dataclass(slots=True)
class GridRuntimeData:
    """Laufzeitdaten eines Stromnetz-Eintrags."""

    signal: SignalCoordinator | None = None
    frequency: FrequencyCoordinator | None = None
    power: PowerCoordinator | None = None

    @property
    def coordinators(self) -> list[GridCoordinator[Any]]:
        """Alle aktiven Coordinators."""
        return [c for c in (self.signal, self.frequency, self.power) if c is not None]
