# Deposit FIFO Migration Implementation Plan

> **For agentic workers:** No `superpowers:subagent-driven-development` / `superpowers:executing-plans`
> skills are installed in this environment. Execute tasks in order, TDD-style, committing after
> each. See "Sandbox test-execution constraint" below before starting.

**Goal:** Move `depozyt_fifo.py` (PyScript: deposit FIFO tracking, Taryfa G12 sensors, kWh-remaining
sensors) into the `energy_wallet` custom integration, config-driven, so the PyScript file can be
deleted.

**Architecture:** Pure FIFO/expiry math lives in a new `deposit.py` (no HA imports, like `wallet.py`).
`Wallet` gains two persisted fields (`deposit_history`, `deposit_start_block`) and three pure
methods (`deposit_events`, `set_deposit_month`, `set_deposit_start_block`) that call into
`deposit.py` — this keeps **all** deposit logic unit-testable without a `hass` fixture, matching
`wallet.py`'s existing "kept free of Home Assistant imports" design. `WalletManager` only adds thin
pass-through methods (mirrors how `set_balance_year`/`clear_balance_year` already work). `sensor.py`
adds 12 new `WalletSensorDescription` entries. Two new services, config-flow fields for G12 prices
and expiry/warning windows.

**Tech Stack:** Python 3.13, Home Assistant custom component conventions already used in this repo
(dataclasses, `voluptuous`, `pytest` + `pytest-homeassistant-custom-component`).

**Spec:** `docs/superpowers/specs/2026-11-16-deposit-fifo-migration-design.md`

**Deviation from spec (approved during planning, same external behavior):** the spec put
`deposit_events()` on `WalletManager`. It moves to `Wallet` instead — `Wallet.days` and
`Wallet.deposit_history` are exactly what it needs, and `wallet.py` is the only module in this repo
designed to be unit-tested without a `hass` fixture (see its own docstring). `WalletManager` keeps
only thin wrappers, same as `set_balance_year`/`clear_balance_year` today.

## Global Constraints

- Nothing user-specific (historical deposit amounts, tariff PLN/kWh prices) is hard-coded in
  source — everything is a config-flow field or set via a service call after install.
- No new external sensor entity config fields for deposit tracking — export/import for any month
  covered by `Wallet.days` is derived from `Wallet.summary(month_prefix)`.
- No month-end rollover/snapshot bookkeeping, no `recompute`/`przelicz` service — every sensor
  value is computed live from persisted state on each read.
- G12 sensors store/display gross (brutto) values exactly as configured — no VAT math in code.
- New entities use the `energy_wallet` naming/translation scheme (`_attr_has_entity_name = True`,
  `translation_key`), not the old PyScript entity_ids.
- `set_deposit_month` requires exactly one of (`value`) or (`export`/`import_`) — reject with a
  clear validation error otherwise.

## Review Focus

- A month present in **both** `Wallet.days` and `deposit_history` must use the `deposit_history`
  value (manual correction wins) — easy to get backwards.
- Partial-month data at expiry boundaries: a bucket exactly at its expiry date (not past it) must
  still count as active, not expired (off-by-one on the `>` vs `>=` comparison).
- FIFO consumption spanning **more than one** bucket in a single month (import bigger than the
  oldest bucket's remaining) must correctly spill into the next-oldest bucket.
- `set_deposit_month` called twice for the **same month** must overwrite, not add to, the existing
  record (idempotent correction, not accumulation).
- Division-by-zero / `None` guards: both kWh-remaining sensors and nothing else must handle a G12
  sell price of `0` (default) without raising — return `None`, not a crash or `inf`.

## Sandbox test-execution constraint

This sandbox has **no network access**, so `pip install pytest` / `pytest-homeassistant-custom-component`
fails here (confirmed: PyPI connection is proxy-blocked). Practical consequence for whoever executes
this plan in this same type of sandbox:

- Tasks 1–4 (`deposit.py`, `Wallet` additions) touch **zero** Home Assistant imports. Verify them
  with a plain `python3` script that imports the module directly and runs the same assertions the
  pytest file contains (shown inline per task) — this is real red/green, just without the `pytest`
  CLI.
- Tasks 5–9 (`config_flow.py`, `manager.py`, `sensor.py`, `services.yaml`/`__init__.py`) need the
  `pytest-homeassistant-custom-component` harness (`hass` fixture, `MockConfigEntry`, etc.) which
  cannot run in this sandbox. Write the real test code per the existing `tests/test_integration.py`
  conventions, but do **not** claim they pass — tell the user explicitly that `pytest tests/` needs
  to run on a machine with `requirements_test.txt` installed (or in CI) before merging, and that you
  verified these tasks by careful code reading instead of execution.
- **Pre-existing issue, not yours to fix:** `tests/test_integration.py` already does not match the
  current `config_flow.py` (it uses old fields like `tariff_group`/`dso`/`seller` instead of the
  current `CONF_PEAK_HOURS`/`CONF_PRICE_PEAK`/etc. single-step form) — it has been stale since the
  initial commit. Don't pattern-match its `BASE` config dict; use the real current fields (see
  Task 8). Mention this pre-existing staleness to the user once; do not fix it as part of this plan
  unless asked.

---

### Task 1: `deposit.py` — bucket creation + FIFO consumption

**Files:**
- Create: `custom_components/energy_wallet/deposit.py`
- Test: `tests/test_deposit.py`

