"""Tariff model: time zones, buy price math.

Kept free of Home Assistant imports so it can be unit tested on its own.
All prices are in PLN per kWh (gross).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any

PEAK = "peak"
OFFPEAK = "offpeak"

_WINDOW_RE = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*-\s*(\d{1,2})(?::(\d{2}))?\s*$")


def parse_windows(spec: str | list[str] | None) -> list[tuple[int, int]]:
    """Parse "6-13,15-22" or ["6:00-13:00", ...] into minute ranges."""
    if not spec:
        return []
    parts = spec.split(",") if isinstance(spec, str) else list(spec)
    windows = []
    for part in parts:
        if not part.strip():
            continue
        match = _WINDOW_RE.match(part)
        if not match:
            raise ValueError(f"Invalid time window: {part!r}")
        h1, m1, h2, m2 = match.groups()
        start = int(h1) * 60 + int(m1 or 0)
        end = int(h2) * 60 + int(m2 or 0)
        if not (0 <= start <= 1440 and 0 <= end <= 1440):
            raise ValueError(f"Invalid time window: {part!r}")
        windows.append((start % 1440, end % 1440))
    return windows


def format_windows(windows: list[tuple[int, int]]) -> str:
    def fmt(minute: int) -> str:
        h, m = divmod(minute, 60)
        return f"{h}" if m == 0 else f"{h}:{m:02d}"

    return ",".join(f"{fmt(s)}-{fmt(e)}" for s, e in windows)


def _in_windows(minute: int, windows: list[tuple[int, int]]) -> bool:
    for start, end in windows:
        if start == end:
            return True
        if start < end and start <= minute < end:
            return True
        if start > end and (minute >= start or minute < end):
            return True
    return False


@dataclass
class PriceConfig:
    """Everything needed to price 1 kWh bought from the grid.

    All prices are gross (with VAT included). No excise or VAT is added.
    """

    peak_windows: list[tuple[int, int]] = field(default_factory=list)
    offpeak_windows: list[tuple[int, int]] = field(default_factory=list)
    price_peak: float = 0.0
    price_offpeak: float = 0.0
    price_peak_depo: float = 0.0
    price_offpeak_depo: float = 0.0
    monthly_fee: float = 0.0

    def zone(self, local_dt: datetime) -> str:
        """Determine if the given local time is in peak or off-peak zone.

        Peak hours take priority - if a time matches peak windows, it's peak.
        If peak windows are defined and the time doesn't match, it's off-peak.
        If no peak windows are defined, check off-peak windows explicitly.
        """
        minute = local_dt.hour * 60 + local_dt.minute
        if self.peak_windows:
            if _in_windows(minute, self.peak_windows):
                return PEAK
            return OFFPEAK
        if self.offpeak_windows:
            if _in_windows(minute, self.offpeak_windows):
                return OFFPEAK
            return PEAK
        return PEAK

    def buy_price(self, local_dt: datetime, rce: float | None = None) -> float:
        """Gross PLN/kWh for energy taken from the grid at ``local_dt``.

        Prices are already gross - returned as-is.
        """
        if self.zone(local_dt) == OFFPEAK:
            return self.price_offpeak
        return self.price_peak

    def depo_price(self, local_dt: datetime) -> float:
        """Deposit price per kWh for the given time zone."""
        if self.zone(local_dt) == OFFPEAK:
            return self.price_offpeak_depo
        return self.price_peak_depo


# Keep these for backward compatibility during migration
def load_catalog(year: int | None = None) -> dict[str, Any]:
    """Stub - catalog is no longer used in simplified mode."""
    return {"year": None, "sellers": {}, "dsos": {}}


def catalog_defaults(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Stub - catalog is no longer used in simplified mode."""
    return {"verified": False, "sources": []}
