"""Tests for the duplicate rule's vendor-specific suppression.

The behavior worth testing here isn't "does it detect two matching
invoices" (trivial) -- it's whether the suppression correctly distinguishes
routine repeat-billing from a genuine first-time duplicate, since that
distinction is the entire reason this rule exists in its current form.
"""
from invoice_anomaly_demo.detection.engine import evaluate_vendor_ledger

from _helpers import make_invoice

_RULES_CONFIG = {
    "rules": {
        "duplicate": {"enabled": True, "severity": "high", "min_count": 2, "normal_repeat_rate": 0.10},
        "same_day_count": {"enabled": False},
        "amount_spike": {"enabled": False},
    }
}


def _background_days(n: int):
    return [make_invoice(amount="1000.00", posting_date=f"2024-01-{day:02d}") for day in range(1, n + 1)]


def test_fires_on_first_time_repeat():
    ledger = _background_days(20)
    ledger += [
        make_invoice(amount="7770.00", posting_date="2024-02-01"),
        make_invoice(amount="7770.00", posting_date="2024-02-01"),
    ]

    candidates = evaluate_vendor_ledger(ledger, _RULES_CONFIG)

    assert len(candidates) == 2
    assert "first time" in candidates[0].message


def test_suppressed_when_repeating_is_routine_for_the_vendor():
    """A vendor repeating an amount on ~40% of days -- clearly routine --
    must not fire, no matter how many times it recurs."""
    ledger = _background_days(10)
    for day in range(1, 9):  # repeats on 8 of 18 total active days
        ledger.append(make_invoice(amount="7770.00", posting_date=f"2024-02-{day:02d}"))
        ledger.append(make_invoice(amount="7770.00", posting_date=f"2024-02-{day:02d}"))

    assert evaluate_vendor_ledger(ledger, _RULES_CONFIG) == []


def test_rare_repeat_still_fires():
    """One prior occurrence out of many active days is rare, not routine --
    still worth flagging."""
    ledger = _background_days(30)
    for day in (5, 25):
        ledger.append(make_invoice(amount="7770.00", posting_date=f"2024-01-{day:02d}"))
        ledger.append(make_invoice(amount="7770.00", posting_date=f"2024-01-{day:02d}"))

    candidates = evaluate_vendor_ledger(ledger, _RULES_CONFIG)

    assert len(candidates) == 4  # both occurrences, both invoices each
    assert "only done this on" in candidates[0].message


def test_the_flagged_day_is_excluded_from_its_own_baseline():
    """A vendor's ONLY repeat day must still be judged as unusual -- it
    cannot count itself as evidence that repeating is normal."""
    ledger = _background_days(10)
    ledger.append(make_invoice(amount="7770.00", posting_date="2024-01-11"))
    ledger.append(make_invoice(amount="7770.00", posting_date="2024-01-11"))

    assert len(evaluate_vendor_ledger(ledger, _RULES_CONFIG)) == 2


def test_different_amounts_same_day_is_not_a_duplicate():
    ledger = [
        make_invoice(amount="5000.00", posting_date="2024-03-01"),
        make_invoice(amount="3000.00", posting_date="2024-03-01"),
    ]

    assert evaluate_vendor_ledger(ledger, _RULES_CONFIG) == []


def test_only_invoices_in_new_keys_become_alerts():
    """The engine's eligibility filter: a matching pair that is entirely
    historical (not part of this cycle's new activity) must not alert."""
    old1 = make_invoice(amount="5000.00", posting_date="2023-01-01", invoice_id="OLD-1")
    old2 = make_invoice(amount="5000.00", posting_date="2023-01-01", invoice_id="OLD-2")
    ledger = _background_days(20) + [old1, old2]

    candidates = evaluate_vendor_ledger(ledger, _RULES_CONFIG, new_keys=set())

    assert candidates == []
