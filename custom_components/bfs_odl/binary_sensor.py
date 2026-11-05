"""Binärsensor "Erhöhte Strahlung"."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_PERCENT_ABOVE_MEAN,
    CONF_THRESHOLD,
    DEFAULT_PERCENT_ABOVE_MEAN,
    DEFAULT_THRESHOLD,
)
from .coordinator import BfsOdlConfigEntry, BfsOdlCoordinator
from .entity import BfsOdlEntity, measurement_usable
from .grid_binary_sensor import build_grid_binary_sensors
from .grid_coordinator import GridRuntimeData

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BfsOdlConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Legt die Binärsensoren einer Sonde bzw. des Stromnetzes an."""
    runtime_data = entry.runtime_data
    if isinstance(runtime_data, GridRuntimeData):
        async_add_entities(build_grid_binary_sensors(runtime_data))
        return
    async_add_entities([BfsOdlElevatedRadiationSensor(runtime_data)])


class BfsOdlElevatedRadiationSensor(BfsOdlEntity, BinarySensorEntity):
    """An, wenn die Ortsdosisleistung über Schwelle oder deutlich über dem Mittel liegt."""

    _attr_translation_key = "elevated_radiation"

    def __init__(self, coordinator: BfsOdlCoordinator) -> None:
        """Initialisiert den Binärsensor."""
        super().__init__(coordinator, "elevated_radiation")

    @property
    def _threshold(self) -> float:
        return float(
            self.coordinator.config_entry.options.get(CONF_THRESHOLD, DEFAULT_THRESHOLD)
        )

    @property
    def _percent(self) -> float:
        return float(
            self.coordinator.config_entry.options.get(
                CONF_PERCENT_ABOVE_MEAN, DEFAULT_PERCENT_ABOVE_MEAN
            )
        )

    @property
    def _mean_limit(self) -> float | None:
        mean = self.coordinator.data.mean_7d
        if mean is None:
            return None
        return mean * (1 + self._percent / 100)

    @property
    def available(self) -> bool:
        """Nur verfügbar, wenn ein aktueller Messwert vorliegt."""
        return (
            super().available
            and self.coordinator.data is not None
            and measurement_usable(self.coordinator.data)
        )

    @property
    def _reasons(self) -> list[str]:
        value = self.coordinator.data.station.value
        if value is None:
            return []
        reasons: list[str] = []
        if value > self._threshold:
            reasons.append("threshold")
        limit = self._mean_limit
        if limit is not None and value > limit:
            reasons.append("mean")
        return reasons

    @property
    def is_on(self) -> bool:
        """Erhöhte Strahlung?"""
        return bool(self._reasons)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Schwellen und Auslöser."""
        limit = self._mean_limit
        return {
            "threshold": self._threshold,
            "percent_above_mean": self._percent,
            "mean_7d": (
                round(self.coordinator.data.mean_7d, 4)
                if self.coordinator.data.mean_7d is not None
                else None
            ),
            "mean_limit": round(limit, 4) if limit is not None else None,
            "triggered_by": self._reasons,
        }
