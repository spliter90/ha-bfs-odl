"""Asynchroner Client und Auswertung für die Energy-Charts-API (Fraunhofer ISE).

Doku: https://api.energy-charts.info, Lizenz der Daten: CC BY 4.0.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp

from .const import (
    ENERGY_CHARTS_URL,
    FREQUENCY_REGION,
    GRID_COUNTRY,
    REQUEST_TIMEOUT,
    SIGNAL_BEST,
    SIGNAL_MAP,
)

_LOGGER = logging.getLogger(__name__)


class EnergyChartsError(Exception):
    """Basisklasse für Fehler beim Abruf."""


class EnergyChartsConnectionError(EnergyChartsError):
    """Server nicht erreichbar, Timeout oder Serverfehler."""


class EnergyChartsResponseError(EnergyChartsError):
    """Ungültige oder unerwartete Antwort."""


class EnergyChartsNoDataError(EnergyChartsError):
    """Für den angefragten Zeitraum liegen (noch) keine Daten vor (HTTP 404)."""


def _ts(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(value, tz=UTC)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class EnergyChartsApi:
    """Client für api.energy-charts.info (eine Instanz für alle Coordinators)."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialisiert den Client."""
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

    async def _async_get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            async with self._session.get(
                f"{ENERGY_CHARTS_URL}/{path}", params=params, timeout=self._timeout
            ) as response:
                status = response.status
                text = await response.text()
        except TimeoutError as err:
            raise EnergyChartsConnectionError("Zeitüberschreitung beim Abruf") from err
        except aiohttp.ClientError as err:
            raise EnergyChartsConnectionError(f"Verbindungsfehler: {err}") from err

        if status == 404:
            raise EnergyChartsNoDataError(f"Keine Daten für {path}")
        if status >= 500 or status == 429:
            raise EnergyChartsConnectionError(f"HTTP {status} bei {path}")
        if status >= 400:
            raise EnergyChartsResponseError(f"HTTP {status} bei {path}: {text[:200]}")
        try:
            payload = json.loads(text)
        except ValueError as err:
            raise EnergyChartsResponseError(
                f"Keine gültige JSON-Antwort von {path}"
            ) from err
        if not isinstance(payload, dict):
            raise EnergyChartsResponseError(f"Unerwartete Antwort von {path}")
        if payload.get("deprecated"):
            _LOGGER.warning(
                "Energy-Charts meldet den Endpunkt /%s als veraltet (deprecated)", path
            )
        return payload

    async def async_get_signal(self, postal_code: str | None) -> dict[str, Any]:
        """Netzampel (Anteil Erneuerbare und Signal je Viertelstunde)."""
        params = {"country": GRID_COUNTRY}
        if postal_code:
            params["postal_code"] = postal_code
        return await self._async_get("signal", params)

    async def async_get_frequency(
        self, start: datetime, end: datetime
    ) -> list[tuple[int, float]]:
        """Netzfrequenz in Sekundenauflösung für [start, end]."""
        try:
            payload = await self._async_get(
                "frequency",
                {
                    "region": FREQUENCY_REGION,
                    "start": str(int(start.timestamp())),
                    "end": str(int(end.timestamp())),
                },
            )
        except EnergyChartsNoDataError:
            return []
        return parse_frequency(payload)

    async def async_get_power(
        self, endpoint: str, start: datetime, end: datetime
    ) -> dict[str, Any]:
        """Last und Erzeugung Deutschland (MW je Viertelstunde).

        ``endpoint`` ist ``total_power`` (inkl. Eigenversorgung der Industrie) oder
        ``public_power`` (öffentliches Netz).
        """
        return await self._async_get(
            endpoint,
            {
                "country": GRID_COUNTRY,
                "start": str(int(start.timestamp())),
                "end": str(int(end.timestamp())),
            },
        )


# ---------------------------------------------------------------------------
# Netzampel
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignalSlot:
    """Ein Zeitfenster der Netzampel."""

    start: datetime
    end: datetime
    share: float | None
    signal: int | None

    @property
    def state(self) -> str | None:
        """Signal als Text-Schlüssel."""
        return None if self.signal is None else SIGNAL_MAP.get(self.signal)


