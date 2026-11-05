"""Diagnose-Daten der Integration Umwelt & Netz (BfS / Energy-Charts)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CONF_POSTAL_CODE
from .coordinator import BfsOdlConfigEntry
from .energy_charts import SignalData
from .grid_coordinator import GridCoordinator, GridRuntimeData

# Die PLZ verrät den Wohnort; Diagnosen werden oft öffentlich geteilt
TO_REDACT = {CONF_POSTAL_CODE}


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_serialize(item) for item in value]
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BfsOdlConfigEntry
) -> dict[str, Any]:
    """Liefert die Diagnose für einen Eintrag."""
    if isinstance(entry.runtime_data, GridRuntimeData):
        return _grid_diagnostics(entry, entry.runtime_data)
    coordinator = entry.runtime_data
    history = coordinator.history
    data = coordinator.data
    return {
        "entry": {
            "title": entry.title,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": (
                repr(coordinator.last_exception) if coordinator.last_exception else None
            ),
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "timeseries_available": coordinator.timeseries_available,
        },
        "api": coordinator.api.diagnostics(),
        "history": {
            "count": len(history),
            "oldest": min(history).isoformat() if history else None,
            "newest": max(history).isoformat() if history else None,
        },
        "data": _serialize(asdict(data)) if data is not None else None,
    }


def _coordinator_diagnostics(coordinator: GridCoordinator[Any]) -> dict[str, Any]:
    data = coordinator.data
    if isinstance(data, SignalData):
        now = dt_util.utcnow()
        current = data.current(now)
        summary: Any = {
            "slots": len(data.slots),
            "first": data.slots[0].start.isoformat() if data.slots else None,
            "last": data.slots[-1].end.isoformat() if data.slots else None,
            "substitute": data.substitute,
            "current": _serialize(asdict(current)) if current else None,
        }
    else:
        summary = _serialize(asdict(data)) if data is not None else None
    return {
        "last_update_success": coordinator.last_update_success,
        "last_exception": (
            repr(coordinator.last_exception) if coordinator.last_exception else None
        ),
        "update_interval_seconds": (
            coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None
        ),
        **coordinator.diagnostics(),
        "data": summary,
    }


def _grid_diagnostics(
    entry: BfsOdlConfigEntry, runtime: GridRuntimeData
) -> dict[str, Any]:
    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinators": {
            coordinator.source: _coordinator_diagnostics(coordinator)
            for coordinator in runtime.coordinators
        },
    }
