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
    # start_block is the raw {"origin", "amount"} shape Wallet.deposit_start_block stores -
    # NOT a full make_bucket() dict (simulate() builds the bucket itself via make_bucket).
    start = {"origin": "2025-12", "amount": 200.0}
    buckets, _ledger = simulate(start, [{"month": "2026-01", "export": 0.0, "import": 50.0}], 12, date(2026, 11, 16))
    assert buckets[0]["origin"] == "2025-12"
    assert buckets[0]["remaining"] == 150.0
    assert buckets[0]["is_estimate"] is True