**Interfaces:**
- Produces: `make_bucket(origin: str, amount: float, expiry_months: int, is_estimate: bool = False) -> dict`
  returning `{"origin", "initial", "remaining", "expiry", "is_estimate", "forfeited", "expired"}`
  (`expiry` is an ISO date string, last day of the month `expiry_months` after `origin`).
- Produces: `simulate(start_block: dict | None, events: list[dict], expiry_months: int, today: date) -> tuple[list[dict], list[dict]]`
  — `events` is `[{"month": "YYYY-MM", "export": float, "import": float}, ...]`, already sorted
  ascending by month by the caller. Returns `(buckets, ledger)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_deposit.py
from datetime import date

from custom_components.energy_wallet.deposit import make_bucket, simulate


def test_make_bucket_expiry_is_last_day_of_month_12_months_later():
    b = make_bucket("2026-01", 100.0, expiry_months=12)
    assert b == {
        "origin": "2026-01", "initial": 100.0, "remaining": 100.0,
        "expiry": "2027-01-31", "is_estimate": False, "forfeited": 0.0, "expired": False,
    }


def test_simulate_single_month_export_only():
    buckets, ledger = simulate(None, [{"month": "2026-06", "export": 500.0, "import": 0.0}], 12, date(2026, 11, 16))
    assert len(buckets) == 1
    assert buckets[0]["remaining"] == 500.0
    assert ledger == [{"miesiac": "2026-06", "eksport": 500.0, "pobor": 0.0, "zmiana_netto": 500.0, "alokacja": [], "niepokryte": 0.0}]


def test_simulate_fifo_consumes_oldest_bucket_first():
    events = [
        {"month": "2026-06", "export": 100.0, "import": 0.0},
        {"month": "2026-07", "export": 50.0, "import": 0.0},
        {"month": "2026-08", "export": 0.0, "import": 30.0},
    ]
    buckets, ledger = simulate(None, events, 12, date(2026, 11, 16))
    june, july = buckets[0], buckets[1]
    assert june["remaining"] == 70.0
    assert july["remaining"] == 50.0
    assert ledger[-1]["alokacja"] == [{"blok": "2026-06", "kwota": 30.0}]


def test_simulate_import_spills_into_second_bucket():
    events = [
        {"month": "2026-06", "export": 20.0, "import": 0.0},
        {"month": "2026-07", "export": 50.0, "import": 0.0},
        {"month": "2026-08", "export": 0.0, "import": 30.0},
    ]
    buckets, ledger = simulate(None, events, 12, date(2026, 11, 16))
    june, july = buckets[0], buckets[1]
    assert june["remaining"] == 0.0
    assert july["remaining"] == 40.0
    assert ledger[-1]["alokacja"] == [{"blok": "2026-06", "kwota": 20.0}, {"blok": "2026-07", "kwota": 10.0}]
    assert ledger[-1]["niepokryte"] == 0.0


def test_simulate_import_bigger_than_all_buckets_reports_uncovered():
    events = [
        {"month": "2026-06", "export": 20.0, "import": 0.0},
        {"month": "2026-07", "export": 0.0, "import": 50.0},
    ]
    _buckets, ledger = simulate(None, events, 12, date(2026, 11, 16))
    assert ledger[-1]["niepokryte"] == 30.0


def test_simulate_with_starting_block():
    start = make_bucket("2025-12", 200.0, expiry_months=12, is_estimate=True)
    buckets, _ledger = simulate(start, [{"month": "2026-01", "export": 0.0, "import": 50.0}], 12, date(2026, 11, 16))
    assert buckets[0]["origin"] == "2025-12"
    assert buckets[0]["remaining"] == 150.0
    assert buckets[0]["is_estimate"] is True
```

- [ ] **Step 2: Run to verify it fails**

```
python3 -c "
import sys; sys.path.insert(0, '.')
from tests.test_deposit import *
for name, fn in list(globals().items()):
    if name.startswith('test_'):
        try:
            fn()
        except ModuleNotFoundError as e:
            print('EXPECTED FAIL:', name, '->', e)
"
```
Expected: every test prints `EXPECTED FAIL ... No module named 'custom_components.energy_wallet.deposit'`.

- [ ] **Step 3: Implement `deposit.py`**

```python
"""Pure FIFO deposit allocation math. No Home Assistant imports — unit-testable on its own."""

from __future__ import annotations

from datetime import date
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
    from datetime import timedelta
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
```

- [ ] **Step 4: Run to verify it passes**

```
python3 -c "
import sys; sys.path.insert(0, '.')
from tests.test_deposit import *
ran = 0
for name, fn in list(globals().items()):
    if name.startswith('test_'):
        fn(); ran += 1
        print('PASS', name)
print(f'{ran} passed')
"
```
Expected: `PASS` for every test, then `6 passed`. Also run on a clean machine later with
`pytest tests/test_deposit.py -v` once `requirements_test.txt` is installed.

- [ ] **Step 5: Commit**

`git add custom_components/energy_wallet/deposit.py tests/test_deposit.py && git commit -m "Add deposit.py: FIFO bucket allocation + expiry (pure logic)"`

---

### Task 2: `deposit.py` — expiry status + warning window

**Files:**
- Modify: `custom_components/energy_wallet/deposit.py`
- Test: `tests/test_deposit.py`

