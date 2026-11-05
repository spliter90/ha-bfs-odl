"""Diagnose-Daten der Integration BfS Ortsdosisleistung (ODL)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import BfsOdlConfigEntry


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
    """Liefert die Diagnose für einen Eintrag (enthält nur öffentliche Daten)."""
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
