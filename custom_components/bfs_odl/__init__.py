"""Die Integration Umwelt & Netz (BfS / Energy-Charts).

Ein Eintrag ist entweder eine ODL-Messsonde des BfS (bestehende Einträge, ohne
``entry_type``) oder das Stromnetz Deutschland aus Energy-Charts.
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_change

from .const import (
    CONF_ENABLE_FREQUENCY,
    CONF_ENABLE_POWER,
    CONF_ENABLE_SIGNAL,
    CONF_ENTRY_TYPE,
    CONF_KENN,
    ENTRY_TYPE_GRID,
    ENTRY_TYPE_ODL,
)
from .coordinator import (
    BfsOdlConfigEntry,
    BfsOdlCoordinator,
    async_get_api,
    history_store,
)
from .grid_coordinator import (
    FrequencyCoordinator,
    GridRuntimeData,
    PowerCoordinator,
    SignalCoordinator,
    async_get_energy_charts_api,
)
from .grid_entity import entity_source

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


def entry_type(entry: BfsOdlConfigEntry) -> str:
    """Art des Eintrags; bestehende Einträge ohne Angabe sind ODL-Sonden."""
    return str(entry.data.get(CONF_ENTRY_TYPE, ENTRY_TYPE_ODL))


async def async_setup_entry(hass: HomeAssistant, entry: BfsOdlConfigEntry) -> bool:
    """Richtet eine Sonde oder das Stromnetz ein."""
    if entry_type(entry) == ENTRY_TYPE_GRID:
        await _async_setup_grid(hass, entry)
    else:
        coordinator = BfsOdlCoordinator(hass, entry, async_get_api(hass))
        await coordinator.async_config_entry_first_refresh()
        entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_setup_grid(hass: HomeAssistant, entry: BfsOdlConfigEntry) -> None:
    api = async_get_energy_charts_api(hass)
    data = GridRuntimeData()
    if entry.options.get(CONF_ENABLE_SIGNAL, True):
        data.signal = SignalCoordinator(hass, entry, api)
    if entry.options.get(CONF_ENABLE_FREQUENCY, True):
        data.frequency = FrequencyCoordinator(hass, entry, api)
    if entry.options.get(CONF_ENABLE_POWER, True):
        data.power = PowerCoordinator(hass, entry, api)

    for coordinator in data.coordinators:
        await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = data
    _async_remove_disabled_entities(hass, entry, data)

    if (signal := data.signal) is not None:
        # Die Ampel gilt je Viertelstunde: Zustände zum Wechsel neu berechnen,
        # ohne die API erneut abzufragen.
        @callback
        def _async_quarter_hour(_now: datetime) -> None:
            signal.async_update_listeners()

        entry.async_on_unload(
            async_track_time_change(
                hass, _async_quarter_hour, minute=[0, 15, 30, 45], second=1
            )
        )


@callback
def _async_remove_disabled_entities(
    hass: HomeAssistant, entry: BfsOdlConfigEntry, data: GridRuntimeData
) -> None:
    """Entfernt Entitäten abgeschalteter Teile aus der Entitätsregistrierung."""
    active = {c.source for c in data.coordinators}
    registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        source = entity_source(registry_entry.unique_id)
        if source is not None and source not in active:
            registry.async_remove(registry_entry.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: BfsOdlConfigEntry) -> bool:
    """Entlädt einen Eintrag."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: BfsOdlConfigEntry) -> None:
    """Löscht den gespeicherten ODL-Verlauf, wenn eine Sonde entfernt wird."""
    if entry_type(entry) == ENTRY_TYPE_ODL:
        await history_store(hass, entry.data[CONF_KENN]).async_remove()