**Interfaces:**
- Consumes: bucket dicts from Task 1's `make_bucket`/`simulate`.
- Produces: `status(bucket: dict, today: date, warning_days: int) -> str`, one of
  `"wygasl"`, `"wykorzystany"`, `"wygasa_wkrotce"`, `"aktywny"`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_deposit.py
from datetime import date

from custom_components.energy_wallet.deposit import make_bucket, status


def test_status_active_far_from_expiry():
    b = make_bucket("2026-06", 100.0, expiry_months=12)
    assert status(b, date(2026, 11, 16), warning_days=60) == "aktywny"


def test_status_expiring_soon_within_warning_window():
    b = make_bucket("2026-01", 100.0, expiry_months=12)  # expires 2027-01-31
    assert status(b, date(2026, 12, 15), warning_days=60) == "wygasa_wkrotce"


def test_status_used_up():
    b = make_bucket("2026-06", 100.0, expiry_months=12)
    b["remaining"] = 0.0
    assert status(b, date(2026, 11, 16), warning_days=60) == "wykorzystany"


def test_status_expired_flag_wins():
    b = make_bucket("2026-06", 100.0, expiry_months=12)
    b["expired"] = True
    assert status(b, date(2026, 11, 16), warning_days=60) == "wygasl"


def test_status_exactly_on_expiry_day_is_still_active_not_expired():
    b = make_bucket("2026-01", 100.0, expiry_months=1)  # expiry = 2026-02-28
    assert status(b, date(2026, 2, 28), warning_days=0) == "aktywny"
```

- [ ] **Step 2: Run to verify it fails** — same inline-script approach as Task 1 Step 2; expect
`AttributeError`/`ImportError: cannot import name 'status'`.

- [ ] **Step 3: Implement `status()`** — append to `deposit.py`:

```python
def status(bucket: dict[str, Any], today: date, warning_days: int) -> str:
    if bucket["expired"]:
        return "wygasl"
    if bucket["remaining"] <= 0.009:
        return "wykorzystany"
    days_left = (date.fromisoformat(bucket["expiry"]) - today).days
    if days_left <= warning_days:
        return "wygasa_wkrotce"
    return "aktywny"
```

- [ ] **Step 4: Run to verify it passes** — same inline-script approach; expect all 11 tests (6 from
Task 1 + 5 new) to print `PASS`.

- [ ] **Step 5: Commit**

`git add -A && git commit -m "Add deposit.status(): active/expiring-soon/used-up/expired"`

---

### Task 3: `Wallet` — deposit storage fields + event aggregation

**Files:**
- Modify: `custom_components/energy_wallet/wallet.py`
- Test: `tests/test_wallet.py`

**Interfaces:**
- Consumes: `Wallet.days` (existing, `{"YYYY-MM-DD": {"cost": float, "rev": float, ...}}`),
  `Wallet.deposit_history` (new).
- Produces: `Wallet.deposit_events() -> list[dict]`, sorted ascending by month, each
  `{"month", "export", "import"}` — `deposit_history` entries win over `days`-derived ones for the
  same month.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_wallet.py
def test_deposit_events_prefers_history_over_derived_days():
    w = Wallet()
    w.days = {
        "2026-11-01": {"cost": 5.0, "rev": 1.0},
        "2026-11-15": {"cost": 3.0, "rev": 0.5},
    }
    w.deposit_history = {"2026-01": {"export": 5.40, "import": 5.40}}
    events = w.deposit_events()
    assert events == [
        {"month": "2026-01", "export": 5.40, "import": 5.40},
        {"month": "2026-11", "export": 1.5, "import": 8.0},
    ]


def test_deposit_events_history_wins_even_if_days_has_same_month():
    w = Wallet()
    w.days = {"2026-11-01": {"cost": 100.0, "rev": 0.0}}
    w.deposit_history = {"2026-11": {"export": 1.5, "import": 8.0}}
    assert w.deposit_events() == [{"month": "2026-11", "export": 1.5, "import": 8.0}]


def test_set_deposit_month_with_value_splits_into_export_import():
    w = Wallet()
    w.set_deposit_month("2026-08", value=150.0)
    assert w.deposit_history["2026-08"] == {"export": 150.0, "import": 0.0}
    w.set_deposit_month("2026-08", value=-40.0)
    assert w.deposit_history["2026-08"] == {"export": 0.0, "import": 40.0}


def test_set_deposit_month_with_export_import_overwrites_not_accumulates():
    w = Wallet()
    w.set_deposit_month("2026-08", export=10.0, import_=2.0)
    w.set_deposit_month("2026-08", export=20.0, import_=3.0)
    assert w.deposit_history["2026-08"] == {"export": 20.0, "import": 3.0}


def test_set_deposit_month_requires_value_or_export_import():
    w = Wallet()
    try:
        w.set_deposit_month("2026-08")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_set_deposit_start_block():
    w = Wallet()
    w.set_deposit_start_block("2025-12", 787.32)
    assert w.deposit_start_block == {"origin": "2025-12", "amount": 787.32, "is_estimate": True}
```

- [ ] **Step 2: Run to verify it fails** — same inline-script approach; expect `AttributeError` on
`w.deposit_events` / `w.deposit_history` / `w.set_deposit_month` / etc.

- [ ] **Step 3: Implement in `wallet.py`**

In `Wallet.__init__`, alongside the existing `self.balance_year_override = ...` line:

