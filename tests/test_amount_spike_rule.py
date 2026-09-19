"""Tests for the amount-spike rule's trailing-window baseline."""
from invoice_anomaly_demo.detection.engine import evaluate_vendor_ledger

from _helpers import make_invoice

_RULES_CONFIG = {
    "rules": {
        "duplicate": {"enabled": False},
        "same_day_count": {"enabled": False},
        "amount_spike": {
            "enabled": True,
            "severity": "high",
            "percent_above": 50,
            "comparison_months": 12,
            "min_history_count": 3,
        },
    }
}


def _history(entries: list[tuple[str, str]]):
    return [make_invoice(amount=amount, posting_date=date) for date, amount in entries]


def test_fires_when_more_than_50_percent_over_the_years_high():
    ledger = _history(
        [
            ("2024-01-10", "1000.00"),
            ("2024-02-10", "1000.00"),
            ("2024-03-10", "1000.00"),
            ("2024-04-10", "1600.00"),
        ]
    )

    candidates = evaluate_vendor_ledger(ledger, _RULES_CONFIG)

    assert len(candidates) == 1
    assert candidates[0].invoice is ledger[-1]


def test_does_not_fire_at_exactly_50_percent_over():
    ledger = _history(
        [
            ("2024-01-10", "1000.00"),
            ("2024-02-10", "1000.00"),
            ("2024-03-10", "1000.00"),
            ("2024-04-10", "1500.00"),
        ]
    )

    assert evaluate_vendor_ledger(ledger, _RULES_CONFIG) == []


def test_baseline_is_the_years_high_not_a_typical_amount():
    """An invoice below the vendor's actual historical peak must not fire,
    even if it looks very large next to their smaller, more common
    invoices."""
    ledger = _history(
        [
            ("2026-01-10", "1000.00"),
            ("2026-02-10", "1000.00"),
            ("2026-03-10", "1000.00"),
            ("2026-04-10", "1800000.00"),
            ("2026-05-10", "448203.50"),
        ]
    )

    candidates = evaluate_vendor_ledger(ledger, _RULES_CONFIG)

    flagged = {c.invoice for c in candidates}
    assert ledger[-1] not in flagged  # below the 1,800,000 peak -- stays quiet
    assert ledger[3] in flagged  # the 1,800,000 invoice IS a genuine new high


def test_old_high_outside_the_window_no_longer_suppresses():
    """A record set more than comparison_months ago must age out, so a
    later, smaller "new high" still fires."""
    ledger = _history(
        [
            ("2024-01-10", "900000.00"),  # more than a year before the last invoice
            ("2026-01-10", "1000.00"),
            ("2026-02-10", "1000.00"),
            ("2026-03-10", "1000.00"),
            ("2026-04-10", "5000.00"),
        ]
    )

    candidates = evaluate_vendor_ledger(ledger, _RULES_CONFIG)

    assert len(candidates) == 1
    assert candidates[0].invoice is ledger[-1]


def test_high_inside_the_window_does_suppress():
    ledger = _history(
        [
            ("2025-06-10", "900000.00"),  # within a year of the last invoice
            ("2026-01-10", "1000.00"),
            ("2026-02-10", "1000.00"),
            ("2026-03-10", "1000.00"),
            ("2026-04-10", "5000.00"),
        ]
    )

    assert evaluate_vendor_ledger(ledger, _RULES_CONFIG) == []


def test_cold_start_guard_blocks_alerts_on_thin_history():
    ledger = _history([("2024-01-10", "1000.00"), ("2024-02-10", "50000.00")])

    assert evaluate_vendor_ledger(ledger, _RULES_CONFIG) == []


def test_zero_baseline_does_not_alert():
    ledger = _history(
        [
            ("2024-01-10", "0.00"),
            ("2024-02-10", "0.00"),
            ("2024-03-10", "0.00"),
            ("2024-04-10", "500.00"),
        ]
    )

    assert evaluate_vendor_ledger(ledger, _RULES_CONFIG) == []


def test_message_has_no_statistical_jargon():
    ledger = _history(
        [
            ("2026-01-10", "1000.00"),
            ("2026-02-10", "1000.00"),
            ("2026-03-10", "1200.00"),
            ("2026-04-10", "9000.00"),
        ]
    )

    message = evaluate_vendor_ledger(ledger, _RULES_CONFIG)[-1].message

    assert "9,000" in message
    for banned in ("median", "mad", "threshold", "k="):
        assert banned not in message.lower()
