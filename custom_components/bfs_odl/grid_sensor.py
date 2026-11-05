"""Sensoren des Stromnetz-Geräts (Energy-Charts)."""

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
from homeassistant.const import PERCENTAGE, UnitOfFrequency, UnitOfPower
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .const import (
    FREQUENCY_STALE_AFTER,
    SIGNAL_FORECAST_HOURS,
    SIGNAL_OPTIONS,
)
from .energy_charts import FrequencyData, PowerData, SignalData
from .grid_coordinator import (
    FrequencyCoordinator,
    GridCoordinator,
    GridRuntimeData,
    SignalCoordinator,
)
from .grid_entity import GridEntity

UNIT_MILLIHERTZ = "mHz"


@dataclass(frozen=True, kw_only=True)
class GridSensorEntityDescription(SensorEntityDescription):
    """Beschreibt einen Stromnetz-Sensor."""

    value_fn: Callable[[Any, datetime], StateType | datetime]
    attributes_fn: Callable[[Any, datetime], dict[str, Any] | None] = (
        lambda data, now: None
    )


# ---------------------------------------------------------------------------
# Netzampel
# ---------------------------------------------------------------------------


def _signal_state(data: SignalData, now: datetime) -> str | None:
    slot = data.current(now)
    return slot.state if slot else None


def _signal_attributes(data: SignalData, now: datetime) -> dict[str, Any]:
    slot = data.current(now)
    return {
        "renewable_share": slot.share if slot else None,
        "slot_start": slot.start.isoformat() if slot else None,
        "slot_end": slot.end.isoformat() if slot else None,
        "substitute": data.substitute,
        "forecast": [
            {
                "start": s.start.isoformat(),
                "end": s.end.isoformat(),
                "share": s.share,
                "signal": s.signal,
                "state": s.state,
            }
            for s in data.forecast(now, SIGNAL_FORECAST_HOURS)
        ],
    }


def _signal_share(data: SignalData, now: datetime) -> float | None:
    slot = data.current(now)
    return slot.share if slot else None


def _next_green(data: SignalData, now: datetime) -> datetime | None:
    window = data.next_green(now)
    return window[0] if window else None


