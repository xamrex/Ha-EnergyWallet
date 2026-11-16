"""End-to-end tests for the deposit FIFO / G12 / kWh-remaining sensors and services.

Uses its OWN config fixture (BASE) matching the *current* config_flow.py schema -
tests/test_integration.py predates that schema (seller/tariff_group/dso/... fields that
no longer exist anywhere in const.py/config_flow.py) and was already stale before this
migration; this file does not build on it. See the plan's "Sandbox test-execution
constraint" section - these tests could not be executed in the agent's sandbox (no
network to install pytest-homeassistant-custom-component) and must be run for real via
`pytest tests/test_deposit_integration.py -v` on a machine with requirements_test.txt
installed, or in CI.
"""

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.energy_wallet.const import DOMAIN
from custom_components.energy_wallet.pse import PSE_RCE_URL

IMP = "sensor.deye_inverter_totalenergybuy"
EXP = "sensor.deye_inverter_totalenergysell"
SELL = "sensor.fake_sell_price"
ATTRS = {"unit_of_measurement": "kWh", "state_class": "total", "device_class": "energy"}

BASE = {
    "import_entity": IMP,
    "export_entity": EXP,
    "peak_hours": "6:00-13:00,15:00-22:00",
    "offpeak_hours": "13:00-15:00,22:00-6:00",
    "price_peak": 0.9,
    "price_offpeak": 0.6,
    "price_peak_depo": 0.3,
    "price_offpeak_depo": 0.2,
    "sell_price_entity": SELL,
    "monthly_fee": 20.0,
}


def value(hass: HomeAssistant, entry: MockConfigEntry, key: str):
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")
    return hass.states.get(entity_id)


@pytest.fixture
async def setup_states(hass: HomeAssistant):
    await hass.config.async_set_time_zone("Europe/Warsaw")
    hass.states.async_set(IMP, "16.5", {**ATTRS, "friendly_name": "Deye Inverter TotalEnergyBuy"})
    hass.states.async_set(EXP, "0.2", {**ATTRS, "friendly_name": "Deye Inverter TotalEnergySell"})
    hass.states.async_set(SELL, "0.4", {"friendly_name": "Fake Sell Price"})


async def _setup_entry(hass: HomeAssistant, aioclient_mock, data: dict) -> MockConfigEntry:
    aioclient_mock.get(PSE_RCE_URL, json={"value": []})
    entry = MockConfigEntry(domain=DOMAIN, data=data, title="Energy Wallet")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_deposit_sensors_reflect_manual_month(hass: HomeAssistant, setup_states, aioclient_mock) -> None:
    entry = await _setup_entry(hass, aioclient_mock, BASE)

    manager = entry.runtime_data
    manager.set_deposit_starting_block("2025-12", 100.0)
    manager.set_deposit_month("2026-01", export=5.40, import_=5.40)
    manager.set_deposit_month("2026-06", export=500.0, import_=0.0)
    manager.set_deposit_month("2026-07", export=0.0, import_=30.0)
    await hass.async_block_till_done()

    # Verified against Wallet.deposit_buckets() directly (see the plan's pure-logic harness):
    # buckets end up [2025-12: 64.6, 2026-01: 5.4, 2026-06: 500.0] -> total 570.0.
    expected_total = 570.0
    blocks = value(hass, entry, "deposit_blocks")
    assert blocks.attributes["suma_pozostalo"] == pytest.approx(expected_total)
    oldest = value(hass, entry, "deposit_oldest_block")
    assert oldest.attributes["pochodzenie"] == "2025-12"
    actual = value(hass, entry, "deposit_actual_balance")
    assert float(actual.state) == pytest.approx(expected_total)


async def test_g12_and_kwh_remaining_sensors(hass: HomeAssistant, setup_states, aioclient_mock) -> None:
    data = {**BASE, "g12_distribution_fixed": 34.33, "g12_sale_day": 0.6221, "g12_sale_night": 0.4716}
    entry = await _setup_entry(hass, aioclient_mock, data)

    assert float(value(hass, entry, "g12_distribution_fixed").state) == pytest.approx(34.33)
    assert float(value(hass, entry, "g12_sale_fixed").state) == pytest.approx(0.0)  # default when omitted

    entry.runtime_data.set_deposit_month("2026-01", export=62.21, import_=0.0)
    await hass.async_block_till_done()
    # deposit_actual_balance() is 62.21 (one untouched bucket, no consumption) - both prices are
    # configured in `data` above, so both kWh sensors must compute, not read as "unknown".
    kwh_day = value(hass, entry, "deposit_kwh_remaining_day")
    assert float(kwh_day.state) == pytest.approx(round(62.21 / 0.6221, 1))  # == 100.0
    kwh_night = value(hass, entry, "deposit_kwh_remaining_night")
    assert float(kwh_night.state) == pytest.approx(round(62.21 / 0.4716, 1))  # == 131.9


async def test_kwh_remaining_is_none_without_price(hass: HomeAssistant, setup_states, aioclient_mock) -> None:
    entry = await _setup_entry(hass, aioclient_mock, BASE)  # no g12_sale_day/night -> default 0
    assert value(hass, entry, "deposit_kwh_remaining_day").state == "unknown"
