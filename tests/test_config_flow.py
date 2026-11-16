from custom_components.energy_wallet.config_flow import full_schema
from custom_components.energy_wallet.const import (
    CONF_DEPOSIT_EXPIRY_MONTHS, CONF_DEPOSIT_WARNING_DAYS, CONF_G12_DIST_DAY,
    CONF_G12_DIST_FIXED, CONF_G12_DIST_NIGHT, CONF_G12_SALE_DAY, CONF_G12_SALE_FIXED,
    CONF_G12_SALE_NIGHT,
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
