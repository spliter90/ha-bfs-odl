"""Binärsensoren des Stromnetz-Geräts (Energy-Charts)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_FREQUENCY_THRESHOLD,
    DEFAULT_FREQUENCY_THRESHOLD,
    SIGNAL_BEST,
)
from .grid_coordinator import FrequencyCoordinator, GridRuntimeData, SignalCoordinator
from .grid_entity import GridEntity


def build_grid_binary_sensors(data: GridRuntimeData) -> list[BinarySensorEntity]:
    """Binärsensoren der aktiven Endpunkte."""
    entities: list[BinarySensorEntity] = []
    if data.signal is not None:
        entities.append(GoodTimeForConsumptionSensor(data.signal))
    if data.frequency is not None:
        entities.append(FrequencyAbnormalSensor(data.frequency))
    return entities


class GoodTimeForConsumptionSensor(GridEntity[SignalCoordinator], BinarySensorEntity):
    """An, wenn die Netzampel auf dem besten Wert (grün) steht."""

    _attr_translation_key = "good_time_for_consumption"

    def __init__(self, coordinator: SignalCoordinator) -> None:
        """Initialisiert den Binärsensor."""
        super().__init__(coordinator, "good_time")

    @property
    def available(self) -> bool:
        """Nur verfügbar, wenn es ein aktuelles Zeitfenster gibt."""
        return (
            super().available
            and self.coordinator.data.current(dt_util.utcnow()) is not None
        )

    @property
    def is_on(self) -> bool | None:
        """Ampel grün?"""
        slot = self.coordinator.data.current(dt_util.utcnow())
        if slot is None or slot.signal is None:
            return None
        return slot.signal == SIGNAL_BEST

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Aktuelles Zeitfenster."""
        slot = self.coordinator.data.current(dt_util.utcnow())
        return {
            "signal": slot.state if slot else None,
            "renewable_share": slot.share if slot else None,
            "slot_end": slot.end.isoformat() if slot else None,
        }


class FrequencyAbnormalSensor(GridEntity[FrequencyCoordinator], BinarySensorEntity):
    """An, wenn die Netzfrequenz mehr als die Schwelle von 50 Hz abweicht."""

    _attr_translation_key = "frequency_abnormal"

    def __init__(self, coordinator: FrequencyCoordinator) -> None:
        """Initialisiert den Binärsensor."""
        super().__init__(coordinator, "abnormal")

    @property
    def _threshold(self) -> float:
        return float(
            self.coordinator.config_entry.options.get(
                CONF_FREQUENCY_THRESHOLD, DEFAULT_FREQUENCY_THRESHOLD
            )
        )

    @property
    def available(self) -> bool:
        """Nur nicht verfügbar, wenn die Daten älter als die Stale-Grenze sind."""
        # Ein einzelner fehlgeschlagener Abruf zählt nicht, nur das Datenalter
        data = self.coordinator.data
        return data is not None and not self.coordinator.is_stale(data)

    @property
    def is_on(self) -> bool | None:
        """Abweichung größer als die Schwelle?"""
        data = self.coordinator.data
        if data is None:
            return None
        return abs(data.deviation_mhz) > self._threshold

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Schwelle und Abweichung."""
        data = self.coordinator.data
        return {
            "threshold_mhz": self._threshold,
            "deviation_mhz": round(data.deviation_mhz, 1) if data else None,
            "measured_at": data.time.isoformat() if data else None,
        }
