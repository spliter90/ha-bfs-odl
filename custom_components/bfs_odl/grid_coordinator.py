"""DataUpdateCoordinators für das Stromnetz (Energy-Charts, Fraunhofer ISE)."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .const import (
    CONF_FREQUENCY_INTERVAL,
    CONF_FREQUENCY_STALE_AFTER,
    CONF_POSTAL_CODE,
    CONF_POWER_INTERVAL,
    CONF_SIGNAL_INTERVAL,
    DEFAULT_FREQUENCY_INTERVAL,
    DEFAULT_FREQUENCY_STALE_AFTER,
    DEFAULT_POWER_INTERVAL,
    DEFAULT_SIGNAL_INTERVAL,
    DOMAIN,
    FREQUENCY_BUFFER,
    FREQUENCY_INITIAL_LOOKBACK,
    FREQUENCY_REPAIR_AFTER,
    FREQUENCY_STORE_SAVE_DELAY,
    FREQUENCY_WINDOW,
    GRID_FREQUENCY_STORAGE_KEY,
    ISSUE_FREQUENCY_MISSING,
    MAX_FREQUENCY_STALE_AFTER,
    MIN_FREQUENCY_INTERVAL,
    MIN_FREQUENCY_STALE_AFTER,
    MIN_POWER_INTERVAL,
    MIN_SIGNAL_INTERVAL,
    POWER_LOOKBACK,
    STORAGE_VERSION,
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

NO_FREQUENCY_DATA = (
    "Keine Frequenzwerte im abgefragten Zeitraum (HTTP 404 bzw. leere Antwort)"
)


@callback
def async_get_energy_charts_api(hass: HomeAssistant) -> EnergyChartsApi:
    """Gemeinsamer Client für alle Stromnetz-Coordinators."""
    if (api := hass.data.get(DATA_ENERGY_CHARTS_API)) is None:
        api = hass.data[DATA_ENERGY_CHARTS_API] = EnergyChartsApi(
            async_get_clientsession(hass)
        )
    return api


def frequency_store(hass: HomeAssistant) -> Store[dict[str, Any]]:
    """Speicher für den letzten Frequenzwert und den Ausfallzustand."""
    return Store(hass, STORAGE_VERSION, GRID_FREQUENCY_STORAGE_KEY)


def _option(entry: BfsOdlConfigEntry, key: str, default: int, minimum: int) -> int:
    return max(minimum, int(entry.options.get(key, default)))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed


def _format_local(value: datetime) -> str:
    """Zeitpunkt für Log und Repair-Issue, z. B. 05.11.2026 14:20."""
    return dt_util.as_local(value).strftime("%d.%m.%Y %H:%M")


def _frequency_to_dict(data: FrequencyData | None) -> dict[str, Any] | None:
    if data is None:
        return None
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in asdict(data).items()
    }


def _frequency_from_dict(value: Any) -> FrequencyData | None:
    if not isinstance(value, dict):
        return None
    time = _parse_dt(value.get("time"))
    window_start = _parse_dt(value.get("window_start"))
    try:
        frequency = float(value["frequency"])
        minimum = float(value["minimum"])
        maximum = float(value["maximum"])
        count = int(value["count"])
    except (KeyError, TypeError, ValueError):
        return None
    if time is None or window_start is None:
        return None
    return FrequencyData(
        time=time,
        frequency=frequency,
        minimum=minimum,
        maximum=maximum,
        count=count,
        window_start=window_start,
    )


class GridCoordinator[DataT](DataUpdateCoordinator[DataT]):
    """Gemeinsame Fehlerbehandlung und Statusverfolgung der Stromnetz-Coordinators."""

    config_entry: BfsOdlConfigEntry
    source: str = ""
    # True, wenn der Coordinator Erfolg und Fehler selbst verbucht
    _tracks_own_status: bool = False

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
        self.last_success_time: datetime | None = None
        self.last_error: str | None = None
        self.last_error_time: datetime | None = None

    @callback
    def _record_success(self) -> None:
        self.last_success_time = dt_util.utcnow()

    @callback
    def _record_error(self, error: str) -> None:
        self.last_error = error
        self.last_error_time = dt_util.utcnow()

    async def _async_update_data(self) -> DataT:
        try:
            data = await self._async_fetch()
        except EnergyChartsError as err:
            self._record_error(str(err))
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="grid_update_failed",
                translation_placeholders={"source": self.source, "error": str(err)},
            ) from err
        if not self._tracks_own_status:
            self._record_success()
        return data

    async def _async_fetch(self) -> DataT:
        raise NotImplementedError

    @property
    def has_data(self) -> bool:
        """Liegen verwertbare Daten vor?"""
        return self.last_update_success and self.data is not None

    def newest_data_point(self) -> datetime | None:
        """Zeitpunkt des neuesten Datenpunkts."""
        return None

    def status_diagnostics(self) -> dict[str, Any]:
        """Letzter erfolgreicher Abruf, letzter Fehler, neuester Datenpunkt."""
        return {
            "last_successful_fetch": _iso(self.last_success_time),
            "last_error": self.last_error,
            "last_error_time": _iso(self.last_error_time),
            "newest_data_point": _iso(self.newest_data_point()),
        }

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

    def newest_data_point(self) -> datetime | None:
        """Beginn des letzten Zeitfensters (Prognose)."""
        if self.data is None or not self.data.slots:
            return None
        return self.data.slots[-1].start


class FrequencyCoordinator(GridCoordinator[FrequencyData | None]):
    """Netzfrequenz, standardmäßig jede Minute; lädt nur neue Sekundenwerte.

    Energy-Charts liefert die Frequenz mit Verzug (rund 50 Minuten). Minimum und
    Maximum beziehen sich daher auf die letzte Stunde der vorhandenen Daten, also
    auf die Stunde vor dem neuesten Messwert, nicht auf die Stunde vor "jetzt".

    Fehlende Daten (HTTP 404, leere Antwort, Verbindungsfehler) führen nie zu
    UpdateFailed. Der Coordinator liefert den letzten bekannten Stand weiter
    (auch über einen Neustart hinweg); die Entitäten entscheiden über das
    Datenalter, ob sie verfügbar sind. Ist gar nichts bekannt, liefert er None.
    """

    source = "frequency"
    _tracks_own_status = True

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
        minutes = min(
            MAX_FREQUENCY_STALE_AFTER,
            _option(
                entry,
                CONF_FREQUENCY_STALE_AFTER,
                DEFAULT_FREQUENCY_STALE_AFTER,
                MIN_FREQUENCY_STALE_AFTER,
            ),
        )
        self.stale_after = timedelta(minutes=minutes)
        self._buffer: deque[tuple[int, float]] = deque()
        self._store = frequency_store(hass)
        # Letzter bekannter Stand, auch aus der Zeit vor einem Neustart
        self._latest: FrequencyData | None = None
        self.last_request_points = 0
        self.last_request_start: datetime | None = None
        # Ausfall = keine verwertbaren Daten (keine oder älter als die Stale-Grenze)
        self.outage = False
        self.outage_detected_at: datetime | None = None
        self.issue_active = False
        self._issue_checked = False

    @property
    def initial_lookback(self) -> timedelta:
        """Zeitraum für den ersten Abruf.

        Mindestens 3 Stunden, und so weit, dass auch bei einem Verzug bis zur
        Stale-Grenze noch eine volle Stunde Daten im Fenster liegt.
        """
        return max(FREQUENCY_INITIAL_LOOKBACK, self.stale_after + FREQUENCY_WINDOW)

    def is_stale(self, data: FrequencyData) -> bool:
        """Ist der neueste Messwert älter als die Stale-Grenze?"""
        return dt_util.utcnow() - data.time > self.stale_after

    def newest_data_point(self) -> datetime | None:
        """Zeitpunkt des neuesten Messwerts."""
        return self._latest.time if self._latest is not None else None

    def missing_since(self) -> datetime | None:
        """Seit wann fehlen verwertbare Daten?

        Bekannt ist der letzte Messwert, dann gilt dessen Zeitpunkt. Sonst der
        Zeitpunkt, an dem der Ausfall bemerkt wurde.
        """
        if not self.outage:
            return None
        if self._latest is not None:
            return self._latest.time
        return self.outage_detected_at

    async def _async_setup(self) -> None:
        """Stellt den letzten Stand und den Ausfallzustand wieder her."""
        stored = await self._store.async_load()
        if not isinstance(stored, dict):
            return
        self._latest = _frequency_from_dict(stored.get("latest"))
        self.outage = bool(stored.get("outage"))
        self.outage_detected_at = _parse_dt(stored.get("outage_detected_at"))
        if self.outage and self.outage_detected_at is None:
            self.outage_detected_at = dt_util.utcnow()

    async def _async_fetch(self) -> FrequencyData | None:
        now = dt_util.utcnow()
        if self._buffer:
            # Nur neue Werte seit dem letzten Abruf
            start = dt_util.utc_from_timestamp(self._buffer[-1][0] + 1)
        else:
            # Erster Abruf bzw. nach einem Neustart
            start = now - self.initial_lookback

        new_points = 0
        if start < now:
            self.last_request_start = start
            try:
                points = await self.api.async_get_frequency(start, now)
            except EnergyChartsError as err:
                # Kein UpdateFailed: Die Entitäten bleiben gültig, solange die
                # vorhandenen Daten jünger als die Stale-Grenze sind.
                _LOGGER.debug("Frequenz-Abruf fehlgeschlagen: %s", err)
                self._record_error(str(err))
                points = []
            else:
                if not points:
                    self._record_error(NO_FREQUENCY_DATA)
            self.last_request_points = len(points)
            last = self._buffer[-1][0] if self._buffer else -1
            for timestamp, value in sorted(points):
                if timestamp > last:
                    self._buffer.append((timestamp, value))
                    last = timestamp
                    new_points += 1

        if new_points:
            self._record_success()
        if self._buffer:
            self._latest = self._compute()

        changed = self._async_track_outage()
        if new_points or changed:
            self._store.async_delay_save(
                self._data_to_store, FREQUENCY_STORE_SAVE_DELAY
            )
        return self._latest

    def _compute(self) -> FrequencyData:
        latest_time, latest_value = self._buffer[-1]
        buffer_start = latest_time - int(FREQUENCY_BUFFER.total_seconds())
        while self._buffer and self._buffer[0][0] < buffer_start:
            self._buffer.popleft()

        window_start = latest_time - int(FREQUENCY_WINDOW.total_seconds())
        window = [
            value for timestamp, value in self._buffer if timestamp >= window_start
        ]
        first_in_window = next(t for t, _ in self._buffer if t >= window_start)
        return FrequencyData(
            time=dt_util.utc_from_timestamp(latest_time),
            frequency=latest_value,
            minimum=min(window),
            maximum=max(window),
            count=len(window),
            window_start=dt_util.utc_from_timestamp(first_in_window),
        )

    @callback
    def _async_track_outage(self) -> bool:
        """Ausfallzustand, Log-Meldungen (einmal pro Ausfall) und Repair-Issue.

        Gibt True zurück, wenn sich der Ausfallzustand geändert hat.
        """
        data = self._latest
        usable = data is not None and not self.is_stale(data)
        changed = False

        if usable and self.outage:
            assert data is not None
            _LOGGER.info(
                "Energy-Charts liefert wieder Netzfrequenz-Daten (neuester Messwert %s)",
                _format_local(data.time),
            )
            self.outage = False
            self.outage_detected_at = None
            changed = True
        elif not usable and not self.outage:
            self.outage = True
            self.outage_detected_at = dt_util.utcnow()
            changed = True
            if data is None:
                _LOGGER.warning(
                    "Energy-Charts liefert keine Netzfrequenz-Daten (%s). Die "
                    "Frequenz-Entitäten sind nicht verfügbar, bis wieder Daten "
                    "kommen; Netzampel und Erzeugung sind nicht betroffen",
                    self.last_error or NO_FREQUENCY_DATA,
                )
            else:
                _LOGGER.warning(
                    "Energy-Charts liefert seit %s keine neuen Netzfrequenz-Daten "
                    "(Stale-Grenze %d min überschritten). Die Frequenz-Entitäten "
                    "sind nicht verfügbar, bis wieder Daten kommen; Netzampel und "
                    "Erzeugung sind nicht betroffen",
                    _format_local(data.time),
                    int(self.stale_after.total_seconds() // 60),
                )

        self._async_update_issue()
        return changed

    @callback
    def _async_update_issue(self) -> None:
        """Repair-Issue anlegen, wenn Frequenzdaten länger als 6 h fehlen."""
        since = self.missing_since()
        if since is not None and dt_util.utcnow() - since > FREQUENCY_REPAIR_AFTER:
            if not self.issue_active:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    ISSUE_FREQUENCY_MISSING,
                    is_fixable=False,
                    is_persistent=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_FREQUENCY_MISSING,
                    translation_placeholders={
                        "since": _format_local(since),
                        "last_measurement": (
                            _format_local(self._latest.time)
                            if self._latest is not None
                            else "–"
                        ),
                    },
                )
                self.issue_active = True
            return
        if self.issue_active or not self._issue_checked:
            # Auch ein Issue aus der Zeit vor einem Neustart entfernen
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_FREQUENCY_MISSING)
            self.issue_active = False
        self._issue_checked = True

    @callback
    def _data_to_store(self) -> dict[str, Any]:
        return {
            "latest": _frequency_to_dict(self._latest),
            "outage": self.outage,
            "outage_detected_at": _iso(self.outage_detected_at),
        }

    async def async_shutdown(self) -> None:
        """Speichert den letzten Stand beim Entladen sofort."""
        await super().async_shutdown()
        await self._store.async_save(self._data_to_store())

    def diagnostics(self) -> dict[str, Any]:
        """Puffer, Datenalter, Stale-Grenze und Ausfallzustand."""
        latest = self.newest_data_point()
        return {
            "buffer_size": len(self._buffer),
            "buffer_start": (
                dt_util.utc_from_timestamp(self._buffer[0][0]).isoformat()
                if self._buffer
                else None
            ),
            "buffer_end": (
                dt_util.utc_from_timestamp(self._buffer[-1][0]).isoformat()
                if self._buffer
                else None
            ),
            "data_age_minutes": (
                round((dt_util.utcnow() - latest).total_seconds() / 60, 1)
                if latest
                else None
            ),
            "stale_after_minutes": self.stale_after.total_seconds() / 60,
            "initial_lookback_hours": self.initial_lookback.total_seconds() / 3600,
            "last_request_start": _iso(self.last_request_start),
            "last_request_points": self.last_request_points,
            "outage": self.outage,
            "outage_detected_at": _iso(self.outage_detected_at),
            "missing_since": _iso(self.missing_since()),
            "repair_issue_active": self.issue_active,
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

    def newest_data_point(self) -> datetime | None:
        """Jüngster Zeitpunkt, für den eine Größe einen Wert hat."""
        if self.data is None or not self.data.values:
            return None
        return max(value.time for value in self.data.values.values())

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
