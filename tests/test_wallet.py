from datetime import datetime, timedelta, timezone

import pytest

from custom_components.energy_wallet.wallet import EXPORT, IMPORT, Wallet, slot_start

T0 = datetime(2026, 10, 14, 10, 7, tzinfo=timezone.utc)


def feed(wallet, kind, value, ts, resets=False):
    delta, rejected = wallet.ingest_reading(kind, value, ts, resets_allowed=resets, max_power_kw=50)
    if delta:
        wallet.add_energy(kind, delta, ts, ts.date().isoformat())
    return delta, rejected


def test_first_reading_is_baseline():
    w = Wallet()
    assert feed(w, IMPORT, 16.5, T0) == (0.0, False)
    assert feed(w, IMPORT, 16.7, T0 + timedelta(minutes=5))[0] == pytest.approx(0.2)


def test_total_glitch_to_zero_is_not_double_counted():
    w = Wallet()
    feed(w, IMPORT, 16.5, T0)
    feed(w, IMPORT, 0.0, T0 + timedelta(minutes=1))
    assert feed(w, IMPORT, 16.6, T0 + timedelta(minutes=2))[0] == pytest.approx(0.1)


def test_total_increasing_reset_counts_new_value():
    w = Wallet()
    feed(w, EXPORT, 5.0, T0, resets=True)
    assert feed(w, EXPORT, 0.3, T0 + timedelta(minutes=10), resets=True)[0] == pytest.approx(0.3)


def test_implausible_jump_is_rejected_and_rebased():
    w = Wallet()
    feed(w, IMPORT, 1.0, T0)
    assert feed(w, IMPORT, 500.0, T0 + timedelta(minutes=1)) == (0.0, True)
    assert feed(w, IMPORT, 500.1, T0 + timedelta(minutes=2))[0] == pytest.approx(0.1)


def test_settle_waits_for_price_then_prices():
    w = Wallet()
    feed(w, EXPORT, 0.0, T0)
    feed(w, EXPORT, 1.0, T0 + timedelta(minutes=5))
    prices = {}

    def rev(start):
        return prices.get(start)

    assert not w.settle(lambda s: 1.0, rev, T0)
    assert w.summary("2026-10-14", "rce")["rev"] == 0
    prices[datetime.fromtimestamp(slot_start(T0), tz=timezone.utc)] = 0.4
    assert w.settle(lambda s: 1.0, rev, T0)
    assert w.summary("2026-10-14", "rce")["rev"] == pytest.approx(0.4)
    assert w.summary("2026-10-14", "rce", multiplier=1.23)["rev"] == pytest.approx(0.492)
    assert not w.pending


def test_unpriced_after_expiry():
    w = Wallet()
    feed(w, IMPORT, 0.0, T0)
    feed(w, IMPORT, 2.0, T0 + timedelta(minutes=5))
    w.settle(lambda s: None, lambda s: None, T0 + timedelta(days=4))
    s = w.summary("2026-10", "rce")
    assert s["imp"] == pytest.approx(2.0)
    assert s["imp_unpriced"] == pytest.approx(2.0)
    assert s["cost"] == 0


def test_summary_balance_and_rcem():
    w = Wallet()
    w.days = {
        "2026-10-01": {"imp": 10, "exp": 4, "cost": 9.0, "rev": 1.0},
        "2026-10-02": {"imp": 5, "exp": 6, "cost": 4.5, "rev": 2.0},
        "2026-09-30": {"imp": 1, "exp": 1, "cost": 1.0, "rev": 0.5},
    }
    month = w.summary("2026-10", "rce")
    assert month["cost"] == pytest.approx(13.5)
    assert month["balance"] == pytest.approx(3.0 - 13.5)
    w.record_rce_day("2026-10-01", 300)
    w.record_rce_day("2026-10-02", 500)
    assert w.rcem("2026-10") == pytest.approx(0.4)
    assert w.summary("2026-10", "rcem")["rev"] == pytest.approx(10 * 0.4)
    w.rcem_override["2026-10"] = 0.25
    assert w.summary("2026-10", "rcem")["rev"] == pytest.approx(2.5)
    year = w.summary("2026", "rcem")
    assert year["exp_unpriced"] == pytest.approx(1)  # no RCEm for September


def test_round_trip_and_prune():
    w = Wallet()
    w.days = {"2023-01-01": {"imp": 1}, "2026-10-14": {"imp": 2}}
    w.prune("2026-10-14")
    assert list(Wallet(w.as_dict()).days) == ["2026-10-14"]


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
