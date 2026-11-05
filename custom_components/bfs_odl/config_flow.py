"""Config-Flow der Integration Umwelt & Netz (BfS / Energy-Charts)."""

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
    BooleanSelector,
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
    CONF_ENABLE_FREQUENCY,
    CONF_ENABLE_POWER,
    CONF_ENABLE_SIGNAL,
    CONF_ENTRY_TYPE,
    CONF_FREQUENCY_INTERVAL,
    CONF_FREQUENCY_THRESHOLD,
    CONF_KENN,
    CONF_PERCENT_ABOVE_MEAN,
    CONF_POSTAL_CODE,
    CONF_POWER_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SIGNAL_INTERVAL,
    CONF_STATION_NAME,
    CONF_THRESHOLD,
    DEFAULT_FREQUENCY_INTERVAL,
    DEFAULT_FREQUENCY_THRESHOLD,
    DEFAULT_PERCENT_ABOVE_MEAN,
    DEFAULT_POWER_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SIGNAL_INTERVAL,
    DEFAULT_THRESHOLD,
    DOMAIN,
    ENTRY_TYPE_GRID,
    ENTRY_TYPE_ODL,
    GRID_UNIQUE_ID,
    MAX_FREQUENCY_INTERVAL,
    MAX_FREQUENCY_THRESHOLD,
    MAX_POWER_INTERVAL,
    MAX_SCAN_INTERVAL,
    MAX_SIGNAL_INTERVAL,
    MIN_FREQUENCY_INTERVAL,
    MIN_FREQUENCY_THRESHOLD,
    MIN_POWER_INTERVAL,
    MIN_SCAN_INTERVAL,
    MIN_SIGNAL_INTERVAL,
    NEAREST_STATION_COUNT,
    SEARCH_RESULT_LIMIT,
    STATUS_IN_OPERATION,
    UNIT_USV_H,
)
from .coordinator import BfsOdlConfigEntry, async_get_api
from .energy_charts import EnergyChartsConnectionError, EnergyChartsError
from .grid_coordinator import async_get_energy_charts_api

_LOGGER = logging.getLogger(__name__)

CONF_QUERY = "query"
KENN_REGEX = re.compile(r"^\d{9}$")
POSTAL_CODE_REGEX = re.compile(r"^\d{5}$")
GRID_TITLE = "Stromnetz Deutschland"

DEFAULT_OPTIONS: dict[str, Any] = {
    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
    CONF_THRESHOLD: DEFAULT_THRESHOLD,
    CONF_PERCENT_ABOVE_MEAN: DEFAULT_PERCENT_ABOVE_MEAN,
}

DEFAULT_GRID_OPTIONS: dict[str, Any] = {
    CONF_ENABLE_SIGNAL: True,
    CONF_ENABLE_FREQUENCY: True,
    CONF_ENABLE_POWER: True,
    CONF_SIGNAL_INTERVAL: DEFAULT_SIGNAL_INTERVAL,
    CONF_FREQUENCY_INTERVAL: DEFAULT_FREQUENCY_INTERVAL,
    CONF_POWER_INTERVAL: DEFAULT_POWER_INTERVAL,
    CONF_FREQUENCY_THRESHOLD: DEFAULT_FREQUENCY_THRESHOLD,
}

GRID_TOGGLES = (CONF_ENABLE_SIGNAL, CONF_ENABLE_FREQUENCY, CONF_ENABLE_POWER)


def _number(
    minimum: float, maximum: float, unit: str, step: float = 1
) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=NumberSelectorMode.BOX,
        )
    )