```python
        self.deposit_history: dict[str, dict[str, float]] = data.get("deposit_history", {})
        self.deposit_start_block: dict[str, Any] | None = data.get("deposit_start_block")
```

In `Wallet.as_dict`, alongside the existing `"balance_year_override": ...` line:

```python
            "deposit_history": self.deposit_history,
            "deposit_start_block": self.deposit_start_block,
```

New methods, placed next to `clear_balance_year_override`:

```python
    def deposit_events(self) -> list[dict[str, Any]]:
        """Monthly export/import events for deposit FIFO: manual corrections win over
        whatever this month's own tracked days would derive."""
        months: dict[str, dict[str, float]] = dict(self.deposit_history)
        derived: dict[str, dict[str, float]] = {}
        for day_key, day in self.days.items():
            month = day_key[:7]
            if month in months:
                continue
            bucket = derived.setdefault(month, {"export": 0.0, "import": 0.0})
            bucket["export"] += day.get("rev", 0.0)
            bucket["import"] += day.get("cost", 0.0)
        for month, totals in derived.items():
            months[month] = {"export": round(totals["export"], 2), "import": round(totals["import"], 2)}
        return [{"month": m, **months[m]} for m in sorted(months)]

    def set_deposit_month(
        self, month: str, export: float | None = None, import_: float | None = None, value: float | None = None
    ) -> None:
        """Set/overwrite one month's deposit export+import (manual correction or historical seed)."""
        if value is None and export is None and import_ is None:
            raise ValueError("set_deposit_month needs 'value' or 'export'/'import_'")
        if value is not None and export is None and import_ is None:
            export = round(max(value, 0.0), 2)
            import_ = round(max(-value, 0.0), 2)
        self.deposit_history[month] = {
            "export": round(export or 0.0, 2),
            "import": round(import_ or 0.0, 2),
        }

    def set_deposit_start_block(self, origin: str, amount: float) -> None:
        """Set the one-time starting deposit block for months before any tracked history."""
        self.deposit_start_block = {"origin": origin, "amount": round(amount, 2), "is_estimate": True}
```

- [ ] **Step 4: Run to verify it passes** — same inline-script approach; expect all new tests to
print `PASS` (and re-run the whole `test_wallet.py` suite's assertions the same way to confirm no
regression in existing ones).

- [ ] **Step 5: Commit**

`git add -A && git commit -m "Wallet: add deposit_history/deposit_start_block storage + deposit_events()"`

---

### Task 4: `Wallet.deposit_buckets()` — tie `deposit.py` + storage together

**Files:**
- Modify: `custom_components/energy_wallet/wallet.py`
- Test: `tests/test_wallet.py`

**Interfaces:**
- Consumes: `deposit.simulate`, `deposit.status` (Tasks 1–2), `Wallet.deposit_events()` (Task 3).
- Produces: `Wallet.deposit_buckets(expiry_months: int, today: date) -> tuple[list[dict], list[dict]]`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_wallet.py
from datetime import date as _date


def test_deposit_buckets_combines_history_and_live_month():
    w = Wallet()
    w.set_deposit_start_block("2025-12", 100.0)
    w.set_deposit_month("2026-01", export=5.40, import_=5.40)
    w.days = {"2026-11-05": {"cost": 2.0, "rev": 20.0}}  # live November, not yet closed
    buckets, ledger = w.deposit_buckets(expiry_months=12, today=_date(2026, 11, 16))
    assert [b["origin"] for b in buckets] == ["2025-12", "2026-11"]
    assert ledger[-1]["miesiac"] == "2026-11"
    assert ledger[-1]["eksport"] == 20.0
```

- [ ] **Step 2: Run to verify it fails** — expect `AttributeError: 'Wallet' object has no attribute
'deposit_buckets'`.

- [ ] **Step 3: Implement** — add the import at the top of `wallet.py` and the method next to
`set_deposit_start_block`:

```python
from . import deposit as _deposit
```

```python
    def deposit_buckets(self, expiry_months: int, today) -> tuple[list[dict], list[dict]]:
        return _deposit.simulate(self.deposit_start_block, self.deposit_events(), expiry_months, today)
```

- [ ] **Step 4: Run to verify it passes**

- [ ] **Step 5: Commit**

`git add -A && git commit -m "Wallet.deposit_buckets(): wire deposit_events() into deposit.simulate()"`

---

### Task 5: Config fields (`const.py` + `config_flow.py`)

**Files:**
- Modify: `custom_components/energy_wallet/const.py`
- Modify: `custom_components/energy_wallet/config_flow.py`
- Test: `tests/test_config_flow.py` (new — no `hass` fixture needed, `full_schema` is a plain
  `voluptuous.Schema` builder)

**Interfaces:**
- Produces: `CONF_DEPOSIT_EXPIRY_MONTHS`, `CONF_DEPOSIT_WARNING_DAYS`, `CONF_G12_DIST_FIXED`,
  `CONF_G12_SALE_FIXED`, `CONF_G12_DIST_DAY`, `CONF_G12_DIST_NIGHT`, `CONF_G12_SALE_DAY`,
  `CONF_G12_SALE_NIGHT` in `const.py`; `full_schema()` accepts and defaults all of them.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_flow.py
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
```

