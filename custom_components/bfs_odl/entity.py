"""Gemeinsame Basisklasse der Entitäten."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    ATTRIBUTION,
    CONF_STATION_NAME,
    DOMAIN,
    MANUFACTURER,
    MODEL,
    ODLINFO_URL,
    STATUS_DEFECTIVE,
)
from .coordinator import BfsOdlCoordinator, BfsOdlData

# Ein "aktueller" Wert, der älter ist, gilt als ausgefallen
STALE_AFTER = timedelta(hours=12)


def measurement_usable(data: BfsOdlData) -> bool:
    """Liefert die Sonde einen aktuellen, gültigen Messwert?"""
    station = data.station
    if station.value is None or station.status == STATUS_DEFECTIVE:
        return False
    if station.end_measure is None:
        return False
    return dt_util.utcnow() - station.end_measure <= STALE_AFTER


class BfsOdlEntity(CoordinatorEntity[BfsOdlCoordinator]):
    """Basis für alle Entitäten einer Sonde."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: BfsOdlCoordinator, key: str) -> None:
        """Initialisiert die Entität."""
        super().__init__(coordinator)
        kenn = coordinator.kenn
        name = coordinator.config_entry.data.get(CONF_STATION_NAME) or kenn
        self._attr_unique_id = f"{kenn}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, kenn)},
            name=name,
            manufacturer=MANUFACTURER,
            model=MODEL,
            serial_number=kenn,
            configuration_url=ODLINFO_URL,
        )
