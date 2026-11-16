# Deposit FIFO migration — design spec

Date: 2026-11-16
Status: approved by user in chat, pending spec review

## Context / intent

A standalone PyScript file (`depozyt_fifo.py`, user-uploaded) currently implements three
independent pieces of logic as global PyScript state/services, entirely outside the
`energy_wallet` custom integration:

1. **Deposit FIFO** — net-billing deposit block tracking with 12-month expiry (FIFO
   consumption: imports deplete the oldest active block first).
2. **Taryfa G12** — six static tariff cost sensors, computed once from hard-coded
   PLN/kWh constants.
3. **Ile kWh zostało w depozycie** — two sensors dividing a deposit balance by a G12
   sell price to show remaining kWh.

Goal: delete the PyScript entirely and reimplement all three as part of the
`energy_wallet` custom integration (same config entry / device as the existing wallet
sensors), replacing hard-coded personal data with config-flow fields, since
`energy_wallet` is a public GitHub project (`xamrex/Ha-EnergyWallet`) that other people
may install.

Decisions already made with the user (do not re-litigate during implementation):

- All historical/tariff numbers become config-flow fields or service calls. **Nothing
  user-specific is hard-coded in source.**
- The FIFO engine's "export"/"import" per month are **the same real-world numbers** as
  `energy_wallet`'s own `purchase_cost_<period>` / `sale_revenue_<period>`. For any month
  covered by the wallet's own tracked days, derive export/import directly from
  `Wallet.summary(month_prefix)` — **no new external sensor config fields**, no
  `sensor.pvzarobek` / `sensor.cena_zakupu_energii_bez_dystrybucji_narastajaco`
  dependency, no month-end rollover/snapshot bookkeeping (`last_pv`/`last_koszt`
  concept is dropped entirely).
- Months that predate `energy_wallet`'s own tracking (Jan–Oct 2026, official data from
  the seller's panel) are seeded **once, after deployment**, via a per-month service
  call (`energy_wallet.set_deposit_month`), not baked into source.
- New entities get **new entity_ids** following the `energy_wallet` naming scheme
  (`sensor.energy_wallet_...`), not the old PyScript ids. User accepted this and will
  update any dashboard cards referencing the old ids.
- G12 tariff sensors take **gross (brutto) values directly** as config fields — no VAT
  multiplier field, no VAT math in code.
