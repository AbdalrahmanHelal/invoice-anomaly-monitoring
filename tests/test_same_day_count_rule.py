"""Tests for the same-day-count rule's new-personal-high logic."""
from invoice_anomaly_demo.detection.engine import evaluate_vendor_ledger

from _helpers import make_invoice

_RULES_CONFIG = {
    "rules": {
        "duplicate": {"enabled": False},
        "same_day_count": {"enabled": True, "severity": "medium", "max_documents_per_day": 5},
        "amount_spike": {"enabled": False},
    }
}


def _n_on(date: str, n: int):
    return [make_invoice(amount=f"{1000 + i}.00", posting_date=date) for i in range(n)]


def test_does_not_fire_below_the_vendors_own_record():
    """The real-world case this rule exists for: once a vendor has
    established a high through routine month-end batching, later batches
    that stay under that record must not fire again, however far above the
    flat floor they are."""
    ledger = (
        _n_on("2024-01-01", 1)
        + _n_on("2024-01-31", 24)  # establishes the record
        + _n_on("2024-02-29", 9)  # well above the floor, but under the record
        + _n_on("2024-03-31", 7)  # same
    )

    candidates = evaluate_vendor_ledger(ledger, _RULES_CONFIG)

    # Only 2024-01-31 set a genuine new high; the later, smaller batches
    # must not fire again even though they are still above the flat floor.
    assert {c.invoice.posting_date.isoformat() for c in candidates} == {"2024-01-31"}


def test_fires_on_a_genuine_new_high_above_the_floor():
    ledger = _n_on("2024-01-01", 1) + _n_on("2024-01-31", 6) + _n_on("2024-06-30", 4) + _n_on("2024-09-01", 8)

    candidates = evaluate_vendor_ledger(ledger, _RULES_CONFIG)

    flagged_dates = {c.invoice.posting_date.isoformat() for c in candidates}
    assert flagged_dates == {"2024-01-31", "2024-09-01"}


def test_new_high_below_the_floor_does_not_fire():
    ledger = _n_on("2024-01-01", 1) + _n_on("2024-02-01", 2) + _n_on("2024-03-01", 3)

    assert evaluate_vendor_ledger(ledger, _RULES_CONFIG) == []


def test_matching_a_record_is_not_a_new_high():
    ledger = _n_on("2024-01-31", 6) + _n_on("2024-02-29", 6)

    candidates = evaluate_vendor_ledger(ledger, _RULES_CONFIG)

    assert {c.invoice.posting_date.isoformat() for c in candidates} == {"2024-01-31"}


def test_records_are_judged_in_date_order_regardless_of_input_order():
    """A later, bigger day must never retroactively suppress an earlier
    genuine record, however the ledger happens to be ordered."""
    ledger = _n_on("2024-06-30", 9) + _n_on("2024-01-31", 6)

    candidates = evaluate_vendor_ledger(ledger, _RULES_CONFIG)

    assert {c.invoice.posting_date.isoformat() for c in candidates} == {"2024-01-31", "2024-06-30"}