def _next_green_attributes(data: SignalData, now: datetime) -> dict[str, Any]:
    window = data.next_green(now)
    if window is None:
        return {"end": None, "duration_minutes": None}
    start, end = window
    return {
        "end": end.isoformat(),
        "duration_minutes": int((end - start).total_seconds() // 60),
    }


SIGNAL_SENSORS: tuple[GridSensorEntityDescription, ...] = (
    GridSensorEntityDescription(
        key="state",
        translation_key="grid_signal",
        device_class=SensorDeviceClass.ENUM,
        options=SIGNAL_OPTIONS,
        value_fn=_signal_state,
        attributes_fn=_signal_attributes,
    ),
    GridSensorEntityDescription(
        key="renewable_share",
        translation_key="signal_renewable_share",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_signal_share,
    ),
    GridSensorEntityDescription(
        key="next_green",
        translation_key="next_green_window",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_next_green,
        attributes_fn=_next_green_attributes,
    ),
)

# ---------------------------------------------------------------------------
# Netzfrequenz
# ---------------------------------------------------------------------------


def _frequency_attributes(data: FrequencyData, now: datetime) -> dict[str, Any]:
    return {"measured_at": data.time.isoformat(), "samples_1h": data.count}


FREQUENCY_SENSORS: tuple[GridSensorEntityDescription, ...] = (
    GridSensorEntityDescription(
        key="frequency",
        translation_key="grid_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda data, now: data.frequency,
        attributes_fn=_frequency_attributes,
    ),
    GridSensorEntityDescription(
        key="deviation",
        translation_key="frequency_deviation",
        native_unit_of_measurement=UNIT_MILLIHERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data, now: round(data.deviation_mhz, 1),
    ),
    GridSensorEntityDescription(
        key="minimum_1h",
        translation_key="frequency_minimum",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda data, now: data.minimum,
    ),
    GridSensorEntityDescription(
        key="maximum_1h",
        translation_key="frequency_maximum",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda data, now: data.maximum,
    ),
)

# ---------------------------------------------------------------------------
# Last und Erzeugung
# ---------------------------------------------------------------------------


def _power_value(key: str) -> Callable[[PowerData, datetime], float | None]:
    def _value(data: PowerData, now: datetime) -> float | None:
        item = data.values.get(key)
        return round(item.value, 3) if item else None

    return _value


def _power_attributes(key: str) -> Callable[[PowerData, datetime], dict[str, Any]]:
    def _attributes(data: PowerData, now: datetime) -> dict[str, Any]:
        item = data.values.get(key)
        return {
            "data_time": item.time.isoformat() if item else None,
            "production_types": list(item.sources) if item else [],
        }

    return _attributes


def _share_value(data: PowerData, now: datetime) -> float | None:
    item = data.renewable_share
    return round(item.value, 1) if item else None


def _share_attributes(data: PowerData, now: datetime) -> dict[str, Any]:
    item = data.renewable_share
    return {
        "data_time": item.time.isoformat() if item else None,
        "production_types": list(item.sources) if item else [],
    }


POWER_KEYS = (
    "load",
    "residual_load",
    "wind_onshore",
    "wind_offshore",
    "solar",
    "biomass",
    "hydro",
    "lignite",
    "hard_coal",
    "gas",
    "cross_border",
)

POWER_SENSORS: tuple[GridSensorEntityDescription, ...] = (
    *(
        GridSensorEntityDescription(
            key=key,
            translation_key=f"power_{key}",
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.GIGA_WATT,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
            value_fn=_power_value(key),
            attributes_fn=_power_attributes(key),
        )
        for key in POWER_KEYS
    ),
    GridSensorEntityDescription(
        key="renewable_share_load",
        translation_key="renewable_share_load",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_share_value,
        attributes_fn=_share_attributes,
    ),
)


def build_grid_sensors(data: GridRuntimeData) -> list[GridSensor]:
    """Alle Sensoren der aktiven Endpunkte."""
    entities: list[GridSensor] = []
    if data.signal is not None:
        entities.extend(SignalSensor(data.signal, d) for d in SIGNAL_SENSORS)
    if data.frequency is not None:
        entities.extend(FrequencySensor(data.frequency, d) for d in FREQUENCY_SENSORS)
    if data.power is not None:
        entities.extend(GridSensor(data.power, d) for d in POWER_SENSORS)
    return entities


class GridSensor(GridEntity[GridCoordinator[Any]], SensorEntity):
    """Ein Sensor des Stromnetz-Geräts."""

    entity_description: GridSensorEntityDescription

    def __init__(
        self,
        coordinator: GridCoordinator[Any],
        description: GridSensorEntityDescription,
    ) -> None:
        """Initialisiert den Sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Nicht verfügbar, wenn der Endpunkt keinen Wert liefert."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> StateType | datetime:
        """Aktueller Wert."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data, dt_util.utcnow())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Zusätzliche Attribute."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.attributes_fn(
            self.coordinator.data, dt_util.utcnow()
        )


class SignalSensor(GridSensor):
    """Ampel-Sensor; die 24-h-Prognose wird nicht im Verlauf gespeichert."""

    _unrecorded_attributes = frozenset({"forecast"})

    def __init__(
        self, coordinator: SignalCoordinator, description: GridSensorEntityDescription
    ) -> None:
        """Initialisiert den Sensor."""
        super().__init__(coordinator, description)


class FrequencySensor(GridSensor):
    """Frequenz-Sensor; veraltete Werte gelten als Ausfall."""

    def __init__(
        self,
        coordinator: FrequencyCoordinator,
        description: GridSensorEntityDescription,
    ) -> None:
        """Initialisiert den Sensor."""
        super().__init__(coordinator, description)

    @property
    def available(self) -> bool:
        """Nicht verfügbar, wenn der letzte Wert zu alt ist."""
        data: FrequencyData | None = self.coordinator.data
        return (
            super().available
            and data is not None
            and dt_util.utcnow() - data.time <= FREQUENCY_STALE_AFTER
        )