- [ ] **Step 2: Run to verify it fails** — this one genuinely needs `voluptuous` + the HA
`selector` helper module, which import actual `homeassistant` — **cannot run in this sandbox**
(no `homeassistant` package installed, no network to install it). Read the diff carefully instead;
run for real with `pytest tests/test_config_flow.py -v` on a machine with `requirements_test.txt`.

- [ ] **Step 3: Implement**

In `const.py`, append:

```python
CONF_DEPOSIT_EXPIRY_MONTHS = "deposit_expiry_months"
CONF_DEPOSIT_WARNING_DAYS = "deposit_warning_days"
CONF_G12_DIST_FIXED = "g12_distribution_fixed"
CONF_G12_SALE_FIXED = "g12_sale_fixed"
CONF_G12_DIST_DAY = "g12_distribution_day"
CONF_G12_DIST_NIGHT = "g12_distribution_night"
CONF_G12_SALE_DAY = "g12_sale_day"
CONF_G12_SALE_NIGHT = "g12_sale_night"
```

In `config_flow.py`, add the new imports from `.const`, two more selectors next to `PRICE`/`MONTHLY`:

```python
MONTHS_INT = selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=36, step=1, mode="box"))
DAYS_INT = selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=365, step=1, mode="box"))
```

and extend `full_schema()`'s returned mapping with:

```python
            vol.Optional(CONF_DEPOSIT_EXPIRY_MONTHS, **_default(defaults.get(CONF_DEPOSIT_EXPIRY_MONTHS, 12))): MONTHS_INT,
            vol.Optional(CONF_DEPOSIT_WARNING_DAYS, **_default(defaults.get(CONF_DEPOSIT_WARNING_DAYS, 60))): DAYS_INT,
            vol.Optional(CONF_G12_DIST_FIXED, **_default(defaults.get(CONF_G12_DIST_FIXED, 0))): MONTHLY,
            vol.Optional(CONF_G12_SALE_FIXED, **_default(defaults.get(CONF_G12_SALE_FIXED, 0))): MONTHLY,
            vol.Optional(CONF_G12_DIST_DAY, **_default(defaults.get(CONF_G12_DIST_DAY, 0))): PRICE,
            vol.Optional(CONF_G12_DIST_NIGHT, **_default(defaults.get(CONF_G12_DIST_NIGHT, 0))): PRICE,
            vol.Optional(CONF_G12_SALE_DAY, **_default(defaults.get(CONF_G12_SALE_DAY, 0))): PRICE,
            vol.Optional(CONF_G12_SALE_NIGHT, **_default(defaults.get(CONF_G12_SALE_NIGHT, 0))): PRICE,
```

- [ ] **Step 4: Run to verify it passes** — not executable here (see Step 2); re-read the diff
against the test to confirm every asserted key/default is present.

- [ ] **Step 5: Commit**

`git add -A && git commit -m "config_flow: add deposit expiry/warning + G12 tariff fields"`

---

### Task 6: `WalletManager` — thin deposit wrappers

**Files:**
- Modify: `custom_components/energy_wallet/manager.py`

**Interfaces:**
- Consumes: `Wallet.deposit_buckets`, `Wallet.set_deposit_month`, `Wallet.set_deposit_start_block`
  (Tasks 3–4); `CONF_DEPOSIT_EXPIRY_MONTHS`, `CONF_DEPOSIT_WARNING_DAYS` (Task 5).
- Produces (consumed by sensor.py in Tasks 7–8):
  `manager.deposit_expiry_months -> int`, `manager.deposit_warning_days -> int`,
  `manager.deposit_buckets() -> tuple[list[dict], list[dict]]`,
  `manager.deposit_actual_balance() -> float`,
  `manager.set_deposit_month(month, export=None, import_=None, value=None) -> None`,
  `manager.set_deposit_starting_block(origin, amount) -> None`.

No new automated test in this task — these are one-line pass-throughs over already-tested `Wallet`
methods; they're exercised indirectly by Task 9's service integration test. Implement directly:

- [ ] **Step 1: Add properties and methods to `WalletManager`**, next to `lifetime()`:

```python
    @property
    def deposit_expiry_months(self) -> int:
        return int(self.conf.get(CONF_DEPOSIT_EXPIRY_MONTHS) or 12)

    @property
    def deposit_warning_days(self) -> int:
        return int(self.conf.get(CONF_DEPOSIT_WARNING_DAYS) or 60)

    def deposit_buckets(self) -> tuple[list[dict], list[dict]]:
        return self.wallet.deposit_buckets(self.deposit_expiry_months, dt_util.now().date())

    def deposit_actual_balance(self) -> float:
        buckets, _ledger = self.deposit_buckets()
        return round(sum(b["remaining"] for b in buckets), 2)
```

and next to `set_balance_year`/`clear_balance_year`:

```python
    def set_deposit_month(
        self, month: str, export: float | None = None, import_: float | None = None, value: float | None = None
    ) -> None:
        self.wallet.set_deposit_month(month, export, import_, value)
        self._settle()

    def set_deposit_starting_block(self, origin: str, amount: float) -> None:
        self.wallet.set_deposit_start_block(origin, amount)
        self._settle()
```

- [ ] **Step 2: Add the new const imports** to the existing `from .const import (...)` block in
`manager.py`: `CONF_DEPOSIT_EXPIRY_MONTHS`, `CONF_DEPOSIT_WARNING_DAYS`.

