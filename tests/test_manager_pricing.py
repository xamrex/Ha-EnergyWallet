"""build_price_config derives peak/off-peak buy+deposit prices from the G12
breakdown, falling back to the legacy direct fields for config entries that
predate the G12 fields. Not executable in the offline sandbox (manager.py
imports homeassistant); reviewed manually, left here for CI / local pytest."""

from custom_components.energy_wallet.const import (
    CONF_G12_DIST_DAY, CONF_G12_DIST_NIGHT, CONF_G12_SALE_DAY, CONF_G12_SALE_NIGHT,
    CONF_PRICE_OFFPEAK, CONF_PRICE_OFFPEAK_DEPO, CONF_PRICE_PEAK, CONF_PRICE_PEAK_DEPO,
)
from custom_components.energy_wallet.manager import build_price_config


def test_build_price_config_derives_from_g12_fields_when_present():
    conf = {
        CONF_G12_DIST_DAY: 0.30,
        CONF_G12_DIST_NIGHT: 0.10,
        CONF_G12_SALE_DAY: 0.60,
        CONF_G12_SALE_NIGHT: 0.40,
    }
    prices = build_price_config(conf)
    assert prices.price_peak == 0.90  # dist_day + sale_day
    assert prices.price_offpeak == 0.50  # dist_night + sale_night
    assert prices.price_peak_depo == 0.60  # sale_day
    assert prices.price_offpeak_depo == 0.40  # sale_night


def test_build_price_config_falls_back_to_legacy_fields_when_g12_is_unset():
    # Upgrading a config entry created before the G12 fields existed must not
    # silently zero out its pricing until the user fills in the G12 breakdown.
    conf = {
        CONF_PRICE_PEAK: 0.95,
        CONF_PRICE_OFFPEAK: 0.55,
        CONF_PRICE_PEAK_DEPO: 0.62,
        CONF_PRICE_OFFPEAK_DEPO: 0.41,
    }
    prices = build_price_config(conf)
    assert prices.price_peak == 0.95
    assert prices.price_offpeak == 0.55
    assert prices.price_peak_depo == 0.62
    assert prices.price_offpeak_depo == 0.41


def test_build_price_config_prefers_g12_over_legacy_once_any_g12_field_is_set():
    conf = {
        CONF_PRICE_PEAK: 0.95,  # stale legacy value, should be ignored
        CONF_G12_DIST_DAY: 0.30,
        CONF_G12_SALE_DAY: 0.60,
    }
    prices = build_price_config(conf)
    assert prices.price_peak == 0.90
