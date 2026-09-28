import datetime
from decimal import Decimal

from invoice_anomaly_demo.domain import Invoice
from invoice_anomaly_demo.review import run_review

CFG = {
    "rules": {
        "duplicate": {"enabled": True, "severity": "high", "min_count": 2, "normal_repeat_rate": 0.10},
        "same_day_count": {"enabled": True, "severity": "medium", "max_documents_per_day": 5},
        "amount_spike": {"enabled": True, "severity": "high", "percent_above": 50,
                         "comparison_months": 12, "min_history_count": 3},
    }
}
D0 = datetime.date(2026, 1, 1)


def inv(vid, iid, day, amount):
    return Invoice(vid, f"Vendor {vid}", iid, D0 + datetime.timedelta(days=day), Decimal(amount))


def steady(vid, n=20, amount=1000):
    return [inv(vid, f"{vid}-{k}", k * 7, amount + k) for k in range(n)]


def test_duplicate_pair_is_one_finding_with_overpayment():
    ledger = steady("V1") + [inv("V1", "X1", 200, 5000), inv("V1", "X2", 200, 5000)]
    s = run_review(ledger, CFG, new_since=D0 + datetime.timedelta(days=200))
    dups = [f for f in s.findings if f.rule_name == "duplicate"]
    assert len(dups) == 1
    assert [i.invoice_id for i in dups[0].invoices] == ["X1", "X2"]
    assert dups[0].potential_overpayment == Decimal(5000)
    assert s.potential_overpayment == Decimal(5000)


def test_monitoring_mode_ignores_history():
    ledger = steady("V1") + [inv("V1", "BIG", 50, 90000)]  # spike in the past
    s = run_review(ledger, CFG, new_since=D0 + datetime.timedelta(days=130))
    assert s.findings == []


def test_audit_mode_finds_past_spike_after_warmup():
    ledger = steady("V1") + [inv("V1", "BIG", 100, 90000)]
    s = run_review(ledger, CFG, new_since=None, warmup_days=30)
    assert [f.invoices[0].invoice_id for f in s.findings] == ["BIG"]
    assert s.amount_under_review == Decimal(90000)


def test_audit_warmup_suppresses_early_history():
    ledger = steady("V1") + [inv("V1", "BIG", 100, 90000)]
    s = run_review(ledger, CFG, new_since=None, warmup_days=120)
    assert s.findings == []


def test_amount_under_review_counts_each_invoice_once():
    # 6 same-amount invoices in one day trip both duplicate and same_day_count
    ledger = steady("V1") + [inv("V1", f"B{k}", 200, 700) for k in range(6)]
    s = run_review(ledger, CFG, new_since=D0 + datetime.timedelta(days=200))
    assert {f.rule_name for f in s.findings} == {"duplicate", "same_day_count"}
    assert s.amount_under_review == Decimal(700 * 6)