- [ ] **Step 3: Sanity-check by reading** — confirm `self.conf` already holds
`{**entry.data, **entry.options}` (it does, see `WalletManager.__init__`), so the new optional
config keys resolve without a `KeyError` even on an entry created before this change (the
`.get(...)` + `or default` guards cover that).

- [ ] **Step 4: Commit**

`git add -A && git commit -m "WalletManager: thin pass-through methods for deposit FIFO"`

---

### Task 7: Sensors — deposit FIFO (4 sensors)

**Files:**
- Modify: `custom_components/energy_wallet/sensor.py`
- Modify: `custom_components/energy_wallet/strings.json`,
  `custom_components/energy_wallet/translations/en.json`,
  `custom_components/energy_wallet/translations/pl.json`
- Test: extend `tests/test_integration.py` (HA harness required — see sandbox constraint note)

**Interfaces:**
- Consumes: `manager.deposit_buckets()`, `manager.deposit_warning_days`, `deposit.status`
  (import directly in `sensor.py` for building attributes).

- [ ] **Step 1: Write the failing test** — append to `tests/test_integration.py`, reusing its
existing `BASE`/`setup_states`/`value()` fixtures (current, real config fields — not the stale
`tariff_group`-style dict elsewhere in that file):

```python
async def test_deposit_sensors_reflect_manual_month(hass: HomeAssistant, setup_states, aioclient_mock) -> None:
    aioclient_mock.get(PSE_RCE_URL, json={"value": []})
    entry = MockConfigEntry(domain=DOMAIN, data=BASE, title="Energy Wallet")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    manager = entry.runtime_data
    manager.set_deposit_starting_block("2025-12", 100.0)
    manager.set_deposit_month("2026-01", export=5.40, import_=5.40)
    manager.set_deposit_month("2026-06", export=500.0, import_=0.0)
    manager.set_deposit_month("2026-07", export=0.0, import_=30.0)
    await hass.async_block_till_done()

    blocks = value(hass, entry, "deposit_blocks")
    assert blocks.attributes["suma_pozostalo"] == pytest.approx(100.0 + 5.40 - 5.40 + 500.0 - 30.0)
    oldest = value(hass, entry, "deposit_oldest_block")
    assert oldest.attributes["pochodzenie"] == "2025-12"
    actual = value(hass, entry, "deposit_actual_balance")
    assert float(actual.state) == pytest.approx(100.0 + 500.0 - 30.0)
```

- [ ] **Step 2: Run to verify it fails** — cannot execute in this sandbox (no
`pytest-homeassistant-custom-component`); expect, when run for real, failures naming the missing
`deposit_blocks`/`deposit_oldest_block`/`deposit_actual_balance` entities.

- [ ] **Step 3: Implement in `sensor.py`**

Add near the top, with the other imports: `from datetime import date as _date` and
`from . import deposit as _deposit`. Add a new description list next to `DESCRIPTIONS`:

```python
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
```

Each sensor above calls `_deposit_buckets_and_status(m)` twice per read (once for `value_fn`, once for
`attrs_fn`) — simple and correct, if wasteful; `m.deposit_buckets()` itself recomputes from
`Wallet.days`/`deposit_history` on every call rather than caching, same as every other sensor in
this file already does (e.g. `summary(m)` in `_period_descriptions`). Not a regression, not worth
optimizing in this task — flag it in the PR description as a known, pre-existing-pattern tradeoff
if it ever shows up in profiling, nothing to do here.

(`value_fn=lambda m: round(b["remaining"], 2) if (b := _oldest_active_bucket(m)) else 0.0` uses a
walrus assignment inside the lambda — confirm the repo's minimum Python version supports it; it
already requires 3.12+ for the `type` alias in `__init__.py`, so 3.8+ walrus support is a non-issue.)

Finally, extend `async_setup_entry` to add these too:

```python
async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    manager: WalletManager = entry.runtime_data
    async_add_entities(WalletSensor(manager, entry, d) for d in DESCRIPTIONS + DEPOSIT_DESCRIPTIONS)
```

(`date` must also be imported: `from datetime import date`.)

Add translation entries (same shape in all three files, `strings.json` / `translations/en.json`
identical English text, `translations/pl.json` Polish) under `"entity": {"sensor": {...}}`
(match the existing structure already used for `balance_year` etc. — read that block first and
mirror its nesting exactly):

```json
"deposit_oldest_block": {"name": "Oldest deposit block"},
"deposit_expiring_soon": {"name": "Deposit expiring soon"},
"deposit_blocks": {"name": "Deposit blocks"},
"deposit_actual_balance": {"name": "Actual deposit balance"}
```
(pl.json: `"Najstarszy blok depozytu"`, `"Depozyt wygasający wkrótce"`, `"Bloki depozytu"`,
`"Rzeczywisty stan depozytu"`.)

- [ ] **Step 4: Run to verify it passes** — not executable here; re-read the test against the
implementation line by line (entity_id resolution via `f"{entry.entry_id}_{key}"` already used by
`value()`, matches `_attr_unique_id` built in `WalletSensor.__init__`).

- [ ] **Step 5: Commit**

`git add -A && git commit -m "sensor.py: add 4 deposit FIFO sensors + translations"`

---

### Task 8: Sensors — G12 tariff (6) + kWh-remaining (2)

**Files:**
- Modify: `custom_components/energy_wallet/sensor.py`
- Modify: `strings.json`, `translations/en.json`, `translations/pl.json`
- Test: extend `tests/test_integration.py`