- The "ile kWh zostało w depozycie" sensors divide by the new
  `stan_rzeczywisty_depozytu` (FIFO's true remaining balance) instead of the old
  external `sensor.depozyt` (`pvzarobek - koszt + 60`). The `+60` bonus/credit is out of
  scope — it is not part of FIFO and nothing replaces it.
- **Self-review catch:** the PyScript's `sensor.depozyt_fifo_biezacy_miesiac` ("net
  change in deposit this month") is, once export/import are derived from
  `wallet.summary()`, mathematically identical to the *already existing*
  `sensor.energy_wallet_bilans_w_tym_miesiacu` (`rev - cost` for the current month). It
  is **not** reimplemented — point to the existing balance-month sensor instead.

## Non-goals

- No change to existing wallet sensors/services (`balance_year` override behavior is a
  separate, already-in-flight fix on branch `fix/clear-balance-year-service`).
- No attempt to preserve old PyScript entity_ids.
- No VAT computation — G12 sensors store/display gross values as entered.
- No generic "deposit expiry" product for other net-billing schemes beyond what the
  PyScript already modeled (single FIFO queue, whole-month granularity).

## Architecture

New pure-logic module `custom_components/energy_wallet/deposit.py` (no HA imports, unit
testable like `wallet.py`):

- `Bucket` shape: `{origin, initial, remaining, expiry, is_estimate, forfeited, expired}`
  — ported from `_make_bucket` / `_simulate` in the PyScript, same FIFO/expiry
  semantics (oldest active block consumed first; a block fully past its expiry date
  forfeits its `remaining` and is marked `expired`).
- `simulate(start_block, events, expiry_months, today) -> (buckets, ledger)` — stateless
  and idempotent like the original `_simulate`; `events` is a chronologically sorted
  list of `{month, export, import}`.
- `status(bucket, today, warning_days) -> "aktywny"|"wygasa_wkrotce"|"wykorzystany"|"wygasl"`

`WalletManager` (manager.py) gains:

- `deposit_events(self) -> list[dict]` — builds the combined, chronologically sorted
  event list each time it's called (always live, no cached/derived state):
  - For every month key in `self.wallet.deposit_history` → use that record verbatim
    (authoritative/manual; covers the pre-tracking historical months).
  - For every month prefix present in `self.wallet.days` that is **not** already in
    `deposit_history` → aggregate `cost`/`rev` across that month's days into one
    `{month, export: rev, import: cost}` event. This naturally includes the
    current, still-open month — recomputed fresh on every read, no rollover.
  - Sort combined list by month ascending before returning.
- `deposit_buckets(self)` / `deposit_ledger(self)` — thin wrappers calling
  `deposit.simulate(self.wallet.deposit_start_block, self.deposit_events(), ...)`,
  parameterized by the configured `deposit_expiry_months`.
- `set_deposit_month(month, export=None, import_=None, value=None)` — validates exactly
  one of (`value`) or (`export`/`import_`) is given (same contract as the PyScript's
  `ustaw_miesiac`), writes/overwrites `self.wallet.deposit_history[month]`, persists via
  the existing debounced store save, no `_settle()`/dispatch needed beyond
  `async_dispatcher_send` to refresh sensors.
- `set_deposit_starting_block(origin, amount)` — sets
  `self.wallet.deposit_start_block = {"origin": origin, "amount": amount,
  "is_estimate": True}`.

`Wallet` (wallet.py) gains two new persisted fields (same `Store`, no new file):

```python
self.deposit_history: dict[str, dict[str, float]] = data.get("deposit_history", {})
self.deposit_start_block: dict[str, Any] | None = data.get("deposit_start_block")
```
included in `as_dict()` alongside the existing fields.

## Config additions (`const.py` + `config_flow.py`)

All new fields are `vol.Optional` with defaults, appended to the existing single
`full_schema()` form (both initial setup and options flow reuse it, per current
pattern) so upgrading an existing config entry doesn't force re-entering everything:

| Const | Key | Default | Selector |
|---|---|---|---|
| `CONF_DEPOSIT_EXPIRY_MONTHS` | `deposit_expiry_months` | 12 | Number, min 1 max 36, step 1 |
| `CONF_DEPOSIT_WARNING_DAYS` | `deposit_warning_days` | 60 | Number, min 0 max 365, step 1 |
| `CONF_G12_DIST_FIXED` | `g12_distribution_fixed` | 0 | MONTHLY-style (PLN) |
| `CONF_G12_SALE_FIXED` | `g12_sale_fixed` | 0 | MONTHLY-style (PLN) |
| `CONF_G12_DIST_DAY` | `g12_distribution_day` | 0 | PRICE-style (PLN/kWh) |
| `CONF_G12_DIST_NIGHT` | `g12_distribution_night` | 0 | PRICE-style (PLN/kWh) |
| `CONF_G12_SALE_DAY` | `g12_sale_day` | 0 | PRICE-style (PLN/kWh) |
| `CONF_G12_SALE_NIGHT` | `g12_sale_night` | 0 | PRICE-style (PLN/kWh) |

(Reuse the existing `PRICE`/`MONTHLY` selector builders in `config_flow.py`.)

## New entities (`sensor.py`)

All on the existing "Energy Wallet" device, `_attr_has_entity_name = True` (entity_ids
below are the expected result given the device name, not hard-coded):

| translation_key | expected entity_id | value | key attributes |
|---|---|---|---|
| `deposit_oldest_block` | `sensor.energy_wallet_najstarszy_blok_depozytu` | oldest active bucket's `remaining` (0 if none) | `origin`, `is_estimate`, `initial`, `expiry`, `days_left` |
| `deposit_expiring_soon` | `sensor.energy_wallet_depozyt_wygasajacy_wkrotce` | sum of `remaining` for buckets with status `wygasa_wkrotce` | `bloki: [{origin, remaining, expiry}]` |
| `deposit_blocks` | `sensor.energy_wallet_bloki_depozytu` | count of active buckets | `bloki: [...]` (full breakdown), `historia_miesieczna: [...]` (reversed ledger), `suma_pozostalo` |
| `deposit_actual_balance` | `sensor.energy_wallet_stan_rzeczywisty_depozytu` | sum of all buckets' `remaining` | `suma_przepadla_dotychczas` |
| `g12_distribution_fixed` / `g12_sale_fixed` / `g12_distribution_day` / `g12_distribution_night` / `g12_sale_day` / `g12_sale_night` | `sensor.energy_wallet_g12_*` | the matching config value, as-is | — |
| `deposit_kwh_remaining_day` / `deposit_kwh_remaining_night` | `sensor.energy_wallet_kwh_w_depozycie_dzien` / `..._noc` | `deposit_actual_balance / g12_sale_day` (or `_night`); `None` if price is 0 | — |

Device class / unit: monetary + PLN for all deposit PLN sensors and G12 sensors
(`state_class: total` only for the ones that represent a point-in-time balance, matching
how the PyScript tagged them); `deposit_blocks` has no device class (it's a count, like
the old `len(active)`); the two kWh sensors use `unit_of_measurement: "kWh"`, no device
class.

Translations: add matching `strings.json` / `translations/en.json` / `translations/pl.json`
entries for every new `translation_key`, Polish names mirroring the old PyScript
`friendly_name`s where natural (e.g. "Depozyt - najstarszy aktywny blok").

## New services (`services.yaml` + `__init__.py`)

```yaml
set_deposit_month:
  fields:
    month: {required: true, example: "2026-08"}       # YYYY-MM
    value: {required: false, example: 150.0}            # netto PLN; XOR with export/import
    export: {required: false, example: 544.39}
    import: {required: false, example: 0.0}
    config_entry_id: {required: false}

set_deposit_starting_block:
  fields:
    origin: {required: true, example: "2025-12"}        # YYYY-MM
    amount: {required: true, example: 787.32}
    config_entry_id: {required: false}
```

Validation: `set_deposit_month` requires either `value` alone, or at least one of
`export`/`import` — reject (raise `ServiceValidationError`) if neither is given, mirroring
the PyScript's warning-and-no-op behavior but as a proper HA validation error.

No `recompute`/`przelicz` service (sensors always compute live) and no
`ustaw_bazowe_pv_koszt` equivalent (no external snapshot to repair).

## Data flow / precedence summary

```
deposit_events()
  = sorted(
      [deposit_history[m] for m in deposit_history]
      + [aggregate(wallet.days matching m) for m in months(wallet.days) if m not in deposit_history]
    )

simulate(deposit_start_block, deposit_events(), expiry_months)
  -> buckets, ledger   (pure function, recomputed on every sensor read)
```

An explicit `set_deposit_month` call always wins over whatever `wallet.days` would
derive for that same month — this lets a user correct a month even if `energy_wallet`
happened to also have metered data for it.

## Testing

- `tests/test_deposit.py` (new, mirrors `tests/test_wallet.py` conventions): bucket
  creation/expiry math, FIFO consumption across multiple buckets including partial
  consumption spanning 2+ buckets, expiry forfeiture, starting-block seeding, empty
  state.
- Extend `tests/test_integration.py` (or add `tests/test_deposit_integration.py`) for:
  the new sensors appearing with correct entity_ids/values against a fake config entry,
  the two new services end-to-end (call service → sensor value changes), and the
  precedence rule (a month present in both `wallet.days` and `deposit_history` uses the
  `deposit_history` value).

## Rollout / PyScript removal checklist (for the user, not automated)

1. Deploy this branch, restart/reload the integration.
2. Call `energy_wallet.set_deposit_starting_block` if applicable (pre-Jan-2026 estimate
   block), then `energy_wallet.set_deposit_month` ten times for 2026-01..2026-10 with the
   official seller figures (same ones currently in `OFFICIAL_EVENTS_V1`).
3. Compare new sensors against the PyScript's current ones side by side (same active
   block count, same `suma_pozostalo`, same oldest-block remaining/expiry) for at least
   one full day so the "current month" live figures have settled.
4. Only once they match: disable/delete `depozyt_fifo.py` from the `pyscript` folder.
   Keep `sensor.pvzarobek` / `sensor.cena_zakupu_energii_bez_dystrybucji_narastajaco` /
   `sensor.depozyt` only if something *else* (outside this script) still depends on
   them — this migration does not touch or require them.