@dataclass(frozen=True, slots=True)
class SignalData:
    """Netzampel mit allen Zeitfenstern ab heute."""

    slots: tuple[SignalSlot, ...]
    substitute: bool

    def current(self, now: datetime) -> SignalSlot | None:
        """Zeitfenster, in dem ``now`` liegt (nicht einfach der letzte Eintrag)."""
        for slot in self.slots:
            if slot.start <= now < slot.end:
                return slot
        return None

    def forecast(self, now: datetime, hours: int) -> list[SignalSlot]:
        """Zeitfenster ab dem aktuellen bis ``hours`` Stunden in die Zukunft."""
        limit = now + timedelta(hours=hours)
        return [s for s in self.slots if s.end > now and s.start < limit]

    def next_green(self, now: datetime) -> tuple[datetime, datetime] | None:
        """Beginn und Ende des nächsten zusammenhängenden grünen Zeitfensters."""
        start: datetime | None = None
        end: datetime | None = None
        for slot in self.slots:
            if slot.end <= now:
                continue
            if slot.signal == SIGNAL_BEST:
                if start is None:
                    start = slot.start
                end = slot.end
            elif start is not None:
                break
        if start is None or end is None:
            return None
        return start, end


def parse_signal(payload: dict[str, Any]) -> SignalData:
    """Wertet die Antwort von /signal aus."""
    times = payload.get("unix_seconds")
    if not isinstance(times, list) or not times:
        raise EnergyChartsResponseError("Antwort von /signal ohne unix_seconds")
    raw_shares = payload.get("share")
    raw_signals = payload.get("signal")
    shares: list[Any] = raw_shares if isinstance(raw_shares, list) else []
    signals: list[Any] = raw_signals if isinstance(raw_signals, list) else []

    stamps = [_ts(t) for t in times]
    # Schrittweite aus den Daten, Standard 15 Minuten
    step = timedelta(minutes=15)
    valid = [s for s in stamps if s is not None]
    if len(valid) >= 2:
        diffs = [b - a for a, b in zip(valid, valid[1:], strict=False) if b > a]
        if diffs:
            step = min(diffs)

    slots: list[SignalSlot] = []
    for index, start in enumerate(stamps):
        if start is None:
            continue
        if index + 1 < len(stamps) and stamps[index + 1] is not None:
            end = stamps[index + 1]
            assert end is not None
        else:
            end = start + step
        number = _num(signals[index]) if index < len(signals) else None
        signal = None if number is None else int(number)
        slots.append(
            SignalSlot(
                start=start,
                end=end,
                share=_num(shares[index]) if index < len(shares) else None,
                signal=signal,
            )
        )
    slots.sort(key=lambda s: s.start)
    return SignalData(slots=tuple(slots), substitute=bool(payload.get("substitute")))


# ---------------------------------------------------------------------------
# Netzfrequenz
# ---------------------------------------------------------------------------


def parse_frequency(payload: dict[str, Any]) -> list[tuple[int, float]]:
    """Wertet die Antwort von /frequency aus (Paare aus Unix-Sekunde und Hz)."""
    times = payload.get("unix_seconds")
    values = payload.get("data")
    if not isinstance(times, list) or not isinstance(values, list):
        raise EnergyChartsResponseError("Antwort von /frequency ohne unix_seconds/data")
    points: list[tuple[int, float]] = []
    for raw_time, raw_value in zip(times, values, strict=False):
        value = _num(raw_value)
        if value is None or isinstance(raw_time, bool):
            continue
        if not isinstance(raw_time, int | float):
            continue
        # Offensichtliche Messfehler verwerfen
        if not 45.0 <= value <= 55.0:
            continue
        points.append((int(raw_time), value))
    return points


@dataclass(frozen=True, slots=True)
class FrequencyData:
    """Ausgewertete Netzfrequenz."""

    time: datetime
    frequency: float
    minimum: float
    maximum: float
    count: int

    @property
    def deviation_mhz(self) -> float:
        """Abweichung von 50 Hz in mHz."""
        return (self.frequency - 50.0) * 1000


# ---------------------------------------------------------------------------
# Last und Erzeugung
# ---------------------------------------------------------------------------

# Schlüssel -> Regeln zum Finden der Produktionsart (Vergleich in Kleinbuchstaben)
# Mehrere Treffer werden addiert (z. B. Wasserkraft).
POWER_TYPES: dict[str, dict[str, Any]] = {
    "load": {"starts": ("load",), "excludes": ("residual",)},
    "residual_load": {"starts": ("residual load",)},
    "wind_onshore": {"contains": ("wind onshore",)},
    "wind_offshore": {"contains": ("wind offshore",)},
    "solar": {"starts": ("solar",)},
    "biomass": {"starts": ("biomass",)},
    "hydro": {
        "starts": ("hydro",),
        "excludes": ("pumped", "consumption"),
        "sum": True,
    },
    "lignite": {"contains": ("brown coal", "lignite")},
    "hard_coal": {"contains": ("hard coal",)},
    "gas": {"starts": ("fossil gas",)},
    "cross_border": {"contains": ("cross border",)},
}
RENEWABLE_SHARE_NAMES = ("renewable share of load",)
# Für die eigene Berechnung des Anteils Erneuerbarer an der Last
RENEWABLE_KEYS = ("wind_onshore", "wind_offshore", "solar", "biomass", "hydro")
GEOTHERMAL_NAMES = ("geothermal",)