def _validate_grid_input(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not POSTAL_CODE_REGEX.match(str(user_input.get(CONF_POSTAL_CODE, "")).strip()):
        errors[CONF_POSTAL_CODE] = "invalid_postal_code"
    if not any(user_input.get(toggle) for toggle in GRID_TOGGLES):
        errors["base"] = "nothing_enabled"
    return errors


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
    """Einrichtung einer ODL-Sonde oder des Stromnetzes."""

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
        """Auswahl der Datenquelle."""
        return self.async_show_menu(step_id="user", menu_options=["odl", "grid"])

    async def async_step_odl(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Strahlungsmesssonde: Standort oder Kennung/Name."""
        return self.async_show_menu(step_id="odl", menu_options=["location", "manual"])

    async def async_step_grid(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Stromnetz Deutschland (Energy-Charts)."""
        await self.async_set_unique_id(GRID_UNIQUE_ID)
        self._abort_if_unique_id_configured(error="grid_already_configured")

        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_grid_input(user_input)
            postal_code = str(user_input.get(CONF_POSTAL_CODE, "")).strip()
            if not errors and user_input.get(CONF_ENABLE_SIGNAL):
                try:
                    await async_get_energy_charts_api(self.hass).async_get_signal(
                        postal_code
                    )
                except EnergyChartsConnectionError:
                    errors["base"] = "cannot_connect_grid"
                except EnergyChartsError as err:
                    _LOGGER.debug("Netzampel für PLZ %s: %s", postal_code, err)
                    errors["base"] = "signal_unavailable"
            if not errors:
                options = dict(DEFAULT_GRID_OPTIONS)
                options.update(
                    {toggle: bool(user_input[toggle]) for toggle in GRID_TOGGLES}
                )
                options[CONF_POSTAL_CODE] = postal_code
                return self.async_create_entry(
                    title=GRID_TITLE,
                    data={
                        CONF_ENTRY_TYPE: ENTRY_TYPE_GRID,
                        CONF_POSTAL_CODE: postal_code,
                    },
                    options=options,
                )

        suggested = user_input or {
            CONF_POSTAL_CODE: await self._async_suggest_postal_code(),
            **{toggle: True for toggle in GRID_TOGGLES},
        }
        schema = vol.Schema(
            {
                vol.Required(CONF_POSTAL_CODE): TextSelector(),
                vol.Required(CONF_ENABLE_SIGNAL): BooleanSelector(),
                vol.Required(CONF_ENABLE_FREQUENCY): BooleanSelector(),
                vol.Required(CONF_ENABLE_POWER): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="grid",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )

    async def _async_suggest_postal_code(self) -> str:
        """PLZ-Vorschlag.

        Home Assistant speichert keine Postleitzahl. Als Vorschlag dient daher die
        PLZ der ODL-Messsonde, die dem Home-Assistant-Standort am nächsten liegt.
        """
        try:
            stations = await async_get_api(self.hass).async_get_all_stations()
        except BfsOdlApiError:
            return ""
        best: tuple[float, str] | None = None
        for station in stations:
            if station.latitude is None or station.longitude is None or not station.plz:
                continue
            meters = distance(
                self.hass.config.latitude,
                self.hass.config.longitude,
                station.latitude,
                station.longitude,
            )
            if meters is not None and (best is None or meters < best[0]):
                best = (meters, station.plz)
        return best[1] if best else ""

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
    """Optionen: Abrufintervalle und Alarmschwellen."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optionen bearbeiten (ODL-Sonde) bzw. zum Stromnetz weiterleiten."""
        if (
            self.config_entry.data.get(CONF_ENTRY_TYPE, ENTRY_TYPE_ODL)
            == ENTRY_TYPE_GRID
        ):
            return await self.async_step_grid(user_input)
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

    async def async_step_grid(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optionen des Stromnetzes."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_grid_input(user_input)
            if not errors:
                return self.async_create_entry(
                    data={
                        CONF_POSTAL_CODE: str(user_input[CONF_POSTAL_CODE]).strip(),
                        **{t: bool(user_input[t]) for t in GRID_TOGGLES},
                        CONF_SIGNAL_INTERVAL: int(user_input[CONF_SIGNAL_INTERVAL]),
                        CONF_FREQUENCY_INTERVAL: int(
                            user_input[CONF_FREQUENCY_INTERVAL]
                        ),
                        CONF_POWER_INTERVAL: int(user_input[CONF_POWER_INTERVAL]),
                        CONF_FREQUENCY_THRESHOLD: float(
                            user_input[CONF_FREQUENCY_THRESHOLD]
                        ),
                    }
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_POSTAL_CODE): TextSelector(),
                vol.Required(CONF_ENABLE_SIGNAL): BooleanSelector(),
                vol.Required(CONF_ENABLE_FREQUENCY): BooleanSelector(),
                vol.Required(CONF_ENABLE_POWER): BooleanSelector(),
                vol.Required(CONF_SIGNAL_INTERVAL): _number(
                    MIN_SIGNAL_INTERVAL, MAX_SIGNAL_INTERVAL, "min"
                ),
                vol.Required(CONF_FREQUENCY_INTERVAL): _number(
                    MIN_FREQUENCY_INTERVAL, MAX_FREQUENCY_INTERVAL, "s"
                ),
                vol.Required(CONF_POWER_INTERVAL): _number(
                    MIN_POWER_INTERVAL, MAX_POWER_INTERVAL, "min"
                ),
                vol.Required(CONF_FREQUENCY_THRESHOLD): _number(
                    MIN_FREQUENCY_THRESHOLD, MAX_FREQUENCY_THRESHOLD, "mHz"
                ),
            }
        )
        current = {
            **DEFAULT_GRID_OPTIONS,
            CONF_POSTAL_CODE: self.config_entry.data.get(CONF_POSTAL_CODE, ""),
            **self.config_entry.options,
        }
        return self.async_show_form(
            step_id="grid",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or current
            ),
            errors=errors,
        )
