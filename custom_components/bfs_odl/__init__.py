"""Die Integration BfS Ortsdosisleistung (ODL)."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_KENN
from .coordinator import (
    BfsOdlConfigEntry,
    BfsOdlCoordinator,
    async_get_api,
    history_store,
)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: BfsOdlConfigEntry) -> bool:
    """Richtet eine Sonde ein."""
    coordinator = BfsOdlCoordinator(hass, entry, async_get_api(hass))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BfsOdlConfigEntry) -> bool:
    """Entlädt eine Sonde."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: BfsOdlConfigEntry) -> None:
    """Löscht den gespeicherten Verlauf, wenn der Eintrag entfernt wird."""
    await history_store(hass, entry.data[CONF_KENN]).async_remove()