**Interfaces:**
- Consumes: `CONF_G12_*` config values directly via `m.conf.get(...)`, `manager.deposit_actual_balance()`.

- [ ] **Step 1: Write the failing test**

```python
async def test_g12_and_kwh_remaining_sensors(hass: HomeAssistant, setup_states, aioclient_mock) -> None:
    aioclient_mock.get(PSE_RCE_URL, json={"value": []})
    data = {**BASE, "g12_distribution_fixed": 34.33, "g12_sale_day": 0.6221, "g12_sale_night": 0.4716}
    entry = MockConfigEntry(domain=DOMAIN, data=data, title="Energy Wallet")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert float(value(hass, entry, "g12_distribution_fixed").state) == pytest.approx(34.33)
    assert value(hass, entry, "g12_sale_fixed").state == "0.0"  # default when omitted

    entry.runtime_data.set_deposit_month("2026-01", export=62.21, import_=0.0)
    await hass.async_block_till_done()
    # deposit_actual_balance() is 62.21 (one untouched bucket, no consumption) - both prices are
    # configured in `data` above, so both kWh sensors must compute, not read as "unknown".
    kwh_day = value(hass, entry, "deposit_kwh_remaining_day")
    assert float(kwh_day.state) == pytest.approx(round(62.21 / 0.6221, 1))  # == 100.0
    kwh_night = value(hass, entry, "deposit_kwh_remaining_night")
    assert float(kwh_night.state) == pytest.approx(round(62.21 / 0.4716, 1))  # == 131.9
```

(The `None`-guard path, where a price is left at its default of 0, is covered separately by
`test_kwh_remaining_is_none_without_price` below.)

```python
async def test_kwh_remaining_is_none_without_price(hass: HomeAssistant, setup_states, aioclient_mock) -> None:
    aioclient_mock.get(PSE_RCE_URL, json={"value": []})
    entry = MockConfigEntry(domain=DOMAIN, data=BASE, title="Energy Wallet")  # no g12_sale_day/night set -> default 0
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert value(hass, entry, "deposit_kwh_remaining_day").state == "unknown"
```

- [ ] **Step 2: Run to verify it fails** — not executable here; expect, for real,
`KeyError`/`AttributeError` for the six `g12_*` and two `deposit_kwh_remaining_*` entities.

- [ ] **Step 3: Implement in `sensor.py`** — append to `DEPOSIT_DESCRIPTIONS`:

```python
def _g12(key: str, translation: str) -> WalletSensorDescription:
    return WalletSensorDescription(
        key=translation,
        translation_key=translation,
        role="g12",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=4,
        value_fn=lambda m, k=key: round(float(m.conf.get(k) or 0), 4),
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
```

(Import `CONF_G12_DIST_FIXED, CONF_G12_SALE_FIXED, CONF_G12_DIST_DAY, CONF_G12_DIST_NIGHT,
CONF_G12_SALE_DAY, CONF_G12_SALE_NIGHT` from `.const` at the top of `sensor.py`.)

