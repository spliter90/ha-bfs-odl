"""Die Integration Umwelt & Netz (BfS / Energy-Charts).

Ein Eintrag ist entweder eine ODL-Messsonde des BfS (bestehende Einträge, ohne
``entry_type``) oder das Stromnetz Deutschland aus Energy-Charts.
"""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_change

from .const import (
    CONF_ENABLE_FREQUENCY,
    CONF_ENABLE_POWER,
    CONF_ENABLE_SIGNAL,
    CONF_ENTRY_TYPE,
    CONF_KENN,
    DOMAIN,
    ENTRY_TYPE_GRID,
    ENTRY_TYPE_ODL,
    ISSUE_FREQUENCY_MISSING,
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
    frequency_store,
)
from .grid_entity import entity_source

_LOGGER = logging.getLogger(__name__)

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

    # Jede Quelle für sich: Fällt eine aus, blockiert sie den Eintrag nicht. Ihre
    # Entitäten bleiben nicht verfügbar; der Coordinator versucht es im normalen
    # Intervall erneut, sobald die Entitäten angemeldet sind.
    failed: dict[str, str] = {}
    for coordinator in data.coordinators:
        try:
            await coordinator.async_config_entry_first_refresh()
        except ConfigEntryNotReady as err:
            failed[coordinator.source] = coordinator.last_error or repr(err.__cause__)
            continue
        if not coordinator.has_data:
            failed[coordinator.source] = coordinator.last_error or "keine Daten"

    if len(failed) == len(data.coordinators):
        # Alle aktivierten Quellen gescheitert: später erneut versuchen
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="grid_not_ready",
            translation_placeholders={
                "errors": "; ".join(f"{src}: {err}" for src, err in failed.items())
            },
        )
    for source, error in failed.items():
        # Die Frequenz meldet ihren Ausfall selbst (einmal pro Ausfall)
        if source != "frequency":
            _LOGGER.warning(
                "Stromnetz: Datenquelle „%s“ beim Start nicht verfügbar (%s). Ihre "
                "Entitäten bleiben nicht verfügbar, bis ein späterer Abruf klappt; "
                "die übrigen Quellen laufen normal",
                source,
                error,
            )

    entry.runtime_data = data
    _async_remove_disabled_entities(hass, entry, data)
    if data.frequency is None:
        # Frequenz abgeschaltet: ein offenes Repair-Issue ist gegenstandslos
        ir.async_delete_issue(hass, DOMAIN, ISSUE_FREQUENCY_MISSING)

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
    """Löscht gespeicherte Daten, wenn ein Eintrag entfernt wird."""
    if entry_type(entry) == ENTRY_TYPE_ODL:
        await history_store(hass, entry.data[CONF_KENN]).async_remove()
        return
    ir.async_delete_issue(hass, DOMAIN, ISSUE_FREQUENCY_MISSING)
    await frequency_store(hass).async_remove()
