"""Config and options flow for Energy Wallet."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_DEPOSIT_EXPIRY_MONTHS,
    CONF_DEPOSIT_WARNING_DAYS,
    CONF_EXPORT_ENTITY,
    CONF_G12_DIST_DAY,
    CONF_G12_DIST_FIXED,
    CONF_G12_DIST_NIGHT,
    CONF_G12_SALE_DAY,
    CONF_G12_SALE_FIXED,
    CONF_G12_SALE_NIGHT,
    CONF_IMPORT_ENTITY,
    CONF_MONTHLY_FEE,
    CONF_OFFPEAK_HOURS,
    CONF_PEAK_HOURS,
    CONF_SELL_PRICE_ENTITY,
    DOMAIN,
)
from .tariff import parse_windows

ENERGY_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="energy")
)
ENTITY_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor")
)
PRICE = selector.NumberSelector(
    selector.NumberSelectorConfig(min=0, max=10, step="any", mode="box", unit_of_measurement="PLN/kWh")
)
MONTHLY = selector.NumberSelector(
    selector.NumberSelectorConfig(min=0, max=10000, step="any", mode="box", unit_of_measurement="zł")
)
HOURS_TEXT = selector.TextSelector()
MONTHS_INT = selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=36, step=1, mode="box"))
DAYS_INT = selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=365, step=1, mode="box"))


def _default(value: Any) -> dict[str, Any]:
    """Only pass a default when one is known."""
    return {} if value is None or value == "" else {"default": value}


def full_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Single-step schema matching the user's desired UI."""
    return vol.Schema(
        {
            vol.Required(CONF_IMPORT_ENTITY, **_default(defaults.get(CONF_IMPORT_ENTITY))): ENERGY_SENSOR,
            vol.Required(CONF_EXPORT_ENTITY, **_default(defaults.get(CONF_EXPORT_ENTITY))): ENERGY_SENSOR,
            vol.Required(CONF_PEAK_HOURS, **_default(defaults.get(CONF_PEAK_HOURS, "6:00-13:00,15:00-22:00"))): HOURS_TEXT,
            vol.Required(CONF_OFFPEAK_HOURS, **_default(defaults.get(CONF_OFFPEAK_HOURS, "13:00-15:00,22:00-6:00"))): HOURS_TEXT,
            vol.Required(CONF_SELL_PRICE_ENTITY, **_default(defaults.get(CONF_SELL_PRICE_ENTITY))): ENTITY_SELECTOR,
            vol.Required(CONF_MONTHLY_FEE, **_default(defaults.get(CONF_MONTHLY_FEE, 0))): MONTHLY,
            vol.Optional(CONF_DEPOSIT_EXPIRY_MONTHS, **_default(defaults.get(CONF_DEPOSIT_EXPIRY_MONTHS, 12))): MONTHS_INT,
            vol.Optional(CONF_DEPOSIT_WARNING_DAYS, **_default(defaults.get(CONF_DEPOSIT_WARNING_DAYS, 60))): DAYS_INT,
            vol.Optional(CONF_G12_DIST_FIXED, **_default(defaults.get(CONF_G12_DIST_FIXED, 0))): MONTHLY,
            vol.Optional(CONF_G12_SALE_FIXED, **_default(defaults.get(CONF_G12_SALE_FIXED, 0))): MONTHLY,
            vol.Optional(CONF_G12_DIST_DAY, **_default(defaults.get(CONF_G12_DIST_DAY, 0))): PRICE,
            vol.Optional(CONF_G12_DIST_NIGHT, **_default(defaults.get(CONF_G12_DIST_NIGHT, 0))): PRICE,
            vol.Optional(CONF_G12_SALE_DAY, **_default(defaults.get(CONF_G12_SALE_DAY, 0))): PRICE,
            vol.Optional(CONF_G12_SALE_NIGHT, **_default(defaults.get(CONF_G12_SALE_NIGHT, 0))): PRICE,
        }
    )


def validate_config(user_input: dict[str, Any]) -> dict[str, str]:
    """Validate user input."""
    errors: dict[str, str] = {}
    if user_input.get(CONF_IMPORT_ENTITY) == user_input.get(CONF_EXPORT_ENTITY):
        errors["base"] = "same_entity"
    for key in (CONF_PEAK_HOURS, CONF_OFFPEAK_HOURS):
        try:
            parse_windows(user_input.get(key))
        except ValueError:
            errors[key] = "invalid_hours"
    return errors


class EnergyWalletConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a wallet for one grid connection."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_config(user_input)
            if not errors:
                await self.async_set_unique_id(user_input[CONF_IMPORT_ENTITY])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Energy Wallet", data=user_input)

        defaults = user_input or {}
        return self.async_show_form(
            step_id="user",
            data_schema=full_schema(defaults),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EnergyWalletOptionsFlow()


class EnergyWalletOptionsFlow(OptionsFlow):
    """Change settings after setup."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = validate_config(user_input)
            if not errors:
                return self.async_create_entry(data=user_input)
            current.update(user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=full_schema(current),
            errors=errors,
        )