Add matching translation entries for all 8 new `translation_key`s (English names: "G12 distribution
— fixed", "G12 sale — fixed", "G12 distribution — day", "G12 distribution — night", "G12 sale —
day", "G12 sale — night", "kWh remaining in deposit (day)", "kWh remaining in deposit (night)";
Polish: "G12 - koszt dystrybucji stały", "G12 - koszt sprzedaży stały", "G12 - koszt dystrybucji
dzienna", "G12 - koszt dystrybucji nocna", "G12 - koszt sprzedaży dzienna", "G12 - koszt sprzedaży
nocna", "Ile kWh zostało w depozycie (dzień)", "Ile kWh zostało w depozycie (noc)").

- [ ] **Step 4: Run to verify it passes** — not executable here; re-read against the test.

- [ ] **Step 5: Commit**

`git add -A && git commit -m "sensor.py: add 6 G12 tariff sensors + 2 kWh-remaining sensors"`

---

### Task 9: Services — `set_deposit_month` / `set_deposit_starting_block`

**Files:**
- Modify: `custom_components/energy_wallet/__init__.py`
- Modify: `custom_components/energy_wallet/services.yaml`
- Modify: `strings.json`, `translations/en.json`, `translations/pl.json`
- Test: extend `tests/test_integration.py`

**Interfaces:**
- Consumes: `manager.set_deposit_month`, `manager.set_deposit_starting_block` (Task 6).

- [ ] **Step 1: Write the failing test**

```python
async def test_set_deposit_month_service_updates_sensor(hass: HomeAssistant, setup_states, aioclient_mock) -> None:
    aioclient_mock.get(PSE_RCE_URL, json={"value": []})
    entry = MockConfigEntry(domain=DOMAIN, data=BASE, title="Energy Wallet")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN, "set_deposit_month", {"month": "2026-06", "export": 544.39, "import": 0.0}, blocking=True
    )
    await hass.async_block_till_done()
    blocks = value(hass, entry, "deposit_blocks")
    assert blocks.attributes["suma_pozostalo"] == pytest.approx(544.39)

    with pytest.raises(Exception):
        await hass.services.async_call(DOMAIN, "set_deposit_month", {"month": "2026-07"}, blocking=True)
```

- [ ] **Step 2: Run to verify it fails** — not executable here; expect, for real,
`ServiceNotFound: energy_wallet.set_deposit_month`.

- [ ] **Step 3: Implement**

`services.yaml` — append:

```yaml
set_deposit_month:
  fields:
    month:
      required: true
      example: "2026-08"
      selector:
        text:
    value:
      required: false
      example: 150.0
      selector:
        number:
          min: -100000
          max: 100000
          step: any
          mode: box
          unit_of_measurement: PLN
    export:
      required: false
      example: 544.39
      selector:
        number:
          min: 0
          max: 100000
          step: any
          mode: box
          unit_of_measurement: PLN
    import:
      required: false
      example: 0.0
      selector:
        number:
          min: 0
          max: 100000
          step: any
          mode: box
          unit_of_measurement: PLN
    config_entry_id:
      required: false
      selector:
        config_entry:
          integration: energy_wallet

set_deposit_starting_block:
  fields:
    origin:
      required: true
      example: "2025-12"
      selector:
        text:
    amount:
      required: true
      example: 787.32
      selector:
        number:
          min: 0
          max: 100000
          step: any
          mode: box
          unit_of_measurement: PLN
    config_entry_id:
      required: false
      selector:
        config_entry:
          integration: energy_wallet
```

`__init__.py` — add schemas next to `SET_BALANCE_YEAR_SCHEMA`:

```python
SERVICE_SET_DEPOSIT_MONTH = "set_deposit_month"
SET_DEPOSIT_MONTH_SCHEMA = vol.Schema(
    {
        vol.Required("month"): vol.Match(r"^\d{4}-\d{2}$"),
        vol.Optional("value"): vol.Coerce(float),
        vol.Optional("export"): vol.Coerce(float),
        vol.Optional("import"): vol.Coerce(float),
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_SET_DEPOSIT_STARTING_BLOCK = "set_deposit_starting_block"
SET_DEPOSIT_STARTING_BLOCK_SCHEMA = vol.Schema(
    {
        vol.Required("origin"): vol.Match(r"^\d{4}-\d{2}$"),
        vol.Required("amount"): vol.Coerce(float),
        vol.Optional("config_entry_id"): cv.string,
    }
)
```

and, in `async_setup` next to the `set_balance_year` registration:

```python
    async def set_deposit_month(call: ServiceCall) -> None:
        entry_id = call.data.get("config_entry_id")
        if "value" not in call.data and "export" not in call.data and "import" not in call.data:
            raise ServiceValidationError("Provide 'value', or 'export'/'import'.")
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if entry_id in (None, entry.entry_id):
                entry.runtime_data.set_deposit_month(
                    call.data["month"], call.data.get("export"), call.data.get("import"), call.data.get("value")
                )

    hass.services.async_register(
        DOMAIN, SERVICE_SET_DEPOSIT_MONTH, set_deposit_month, schema=SET_DEPOSIT_MONTH_SCHEMA
    )

    async def set_deposit_starting_block(call: ServiceCall) -> None:
        entry_id = call.data.get("config_entry_id")
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if entry_id in (None, entry.entry_id):
                entry.runtime_data.set_deposit_starting_block(call.data["origin"], call.data["amount"])

    hass.services.async_register(
        DOMAIN, SERVICE_SET_DEPOSIT_STARTING_BLOCK, set_deposit_starting_block,
        schema=SET_DEPOSIT_STARTING_BLOCK_SCHEMA,
    )
```

Add `from homeassistant.exceptions import ServiceValidationError` to `__init__.py`'s imports.

Add service translation entries (mirror the existing `set_balance_year` block's nesting) for
`set_deposit_month` and `set_deposit_starting_block` in all three translation files.

- [ ] **Step 4: Run to verify it passes** — not executable here; re-read against the test,
double-check the `ServiceValidationError` import path is correct for the HA version pinned by
`requirements_test.txt` (it has been stable at `homeassistant.exceptions.ServiceValidationError`
for several years — verify against the installed `homeassistant` version once tests run for real).

- [ ] **Step 5: Commit**

`git add -A && git commit -m "Register set_deposit_month / set_deposit_starting_block services"`

---

### Task 10: Final pass

**Files:** none new; review only.

- [ ] **Step 1:** Re-read the full diff since branching from `master` (`git diff master...HEAD`).
  Confirm every item in the spec's "New entities" table (Task 7/8) and "New services" section
  (Task 9) has a matching implementation, and that the deliberate drop of
  `sensor.depozyt_fifo_biezacy_miesiac` (spec's self-review catch) is not silently reintroduced.
- [ ] **Step 2:** Confirm `manifest.json`'s `version` needs bumping (it was bumped to `0.1.3` for
  the earlier `clear_balance_year` fix already merged to `master` — bump again, e.g. `0.1.4`, since
  this is a second, separate user-facing change) — ask the user to confirm the new version number
  rather than guessing.
- [ ] **Step 3:** Tell the user explicitly: this branch's HA-dependent tests (Tasks 5, 7, 8, 9) were
  written and reviewed but **not executed** (no network/pytest in this sandbox) — they must run
  `pytest tests/` (via `requirements_test.txt`) locally or in CI before merging, and should follow
  the spec's "Rollout / PyScript removal checklist" section before deleting `depozyt_fifo.py`.
- [ ] **Step 4:** Final commit if Steps 1–2 produced any fixups; otherwise the branch is done.
