"""Gemeinsame Basisklasse der Stromnetz-Entitäten."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    GRID_ATTRIBUTION,
    GRID_DEVICE_NAME,
    GRID_MANUFACTURER,
    GRID_MODEL,
    GRID_UNIQUE_ID,
    GRID_URL,
)
from .grid_coordinator import GridCoordinator

SOURCES = ("signal", "frequency", "power")


def grid_unique_id(source: str, key: str) -> str:
    """Eindeutige ID einer Stromnetz-Entität, z. B. energy_charts_de_power_solar."""
    return f"{GRID_UNIQUE_ID}_{source}_{key}"


def entity_source(unique_id: str) -> str | None:
    """Ermittelt aus der eindeutigen ID, zu welchem Endpunkt eine Entität gehört."""
    prefix = f"{GRID_UNIQUE_ID}_"
    if not unique_id.startswith(prefix):
        return None
    source = unique_id[len(prefix) :].split("_", 1)[0]
    return source if source in SOURCES else None


class GridEntity[CoordinatorT: GridCoordinator[Any]](CoordinatorEntity[CoordinatorT]):
    """Basis für alle Entitäten des Stromnetz-Geräts."""

    _attr_has_entity_name = True
    _attr_attribution = GRID_ATTRIBUTION

    def __init__(self, coordinator: CoordinatorT, key: str) -> None:
        """Initialisiert die Entität."""
        super().__init__(coordinator)
        self._attr_unique_id = grid_unique_id(coordinator.source, key)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, GRID_UNIQUE_ID)},
            name=GRID_DEVICE_NAME,
            manufacturer=GRID_MANUFACTURER,
            model=GRID_MODEL,
            configuration_url=GRID_URL,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        """Nicht verfügbar ohne Daten."""
        return super().available and self.coordinator.data is not None
