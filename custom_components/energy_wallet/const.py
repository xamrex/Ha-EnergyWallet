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

PERIOD_TODAY = "today"
PERIOD_MONTH = "month"
PERIOD_YEAR = "year"
PERIODS = [PERIOD_TODAY, PERIOD_MONTH, PERIOD_YEAR]

SIGNAL_UPDATE = "energy_wallet_update_{}"

STORAGE_VERSION = 1

FRONTEND_URL = "/energy_wallet/energy-wallet-card.js"
