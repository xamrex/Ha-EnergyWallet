"""Energy Wallet: what grid energy cost you and what your exports earned."""

from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_COMPONENT_LOADED, Platform
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import DOMAIN, FRONTEND_URL
from .coordinator import RcePriceCoordinator
from .manager import WalletManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_SET_RCEM = "set_rcem"
SET_RCEM_SCHEMA = vol.Schema(
    {
        vol.Required("month"): vol.Match(r"^\d{4}-\d{2}$"),
        vol.Optional("price"): vol.Any(None, vol.Coerce(float)),
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_SET_BALANCE_YEAR = "set_balance_year"
SET_BALANCE_YEAR_SCHEMA = vol.Schema(
    {
        vol.Required("value"): vol.Coerce(float),
        vol.Optional("year"): vol.Match(r"^\d{4}$"),
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_CLEAR_BALANCE_YEAR = "clear_balance_year"
CLEAR_BALANCE_YEAR_SCHEMA = vol.Schema(
    {
        vol.Optional("year"): vol.Match(r"^\d{4}$"),
        vol.Optional("config_entry_id"): cv.string,
    }
)

type EnergyWalletConfigEntry = ConfigEntry[WalletManager]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    await _register_card(hass)

    async def set_rcem(call: ServiceCall) -> None:
        entry_id = call.data.get("config_entry_id")
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if entry_id in (None, entry.entry_id):
                entry.runtime_data.set_rcem(call.data["month"], call.data.get("price"))

    hass.services.async_register(DOMAIN, SERVICE_SET_RCEM, set_rcem, schema=SET_RCEM_SCHEMA)

    async def set_balance_year(call: ServiceCall) -> None:
        entry_id = call.data.get("config_entry_id")
        from homeassistant.util import dt as dt_util
        year = call.data.get("year") or dt_util.now().date().isoformat()[:4]
        value = call.data["value"]
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if entry_id in (None, entry.entry_id):
                entry.runtime_data.set_balance_year(year, value)

    hass.services.async_register(DOMAIN, SERVICE_SET_BALANCE_YEAR, set_balance_year, schema=SET_BALANCE_YEAR_SCHEMA)

    async def clear_balance_year(call: ServiceCall) -> None:
        entry_id = call.data.get("config_entry_id")
        from homeassistant.util import dt as dt_util
        year = call.data.get("year") or dt_util.now().date().isoformat()[:4]
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if entry_id in (None, entry.entry_id):
                entry.runtime_data.clear_balance_year(year)

    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_BALANCE_YEAR, clear_balance_year, schema=CLEAR_BALANCE_YEAR_SCHEMA
    )
    return True


async def _register_card(hass: HomeAssistant) -> None:
    """Serve the Lovelace card and load it on every dashboard."""
    card = Path(__file__).parent / "frontend" / "energy-wallet-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(FRONTEND_URL, str(card), True)])
    version = (await async_get_integration(hass, DOMAIN)).version
    url = f"{FRONTEND_URL}?v={version}"

    if "frontend" in hass.config.components:
        add_extra_js_url(hass, url)
        return

    @callback
    def _frontend_loaded(event: Event) -> None:
        if event.data.get("component") == "frontend":
            add_extra_js_url(hass, url)
            unsub()

    unsub = hass.bus.async_listen(EVENT_COMPONENT_LOADED, _frontend_loaded)


async def async_setup_entry(hass: HomeAssistant, entry: EnergyWalletConfigEntry) -> bool:
    coordinator = RcePriceCoordinator(hass)
    await coordinator.async_refresh()

    manager = WalletManager(hass, entry, coordinator)
    await manager.async_start()
    entry.runtime_data = manager

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EnergyWalletConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: EnergyWalletConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
