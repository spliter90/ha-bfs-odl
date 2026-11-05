"""Config-Flow der Integration BfS Ortsdosisleistung (ODL)."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.util.location import distance

from .api import BfsOdlApiError, BfsOdlStationNotFoundError, OdlStation
from .const import (
    CONF_KENN,
    CONF_PERCENT_ABOVE_MEAN,
    CONF_SCAN_INTERVAL,
    CONF_STATION_NAME,
    CONF_THRESHOLD,
    DEFAULT_PERCENT_ABOVE_MEAN,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_THRESHOLD,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    NEAREST_STATION_COUNT,
    SEARCH_RESULT_LIMIT,
    STATUS_IN_OPERATION,
    UNIT_USV_H,
)
from .coordinator import BfsOdlConfigEntry, async_get_api

_LOGGER = logging.getLogger(__name__)

CONF_QUERY = "query"
KENN_REGEX = re.compile(r"^\d{9}$")

DEFAULT_OPTIONS: dict[str, Any] = {
    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
    CONF_THRESHOLD: DEFAULT_THRESHOLD,
    CONF_PERCENT_ABOVE_MEAN: DEFAULT_PERCENT_ABOVE_MEAN,
}


def _status_suffix(station: OdlStation) -> str:
    if station.status in (None, STATUS_IN_OPERATION):
        return ""
    return f" [{station.site_status_text or station.status}]"


def search_stations(stations: list[OdlStation], query: str) -> list[OdlStation]:
    """Sucht Sonden nach Name, Postleitzahl oder Teil der Kennung."""
    needle = query.strip().casefold()
    if not needle:
        return []
    matches = [
        station
        for station in stations
        if needle in station.name.casefold()
        or (station.plz is not None and station.plz.startswith(needle))
        or station.kenn.startswith(needle)
    ]
    matches.sort(
        key=lambda s: (not s.name.casefold().startswith(needle), s.name.casefold())
    )
    return matches[:SEARCH_RESULT_LIMIT]


class BfsOdlConfigFlow(ConfigFlow, domain=DOMAIN):
    """Einrichtung einer ODL-Sonde."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialisiert den Flow."""
        self._candidates: dict[str, OdlStation] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: BfsOdlConfigEntry) -> BfsOdlOptionsFlow:
        """Optionen-Flow."""
        return BfsOdlOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Auswahl: Standort oder Kennung/Name."""
        return self.async_show_menu(step_id="user", menu_options=["location", "manual"])

    async def async_step_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Die nächsten Sonden zum Home-Assistant-Standort."""
        if user_input is not None:
            return await self._async_create(self._candidates[user_input[CONF_KENN]])

        try:
            stations = await async_get_api(self.hass).async_get_all_stations()
        except BfsOdlApiError as err:
            _LOGGER.debug("Sondenliste nicht abrufbar: %s", err)
            return self.async_abort(reason="cannot_connect")

        home_lat = self.hass.config.latitude
        home_lon = self.hass.config.longitude
        with_distance: list[tuple[float, OdlStation]] = []
        for station in stations:
            if station.latitude is None or station.longitude is None:
                continue
            meters = distance(home_lat, home_lon, station.latitude, station.longitude)
            if meters is not None:
                with_distance.append((meters / 1000, station))
        with_distance.sort(key=lambda item: item[0])
        nearest = with_distance[:NEAREST_STATION_COUNT]
        if not nearest:
            return self.async_abort(reason="no_stations")

        self._candidates = {station.kenn: station for _, station in nearest}
        options = [
            SelectOptionDict(
                value=station.kenn,
                label=(
                    f"{station.name} ({station.kenn}) – {km:.1f} km"
                    f"{_status_suffix(station)}"
                ),
            )
            for km, station in nearest
        ]
        return self.async_show_form(
            step_id="location",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_KENN, default=options[0]["value"]
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    )
                }
            ),
            description_placeholders={
                "latitude": f"{home_lat:.4f}",
                "longitude": f"{home_lon:.4f}",
            },
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Kennung oder Namenssuche."""
        errors: dict[str, str] = {}
        if user_input is not None:
            query = str(user_input[CONF_QUERY]).strip()
            api = async_get_api(self.hass)
            if KENN_REGEX.match(query):
                try:
                    station = await api.async_get_station(query)
                except BfsOdlStationNotFoundError:
                    errors["base"] = "station_not_found"
                except BfsOdlApiError:
                    errors["base"] = "cannot_connect"
                else:
                    return await self._async_create(station)
            elif len(query) < 2:
                errors["base"] = "query_too_short"
            else:
                try:
                    stations = await api.async_get_all_stations()
                except BfsOdlApiError:
                    errors["base"] = "cannot_connect"
                else:
                    matches = search_stations(stations, query)
                    if not matches:
                        errors["base"] = "no_results"
                    elif len(matches) == 1:
                        return await self._async_create(matches[0])
                    else:
                        self._candidates = {s.kenn: s for s in matches}
                        return await self.async_step_select()

        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_QUERY): TextSelector()}), user_input
            ),
            errors=errors,
        )

    async def async_step_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Auswahl aus mehreren Suchtreffern."""
        if user_input is not None:
            return await self._async_create(self._candidates[user_input[CONF_KENN]])

        options = [
            SelectOptionDict(
                value=station.kenn,
                label=(
                    f"{station.name} ({station.kenn})"
                    f"{f', PLZ {station.plz}' if station.plz else ''}"
                    f"{_status_suffix(station)}"
                ),
            )
            for station in self._candidates.values()
        ]
        return self.async_show_form(
            step_id="select",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_KENN): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
            description_placeholders={"count": str(len(options))},
        )

    async def _async_create(self, station: OdlStation) -> ConfigFlowResult:
        """Legt den Eintrag an (eine Sonde pro Eintrag)."""
        await self.async_set_unique_id(station.kenn)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=station.name,
            data={CONF_KENN: station.kenn, CONF_STATION_NAME: station.name},
            options=dict(DEFAULT_OPTIONS),
        )


class BfsOdlOptionsFlow(OptionsFlowWithReload):
    """Optionen: Abrufintervall und Alarmschwellen."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optionen bearbeiten."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_THRESHOLD: float(user_input[CONF_THRESHOLD]),
                    CONF_PERCENT_ABOVE_MEAN: float(user_input[CONF_PERCENT_ABOVE_MEAN]),
                }
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_THRESHOLD): NumberSelector(
                    NumberSelectorConfig(
                        min=0.01,
                        max=100,
                        step=0.001,
                        unit_of_measurement=UNIT_USV_H,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_PERCENT_ABOVE_MEAN): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=1000,
                        step=1,
                        unit_of_measurement="%",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, {**DEFAULT_OPTIONS, **self.config_entry.options}
            ),
        )
