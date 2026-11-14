"""Glue between Home Assistant state changes and the Wallet."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_EXPORT_ENTITY,
    CONF_IMPORT_ENTITY,
    CONF_MONTHLY_FEE,
    CONF_OFFPEAK_HOURS,
    CONF_PEAK_HOURS,
    CONF_PRICE_OFFPEAK,
    CONF_PRICE_OFFPEAK_DEPO,
    CONF_PRICE_PEAK,
    CONF_PRICE_PEAK_DEPO,
    CONF_SELL_PRICE_ENTITY,
    DOMAIN,
    PERIOD_MONTH,
    PERIOD_TODAY,
    PERIOD_YEAR,
    SIGNAL_UPDATE,
    STORAGE_VERSION,
)
from .coordinator import RcePriceCoordinator
from .detect import ENERGY_UNITS
from .tariff import PriceConfig, parse_windows
from .wallet import EXPORT, IMPORT, Wallet

_LOGGER = logging.getLogger(__name__)

SAVE_DELAY = 60
DEFAULT_MAX_POWER_KW = 50.0


def build_price_config(conf: dict[str, Any]) -> PriceConfig:
    return PriceConfig(
        peak_windows=parse_windows(conf.get(CONF_PEAK_HOURS, "")),
        offpeak_windows=parse_windows(conf.get(CONF_OFFPEAK_HOURS, "")),
        price_peak=float(conf.get(CONF_PRICE_PEAK) or 0),
        price_offpeak=float(conf.get(CONF_PRICE_OFFPEAK) or 0),
        price_peak_depo=float(conf.get(CONF_PRICE_PEAK_DEPO) or 0),
        price_offpeak_depo=float(conf.get(CONF_PRICE_OFFPEAK_DEPO) or 0),
        monthly_fee=float(conf.get(CONF_MONTHLY_FEE) or 0),
    )


class WalletManager:
    """Owns the Wallet for one config entry and keeps it fed."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: RcePriceCoordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.conf: dict[str, Any] = {**entry.data, **entry.options}
        self.prices = build_price_config(self.conf)
        self.wallet = Wallet()
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._unsubs: list[Callable[[], None]] = []
        self._sell_price: float | None = None

    @property
    def sell_price_entity(self) -> str | None:
        return self.conf.get(CONF_SELL_PRICE_ENTITY)

    async def async_start(self) -> None:
        self.wallet = Wallet(await self._store.async_load())
        entities = {IMPORT: self.conf[CONF_IMPORT_ENTITY], EXPORT: self.conf[CONF_EXPORT_ENTITY]}
        for kind, entity_id in entities.items():
            if state := self.hass.states.get(entity_id):
                self._ingest(kind, state)
            self._unsubs.append(
                async_track_state_change_event(self.hass, [entity_id], self._make_listener(kind))
            )

        # Track sell price entity
        sell_entity = self.sell_price_entity
        if sell_entity:
            if state := self.hass.states.get(sell_entity):
                self._update_sell_price(state)
            self._unsubs.append(
                async_track_state_change_event(self.hass, [sell_entity], self._on_sell_price_change)
            )

        self._unsubs.append(self.coordinator.async_add_listener(self._on_prices))
        self._unsubs.append(
            async_track_time_change(self.hass, self._on_tick, minute=[0, 15, 30, 45], second=2)
        )
        self._on_prices()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._store.async_save(self.wallet.as_dict())

    def _make_listener(self, kind: str) -> Callable[[Event[EventStateChangedData]], None]:
        @callback
        def _listener(event: Event[EventStateChangedData]) -> None:
            if (state := event.data["new_state"]) is not None:
                self._ingest(kind, state)

        return _listener

    @callback
    def _on_sell_price_change(self, event: Event[EventStateChangedData]) -> None:
        if (state := event.data["new_state"]) is not None:
            self._update_sell_price(state)
            self._settle()

    def _update_sell_price(self, state: State) -> None:
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
            return
        try:
            self._sell_price = float(state.state)
        except ValueError:
            pass

    def _ingest(self, kind: str, state: State) -> None:
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
            return
        try:
            value = float(state.state)
        except ValueError:
            return
        factor = ENERGY_UNITS.get(state.attributes.get(ATTR_UNIT_OF_MEASUREMENT, "kWh"))
        if factor is None:
            _LOGGER.warning("%s has unsupported unit, expected Wh/kWh/MWh", state.entity_id)
            return
        now = dt_util.utcnow()
        local_now = dt_util.as_local(now)
        delta, rejected = self.wallet.ingest_reading(
            kind,
            value * factor,
            now,
            resets_allowed=state.attributes.get("state_class") == "total_increasing",
            max_power_kw=DEFAULT_MAX_POWER_KW,
        )
        if rejected:
            _LOGGER.warning("Ignoring implausible jump of %s to %s", state.entity_id, state.state)
        if delta > 0:
            zone = self.prices.zone(local_now)
            self.wallet.add_energy(kind, delta, now, local_now.date().isoformat(), zone=zone)
            self._settle()

    def _cost_fn(self, start: datetime) -> float | None:
        """Buy price - gross, directly from config."""
        local = dt_util.as_local(start)
        return self.prices.buy_price(local)

    def _rev_fn(self, start: datetime) -> float | None:
        """Sell price - from the external sensor entity."""
        return self._sell_price

    def _settle(self) -> None:
        self.wallet.settle(self._cost_fn, self._rev_fn, dt_util.utcnow())
        self._store.async_delay_save(self.wallet.as_dict, SAVE_DELAY)
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.entry.entry_id))

    @callback
    def _on_prices(self) -> None:
        tz = dt_util.get_default_time_zone()
        today = dt_util.now().date()
        for offset in range(-6, 2):
            day = today + timedelta(days=offset)
            if (mean := self.coordinator.table.day_mean(day, tz)) is not None:
                self.wallet.record_rce_day(day.isoformat(), mean)
        self.wallet.prune(today.isoformat())
        self._settle()

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self._settle()

    def set_rcem(self, month: str, price: float | None) -> None:
        if price is None:
            self.wallet.rcem_override.pop(month, None)
        else:
            self.wallet.rcem_override[month] = price
        self._settle()

    def set_balance_year(self, year: str, value: float) -> None:
        """Set the yearly balance to a specific value."""
        self.wallet.set_balance_year_override(year, value)
        self._settle()

    def clear_balance_year(self, year: str) -> None:
        """Clear the yearly balance override."""
        self.wallet.clear_balance_year_override(year)
        self._settle()

    # Values for entities

    def period_prefix(self, period: str) -> str:
        today = dt_util.now().date().isoformat()
        return {PERIOD_TODAY: today, PERIOD_MONTH: today[:7], PERIOD_YEAR: today[:4]}[period]

    def summary(self, period: str) -> dict[str, float]:
        return self.wallet.summary(self.period_prefix(period))

    def lifetime(self) -> dict[str, float]:
        totals = self.wallet.totals
        return {"cost": totals.get("cost", 0.0), "rev": totals.get("rev", 0.0)}

    def current_buy_price(self) -> float | None:
        local_now = dt_util.now()
        return self.prices.buy_price(local_now)

    def current_sell_price(self) -> float | None:
        return self._sell_price

    def invoice_no_correction(self) -> float:
        """Monthly invoice without deposit correction.

        = monthly_fee + purchase_cost_this_month
        """
        month_summary = self.summary(PERIOD_MONTH)
        return self.prices.monthly_fee + month_summary["cost"]

    def invoice_with_deposit(self) -> float:
        """Monthly invoice with deposit correction.

        = monthly_fee + purchase_cost_this_month
          - (imp_peak_month * price_peak_depo)
          - (imp_offpeak_month * price_offpeak_depo)
        """
        month_summary = self.summary(PERIOD_MONTH)
        base = self.prices.monthly_fee + month_summary["cost"]
        depo_peak = month_summary.get("imp_peak", 0.0) * self.prices.price_peak_depo
        depo_offpeak = month_summary.get("imp_offpeak", 0.0) * self.prices.price_offpeak_depo
        return base - depo_peak - depo_offpeak

    def deposit_correction_total(self) -> float:
        """Total deposit correction amount for this month."""
        month_summary = self.summary(PERIOD_MONTH)
        depo_peak = month_summary.get("imp_peak", 0.0) * self.prices.price_peak_depo
        depo_offpeak = month_summary.get("imp_offpeak", 0.0) * self.prices.price_offpeak_depo
        return depo_peak + depo_offpeak

    def balance_year_value(self) -> float:
        """Yearly balance, possibly overridden."""
        year_key = dt_util.now().date().isoformat()[:4]
        if year_key in self.wallet.balance_year_override:
            return self.wallet.balance_year_override[year_key]
        year_summary = self.summary(PERIOD_YEAR)
        return year_summary["balance"]

    def price_forecast(self) -> list[dict[str, Any]]:
        """Buy and sell prices from now until the end of known data."""
        now = dt_util.utcnow()
        out = []
        for start, end, rce in self.coordinator.table.between(now - timedelta(hours=1), now + timedelta(days=2)):
            begin = dt_util.utc_from_timestamp(start)
            local = dt_util.as_local(begin)
            buy = self.prices.buy_price(local)
            out.append(
                {
                    "start": local.isoformat(),
                    "end": dt_util.as_local(dt_util.utc_from_timestamp(end)).isoformat(),
                    "buy": round(buy, 4) if buy is not None else None,
                    "sell": round(self._sell_price, 4) if self._sell_price is not None else None,
                }
            )
        return out
