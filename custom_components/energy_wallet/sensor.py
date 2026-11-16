"""Sensors exposing purchase cost, sale revenue, balance, and invoices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import deposit as _deposit
from .const import (
    CONF_G12_DIST_DAY,
    CONF_G12_DIST_FIXED,
    CONF_G12_DIST_NIGHT,
    CONF_G12_SALE_DAY,
    CONF_G12_SALE_FIXED,
    CONF_G12_SALE_NIGHT,
    DOMAIN,
    PERIODS,
    SIGNAL_UPDATE,
)
from .manager import WalletManager
from homeassistant.util import dt as dt_util

CURRENCY = "PLN"
PRICE_UNIT = "PLN/kWh"


@dataclass(frozen=True, kw_only=True)
class WalletSensorDescription(SensorEntityDescription):
    value_fn: Callable[[WalletManager], float | None]
    attrs_fn: Callable[[WalletManager], dict[str, Any]] | None = None
    role: str
    period: str | None = None


def _period_descriptions(period: str) -> list[WalletSensorDescription]:
    def summary(manager: WalletManager) -> dict[str, float]:
        return manager.summary(period)

    money = {
        "device_class": SensorDeviceClass.MONETARY,
        "native_unit_of_measurement": CURRENCY,
        "state_class": SensorStateClass.TOTAL,
        "suggested_display_precision": 2,
    }
    return [
        WalletSensorDescription(
            key=f"purchase_cost_{period}",
            translation_key=f"purchase_cost_{period}",
            role="purchase_cost",
            period=period,
            value_fn=lambda m: round(summary(m)["cost"], 4),
            attrs_fn=lambda m: {
                "energy_kwh": round(summary(m)["imp"], 3),
                "unpriced_kwh": round(summary(m)["imp_unpriced"], 3),
            },
            **money,
        ),
        WalletSensorDescription(
            key=f"sale_revenue_{period}",
            translation_key=f"sale_revenue_{period}",
            role="sale_revenue",
            period=period,
            value_fn=lambda m: round(summary(m)["rev"], 4),
            attrs_fn=lambda m: {
                "energy_kwh": round(summary(m)["exp"], 3),
                "unpriced_kwh": round(summary(m)["exp_unpriced"], 3),
            },
            **money,
        ),
        WalletSensorDescription(
            key=f"balance_{period}",
            translation_key=f"balance_{period}",
            role="balance",
            period=period,
            value_fn=lambda m: (
                round(m.balance_year_value(), 4)
                if period == "year"
                else round(summary(m)["balance"], 4)
            ),
            attrs_fn=lambda m: {
                "monthly_fee": round(m.prices.monthly_fee, 2),
            },
            **money,
        ),
    ]


DESCRIPTIONS: list[WalletSensorDescription] = [
    *(d for period in PERIODS for d in _period_descriptions(period)),
    WalletSensorDescription(
        key="purchase_cost_total",
        translation_key="purchase_cost_total",
        role="purchase_cost",
        period="total",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda m: round(m.lifetime()["cost"], 4),
    ),
    WalletSensorDescription(
        key="sale_revenue_total",
        translation_key="sale_revenue_total",
        role="sale_revenue",
        period="total",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda m: round(m.lifetime()["rev"], 4),
    ),
    WalletSensorDescription(
        key="buy_price",
        translation_key="buy_price",
        role="buy_price",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda m: m.current_buy_price(),
        attrs_fn=lambda m: {"forecast": m.price_forecast()},
    ),
    WalletSensorDescription(
        key="sell_price",
        translation_key="sell_price",
        role="sell_price",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda m: m.current_sell_price(),
    ),
    # Invoice sensors
    WalletSensorDescription(
        key="invoice_no_correction",
        translation_key="invoice_no_correction",
        role="invoice",
        period="month",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda m: round(m.invoice_no_correction(), 2),
        attrs_fn=lambda m: {
            "monthly_fee": round(m.prices.monthly_fee, 2),
            "purchase_cost_month": round(m.summary("month")["cost"], 2),
        },
    ),
    WalletSensorDescription(
        key="invoice_with_deposit",
        translation_key="invoice_with_deposit",
        role="invoice",
        period="month",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda m: round(m.invoice_with_deposit(), 2),
        attrs_fn=lambda m: {
            "monthly_fee": round(m.prices.monthly_fee, 2),
            "purchase_cost_month": round(m.summary("month")["cost"], 2),
            "deposit_correction": round(m.deposit_correction_total(), 2),
            "imp_peak_kwh": round(m.summary("month").get("imp_peak", 0.0), 3),
            "imp_offpeak_kwh": round(m.summary("month").get("imp_offpeak", 0.0), 3),
            "price_peak_depo": m.prices.price_peak_depo,
            "price_offpeak_depo": m.prices.price_offpeak_depo,
        },
    ),
]


def _deposit_buckets_and_status(m: WalletManager):
    buckets, ledger = m.deposit_buckets()
    today = dt_util.now().date()
    statuses = [_deposit.status(b, today, m.deposit_warning_days) for b in buckets]
    return buckets, ledger, statuses


def _oldest_active_bucket(m: WalletManager) -> dict[str, Any] | None:
    buckets, _ledger, statuses = _deposit_buckets_and_status(m)
    for b, s in zip(buckets, statuses):
        if s in ("aktywny", "wygasa_wkrotce"):
            return b
    return None


def _expiring_soon_buckets(m: WalletManager) -> list[dict[str, Any]]:
    buckets, _ledger, statuses = _deposit_buckets_and_status(m)
    return [b for b, s in zip(buckets, statuses) if s == "wygasa_wkrotce"]


def _oldest_block_attrs(m: WalletManager) -> dict[str, Any]:
    b = _oldest_active_bucket(m)
    if b is None:
        return {"info": "brak aktywnych blokow"}
    days_left = (date.fromisoformat(b["expiry"]) - dt_util.now().date()).days
    return {
        "pochodzenie": b["origin"], "szacowany": b["is_estimate"],
        "kwota_pierwotna": b["initial"], "wygasa": b["expiry"], "dni_do_wygasniecia": days_left,
    }


def _blocks_attrs(m: WalletManager) -> dict[str, Any]:
    buckets, ledger, statuses = _deposit_buckets_and_status(m)
    return {
        "bloki": [
            {
                "pochodzenie": b["origin"], "wygasa": b["expiry"], "kwota_pierwotna": b["initial"],
                "pozostalo": b["remaining"], "status": s, "szacowany": b["is_estimate"],
            }
            for b, s in zip(buckets, statuses)
        ],
        "historia_miesieczna": list(reversed(ledger)),
        "suma_pozostalo": round(sum(b["remaining"] for b in buckets), 2),
    }


DEPOSIT_DESCRIPTIONS: list[WalletSensorDescription] = [
    WalletSensorDescription(
        key="deposit_oldest_block",
        translation_key="deposit_oldest_block",
        role="deposit",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda m: round(b["remaining"], 2) if (b := _oldest_active_bucket(m)) else 0.0,
        attrs_fn=lambda m: _oldest_block_attrs(m),
    ),
    WalletSensorDescription(
        key="deposit_expiring_soon",
        translation_key="deposit_expiring_soon",
        role="deposit",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        value_fn=lambda m: round(sum(b["remaining"] for b in _expiring_soon_buckets(m)), 2),
        attrs_fn=lambda m: {"bloki": [
            {"pochodzenie": b["origin"], "pozostalo": b["remaining"], "wygasa": b["expiry"]}
            for b in _expiring_soon_buckets(m)
        ]},
    ),
    WalletSensorDescription(
        key="deposit_blocks",
        translation_key="deposit_blocks",
        role="deposit",
        value_fn=lambda m: sum(1 for s in _deposit_buckets_and_status(m)[2] if s in ("aktywny", "wygasa_wkrotce")),
        attrs_fn=lambda m: _blocks_attrs(m),
    ),
    WalletSensorDescription(
        key="deposit_actual_balance",
        translation_key="deposit_actual_balance",
        role="deposit",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda m: m.deposit_actual_balance(),
        attrs_fn=lambda m: {"suma_przepadla_dotychczas": round(sum(b["forfeited"] for b in m.deposit_buckets()[0]), 2)},
    ),
]


def _g12(conf_key: str, translation: str) -> WalletSensorDescription:
    return WalletSensorDescription(
        key=translation,
        translation_key=translation,
        role="g12",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=4,
        value_fn=lambda m, k=conf_key: round(float(m.conf.get(k) or 0), 4),
    )


DEPOSIT_DESCRIPTIONS += [
    _g12(CONF_G12_DIST_FIXED, "g12_distribution_fixed"),
    _g12(CONF_G12_SALE_FIXED, "g12_sale_fixed"),
    _g12(CONF_G12_DIST_DAY, "g12_distribution_day"),
    _g12(CONF_G12_DIST_NIGHT, "g12_distribution_night"),
    _g12(CONF_G12_SALE_DAY, "g12_sale_day"),
    _g12(CONF_G12_SALE_NIGHT, "g12_sale_night"),
    WalletSensorDescription(
        key="deposit_kwh_remaining_day",
        translation_key="deposit_kwh_remaining_day",
        role="deposit",
        native_unit_of_measurement="kWh",
        value_fn=lambda m: (
            round(m.deposit_actual_balance() / float(m.conf[CONF_G12_SALE_DAY]), 1)
            if m.conf.get(CONF_G12_SALE_DAY)
            else None
        ),
    ),
    WalletSensorDescription(
        key="deposit_kwh_remaining_night",
        translation_key="deposit_kwh_remaining_night",
        role="deposit",
        native_unit_of_measurement="kWh",
        value_fn=lambda m: (
            round(m.deposit_actual_balance() / float(m.conf[CONF_G12_SALE_NIGHT]), 1)
            if m.conf.get(CONF_G12_SALE_NIGHT)
            else None
        ),
    ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    manager: WalletManager = entry.runtime_data
    async_add_entities(WalletSensor(manager, entry, d) for d in DESCRIPTIONS + DEPOSIT_DESCRIPTIONS)


class WalletSensor(SensorEntity):
    """A value computed by the WalletManager."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"forecast"})
    entity_description: WalletSensorDescription

    def __init__(self, manager: WalletManager, entry: ConfigEntry, description: WalletSensorDescription) -> None:
        self.entity_description = description
        self._manager = manager
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Energy Wallet",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_UPDATE.format(self._entry.entry_id), self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self._manager)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        desc = self.entity_description
        attrs: dict[str, Any] = {
            "energy_wallet": {"entry_id": self._entry.entry_id, "role": desc.role, "period": desc.period}
        }
        if desc.attrs_fn:
            attrs.update(desc.attrs_fn(self._manager))
        return attrs
