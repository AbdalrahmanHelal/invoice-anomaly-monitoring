"""Tests for the orchestration layer: the new_keys eligibility filter and
the SQLite dedup guard. These are the two mechanisms that make repeated
monitoring cycles over the same vendor ledger safe and non-repetitive.
"""
from sqlalchemy import select

from invoice_anomaly_demo.db.models import Alert
from invoice_anomaly_demo.detection.engine import (
    evaluate_vendor_ledger,
    persist_candidates,
    run_monitoring_cycle,
)

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


def test_new_keys_filters_out_purely_historical_matches():
    """A vendor's full ledger can contain several invoices that all match a
    rule's threshold -- e.g. three separate historical spikes -- but only
    the ones flagged as part of THIS cycle's new activity may become
    alerts. This is what stops a vendor's entire multi-year history from
    flooding out as candidates the first time it happens to be evaluated.
    """
    old1 = make_invoice(amount="45000.00", posting_date="2023-06-01", invoice_id="OLD-A")
    old2 = make_invoice(amount="150000.00", posting_date="2023-09-01", invoice_id="OLD-B")
    new = make_invoice(amount="400000.00", posting_date="2023-12-01", invoice_id="NEW")
    baseline = [
        make_invoice(amount="1000.00", posting_date=f"2023-01-{d:02d}", invoice_id=f"BASE-{d}")
        for d in range(1, 11)
    ]
    ledger = baseline + [old1, old2, new]

    # Sanity check: without the filter, all three big invoices are genuine matches.
    unfiltered = evaluate_vendor_ledger(ledger, _RULES_CONFIG)
    assert len(unfiltered) == 3

    filtered = evaluate_vendor_ledger(ledger, _RULES_CONFIG, new_keys={"NEW"})

    assert len(filtered) == 1
    assert filtered[0].invoice is new


def test_new_keys_none_means_no_filtering():
    ledger = [
        make_invoice(amount="1000.00", posting_date="2024-01-01"),
        make_invoice(amount="1000.00", posting_date="2024-02-01"),
        make_invoice(amount="1000.00", posting_date="2024-03-01"),
        make_invoice(amount="5000.00", posting_date="2024-04-01"),
    ]

    assert len(evaluate_vendor_ledger(ledger, _RULES_CONFIG)) == 1


def test_persist_candidates_dedups_on_rerun(db_session):
    """The same candidate persisted twice must only ever create one Alert
    row -- this is what makes re-running a monitoring cycle over unchanged
    data safe."""
    inv = make_invoice(amount="5000.00", posting_date="2024-01-01", invoice_id="DOC-1")
    ledger = [
        make_invoice(amount="1000.00", posting_date="2023-01-01"),
        make_invoice(amount="1000.00", posting_date="2023-02-01"),
        make_invoice(amount="1000.00", posting_date="2023-03-01"),
        inv,
    ]
    candidates = evaluate_vendor_ledger(ledger, _RULES_CONFIG, new_keys={"DOC-1"})

    first = persist_candidates(db_session, candidates)
    db_session.commit()
    second = persist_candidates(db_session, candidates)
    db_session.commit()

    assert first.alerts_created == 1
    assert first.alerts_skipped == 0
    assert second.alerts_created == 0
    assert second.alerts_skipped == 1

    rows = db_session.execute(select(Alert)).scalars().all()
    assert len(rows) == 1


def test_run_monitoring_cycle_only_touches_vendors_with_activity_today(db_session):
    history = {
        "V1": [make_invoice(vendor_id="V1", amount="1000.00", posting_date="2024-01-01")],
        "V2": [make_invoice(vendor_id="V2", amount="1000.00", posting_date="2024-01-01")],
    }
    todays_activity = {
        "V1": [make_invoice(vendor_id="V1", amount="1000.00", posting_date="2024-06-01", invoice_id="V1-NEW")]
    }

    result = run_monitoring_cycle(db_session, _RULES_CONFIG, history, todays_activity)

    assert result.vendors_checked == 1  # V2 had no activity today, never touched
