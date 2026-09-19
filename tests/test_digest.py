from decimal import Decimal

from invoice_anomaly_demo.detection.rules.base import AlertCandidate
from invoice_anomaly_demo.digest import render_digest

from _helpers import make_invoice


def test_empty_digest_says_nothing_new():
    assert "No new alerts" in render_digest([])


def test_digest_orders_by_severity_and_formats_amount():
    low_priority = AlertCandidate(
        invoice=make_invoice(amount="1000.00", posting_date="2024-01-01"),
        rule_name="same_day_count",
        severity="medium",
        message="medium severity case",
    )
    high_priority = AlertCandidate(
        invoice=make_invoice(amount="1800000.00", posting_date="2024-01-02"),
        rule_name="amount_spike",
        severity="high",
        message="high severity case",
    )

    text = render_digest([low_priority, high_priority])

    assert text.index("HIGH") < text.index("MEDIUM")
    assert "1,800,000" in text
    assert "1800000.00" not in text
