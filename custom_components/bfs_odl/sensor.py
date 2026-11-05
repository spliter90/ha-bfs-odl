"""Sensoren der Integration BfS Ortsdosisleistung (ODL)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import STATUS_OPTIONS, UNIT_USV_H
from .coordinator import BfsOdlConfigEntry, BfsOdlCoordinator, BfsOdlData
from .entity import BfsOdlEntity, measurement_usable

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class BfsOdlSensorEntityDescription(SensorEntityDescription):
    """Beschreibt einen ODL-Sensor."""

    value_fn: Callable[[BfsOdlData], StateType | datetime]
    available_fn: Callable[[BfsOdlData], bool] = measurement_usable
    station_attributes: bool = False


SENSORS: tuple[BfsOdlSensorEntityDescription, ...] = (
    BfsOdlSensorEntityDescription(
        key="dose_rate",
        translation_key="dose_rate",
        native_unit_of_measurement=UNIT_USV_H,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda data: data.station.value,
        station_attributes=True,
    ),
    BfsOdlSensorEntityDescription(
        key="dose_rate_cosmic",
        translation_key="dose_rate_cosmic",
        native_unit_of_measurement=UNIT_USV_H,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda data: data.station.value_cosmic,
        available_fn=lambda data: (
            measurement_usable(data) and data.station.value_cosmic is not None
        ),
    ),
    BfsOdlSensorEntityDescription(
        key="dose_rate_terrestrial",
        translation_key="dose_rate_terrestrial",
        native_unit_of_measurement=UNIT_USV_H,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda data: data.station.value_terrestrial,
        available_fn=lambda data: (
            measurement_usable(data) and data.station.value_terrestrial is not None
        ),
    ),
    BfsOdlSensorEntityDescription(
        key="dose_rate_mean_7d",
        translation_key="dose_rate_mean_7d",
        native_unit_of_measurement=UNIT_USV_H,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda data: data.mean_7d,
        available_fn=lambda data: data.mean_7d is not None,
    ),
    BfsOdlSensorEntityDescription(
        key="measurement_time",
        translation_key="measurement_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.station.end_measure,
        available_fn=lambda data: data.station.end_measure is not None,
    ),
    BfsOdlSensorEntityDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.station.status,
        available_fn=lambda data: data.station.status is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BfsOdlConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Legt die Sensoren einer Sonde an."""
    coordinator = entry.runtime_data
    async_add_entities(
        BfsOdlSensor(coordinator, description) for description in SENSORS
    )


class BfsOdlSensor(BfsOdlEntity, SensorEntity):
    """Ein Sensor einer ODL-Sonde."""

    entity_description: BfsOdlSensorEntityDescription

    def __init__(
        self,
        coordinator: BfsOdlCoordinator,
        description: BfsOdlSensorEntityDescription,
    ) -> None:
        """Initialisiert den Sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Nicht verfügbar, wenn der Abruf scheitert oder kein Wert vorliegt."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.entity_description.available_fn(self.coordinator.data)
        )

    @property
    def native_value(self) -> StateType | datetime:
        """Aktueller Wert."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Stammdaten der Sonde am Hauptsensor."""
        if not self.entity_description.station_attributes:
            return None
        station = self.coordinator.data.station
        return {
            "station_name": station.name,
            "kenn": station.kenn,
            "postal_code": station.plz,
            "height_above_sea": station.height_above_sea,
            "validated": station.validated,
            ATTR_LATITUDE: station.latitude,
            ATTR_LONGITUDE: station.longitude,
        }
