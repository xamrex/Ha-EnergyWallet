"""Constants for the Energy Wallet integration."""

from __future__ import annotations

DOMAIN = "energy_wallet"

CONF_IMPORT_ENTITY = "import_entity"
CONF_EXPORT_ENTITY = "export_entity"

# Simplified config - peak/off-peak hours
CONF_PEAK_HOURS = "peak_hours"
CONF_OFFPEAK_HOURS = "offpeak_hours"

# Simplified config - buy prices (gross)
CONF_PRICE_PEAK = "price_peak"
CONF_PRICE_OFFPEAK = "price_offpeak"

# Deposit prices for invoice correction
CONF_PRICE_PEAK_DEPO = "price_peak_depo"
CONF_PRICE_OFFPEAK_DEPO = "price_offpeak_depo"

# Sell price entity
CONF_SELL_PRICE_ENTITY = "sell_price_entity"

# Monthly constant fee (gross)
CONF_MONTHLY_FEE = "monthly_fee"

# Deposit FIFO (net-billing deposit block tracking)
CONF_DEPOSIT_EXPIRY_MONTHS = "deposit_expiry_months"
CONF_DEPOSIT_WARNING_DAYS = "deposit_warning_days"

# Taryfa G12 - static tariff cost breakdown (gross, PLN / PLN per kWh)
CONF_G12_DIST_FIXED = "g12_distribution_fixed"
CONF_G12_SALE_FIXED = "g12_sale_fixed"
CONF_G12_DIST_DAY = "g12_distribution_day"
CONF_G12_DIST_NIGHT = "g12_distribution_night"
CONF_G12_SALE_DAY = "g12_sale_day"
CONF_G12_SALE_NIGHT = "g12_sale_night"

PERIOD_TODAY = "today"
PERIOD_MONTH = "month"
PERIOD_YEAR = "year"
PERIODS = [PERIOD_TODAY, PERIOD_MONTH, PERIOD_YEAR]

SIGNAL_UPDATE = "energy_wallet_update_{}"

STORAGE_VERSION = 1

FRONTEND_URL = "/energy_wallet/energy-wallet-card.js"
