"""Pure FIFO deposit allocation math. No Home Assistant imports — unit-testable on its own."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = (year * 12 + (month - 1)) + delta
    return idx // 12, idx % 12 + 1


def _month_label_add(label: str, delta: int) -> str:
    y, m = int(label[:4]), int(label[5:7])
    y2, m2 = _add_months(y, m, delta)
    return f"{y2:04d}-{m2:02d}"


def _month_start_date(label: str) -> date:
    y, m = int(label[:4]), int(label[5:7])
    return date(y, m, 1)


def _last_day_of_month(label: str) -> date:
    next_label = _month_label_add(label, 1)
    return _month_start_date(next_label) - timedelta(days=1)


def make_bucket(origin: str, amount: float, expiry_months: int, is_estimate: bool = False) -> dict[str, Any]:
    """Build one FIFO deposit bucket originating in month ``origin`` ('YYYY-MM')."""
    expiry_label = _month_label_add(origin, expiry_months)
    return {
        "origin": origin,
        "initial": round(amount, 2),
        "remaining": round(amount, 2),
        "expiry": _last_day_of_month(expiry_label).isoformat(),
        "is_estimate": is_estimate,
        "forfeited": 0.0,
        "expired": False,
    }


def simulate(
    start_block: dict[str, Any] | None,
    events: list[dict[str, Any]],
    expiry_months: int,
    today: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Replay FIFO allocation from a starting block plus chronological monthly events.

    Stateless and idempotent: always recomputes from scratch, safe to call on every sensor read.
    """
    buckets: list[dict[str, Any]] = []
    if start_block and start_block.get("amount", 0) > 0.009:
        buckets.append(
            make_bucket(start_block["origin"], start_block["amount"], expiry_months, is_estimate=True)
        )

    def expire_pass(cutoff: date) -> None:
        for b in buckets:
            if not b["expired"] and b["remaining"] > 0.009:
                if cutoff > date.fromisoformat(b["expiry"]):
                    b["forfeited"] = b["remaining"]
                    b["remaining"] = 0.0
                    b["expired"] = True

    ledger: list[dict[str, Any]] = []
    for ev in events:
        month = ev["month"]
        export = round(ev.get("export", 0.0), 2)
        pobor = round(ev.get("import", 0.0), 2)
        expire_pass(_month_start_date(month))

        if export > 0.009:
            buckets.append(make_bucket(month, export, expiry_months, is_estimate=False))

        to_consume = pobor
        allocations = []
        for b in buckets:
            if to_consume <= 0.009:
                break
            if b["remaining"] <= 0.009:
                continue
            take = min(b["remaining"], to_consume)
            b["remaining"] = round(b["remaining"] - take, 2)
            to_consume = round(to_consume - take, 2)
            allocations.append({"blok": b["origin"], "kwota": round(take, 2)})

        ledger.append({
            "miesiac": month,
            "eksport": export,
            "pobor": pobor,
            "zmiana_netto": round(export - pobor, 2),
            "alokacja": allocations,
            "niepokryte": round(to_consume, 2) if to_consume > 0.009 else 0.0,
        })

    expire_pass(today)
    return buckets, ledger


def status(bucket: dict[str, Any], today: date, warning_days: int) -> str:
    """Classify one bucket as 'wygasl' (expired), 'wykorzystany' (used up),
    'wygasa_wkrotce' (expiring soon) or 'aktywny' (active)."""
    if bucket["expired"]:
        return "wygasl"
    if bucket["remaining"] <= 0.009:
        return "wykorzystany"
    days_left = (date.fromisoformat(bucket["expiry"]) - today).days
    if days_left <= warning_days:
        return "wygasa_wkrotce"
    return "aktywny"