@dataclass(frozen=True, slots=True)
class PowerValue:
    """Letzter gültiger Wert einer Größe mit Zeitpunkt."""

    value: float
    time: datetime
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PowerData:
    """Ausgewertete Last- und Erzeugungsdaten (Leistung in GW)."""

    values: dict[str, PowerValue]
    renewable_share: PowerValue | None
    names: tuple[str, ...]


def _matches(name: str, rule: dict[str, Any]) -> bool:
    lowered = name.casefold().strip()
    if any(ex in lowered for ex in rule.get("excludes", ())):
        return False
    if any(lowered.startswith(s) for s in rule.get("starts", ())):
        return True
    return any(c in lowered for c in rule.get("contains", ()))


def _last_valid(
    times: list[datetime | None], data: list[Any]
) -> tuple[int, float] | None:
    for index in range(min(len(times), len(data)) - 1, -1, -1):
        value = _num(data[index])
        if value is not None and times[index] is not None:
            return index, value
    return None


def parse_power(payload: dict[str, Any]) -> PowerData:
    """Wertet die Antwort von /total_power bzw. /public_power aus.

    Die neuesten Werte sind oft ``null``; je Größe wird der letzte Nicht-null-Wert
    verwendet. MW werden in GW umgerechnet.
    """
    raw_times = payload.get("unix_seconds")
    types = payload.get("production_types")
    if not isinstance(raw_times, list) or not isinstance(types, list):
        raise EnergyChartsResponseError("Antwort ohne unix_seconds/production_types")
    times = [_ts(t) for t in raw_times]
    series: dict[str, list[Any]] = {}
    for entry in types:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            data = entry.get("data")
            series[entry["name"]] = data if isinstance(data, list) else []

    values: dict[str, PowerValue] = {}
    for key, rule in POWER_TYPES.items():
        matched = [name for name in series if _matches(name, rule)]
        if not matched:
            continue
        if not rule.get("sum"):
            matched = matched[:1]
        total = 0.0
        stamp: datetime | None = None
        used: list[str] = []
        for name in matched:
            found = _last_valid(times, series[name])
            if found is None:
                continue
            index, value = found
            total += value
            used.append(name)
            when = times[index]
            assert when is not None
            stamp = when if stamp is None else min(stamp, when)
        if used and stamp is not None:
            values[key] = PowerValue(
                value=total / 1000, time=stamp, sources=tuple(used)
            )

    renewable_share = _renewable_share(times, series)
    return PowerData(
        values=values, renewable_share=renewable_share, names=tuple(series)
    )


def _renewable_share(
    times: list[datetime | None], series: dict[str, list[Any]]
) -> PowerValue | None:
    # 1. Direkt geliefert
    for name, data in series.items():
        if name.casefold().strip() in RENEWABLE_SHARE_NAMES:
            found = _last_valid(times, data)
            if found is not None:
                index, value = found
                when = times[index]
                assert when is not None
                return PowerValue(value=value, time=when, sources=(name,))

    # 2. Selbst berechnen: Summe Erneuerbare / Last zum letzten Zeitpunkt mit Last
    load_names = [n for n in series if _matches(n, POWER_TYPES["load"])]
    if not load_names:
        return None
    load_name = load_names[0]
    renewable_names = [
        name
        for key in RENEWABLE_KEYS
        for name in series
        if _matches(name, POWER_TYPES[key])
    ] + [n for n in series if n.casefold().strip().startswith(GEOTHERMAL_NAMES)]
    if not renewable_names:
        return None
    load_data = series[load_name]
    for index in range(min(len(times), len(load_data)) - 1, -1, -1):
        load = _num(load_data[index])
        when = times[index]
        if load is None or load <= 0 or when is None:
            continue
        parts = [
            _num(series[name][index]) if index < len(series[name]) else None
            for name in renewable_names
        ]
        known = [p for p in parts if p is not None]
        # Nur rechnen, wenn mindestens Wind und Solar vorliegen
        if len(known) < min(3, len(parts)):
            continue
        return PowerValue(
            value=sum(known) / load * 100,
            time=when,
            sources=(load_name, *renewable_names),
        )
    return None
