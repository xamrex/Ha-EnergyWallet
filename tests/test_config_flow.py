from custom_components.energy_wallet.config_flow import full_schema
from custom_components.energy_wallet.const import (
    CONF_DEPOSIT_EXPIRY_MONTHS, CONF_DEPOSIT_WARNING_DAYS, CONF_G12_DIST_DAY,
    CONF_G12_DIST_FIXED, CONF_G12_DIST_NIGHT, CONF_G12_SALE_DAY, CONF_G12_SALE_FIXED,
    CONF_G12_SALE_NIGHT, CONF_PRICE_OFFPEAK, CONF_PRICE_OFFPEAK_DEPO, CONF_PRICE_PEAK,
    CONF_PRICE_PEAK_DEPO,
)


def test_full_schema_has_deposit_and_g12_fields_with_defaults():
    schema = full_schema({})
    keys = {str(k): k for k in schema.schema}
    for key in (
        CONF_DEPOSIT_EXPIRY_MONTHS, CONF_DEPOSIT_WARNING_DAYS, CONF_G12_DIST_FIXED,
        CONF_G12_SALE_FIXED, CONF_G12_DIST_DAY, CONF_G12_DIST_NIGHT, CONF_G12_SALE_DAY,
        CONF_G12_SALE_NIGHT,
    ):
        assert key in keys, f"missing {key}"
    defaults = {str(k): k.default() for k in schema.schema if callable(getattr(k, "default", None))}
    assert defaults[CONF_DEPOSIT_EXPIRY_MONTHS] == 12
    assert defaults[CONF_DEPOSIT_WARNING_DAYS] == 60
    assert defaults[CONF_G12_DIST_FIXED] == 0


def test_full_schema_no_longer_has_the_redundant_legacy_price_fields():
    # price_peak/price_offpeak/price_peak_depo/price_offpeak_depo are now
    # derived from the G12 fields (manager.build_price_config) instead of
    # being entered directly - see test_manager_pricing.py.
    schema = full_schema({})
    keys = {str(k) for k in schema.schema}
    for key in (CONF_PRICE_PEAK, CONF_PRICE_OFFPEAK, CONF_PRICE_PEAK_DEPO, CONF_PRICE_OFFPEAK_DEPO):
        assert key not in keys, f"{key} should have been removed from the config form"


def test_monthly_selector_allows_arbitrary_decimal_precision():
    from custom_components.energy_wallet.config_flow import MONTHLY
    assert MONTHLY.config["step"] == "any"
