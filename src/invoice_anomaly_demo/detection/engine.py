"""Detection engine.

Two layers, split for testability:

  - `evaluate_vendor_ledger` is pure: runs the enabled rules against one
    vendor's full ledger and returns candidates. No DB, no I/O. A vendor's
    *full* ledger (history + today) is always used as context -- duplicate
    grouping, same-day counts, and the amount-spike window all need the
    complete picture to know what is normal for that vendor -- but if
    `new_keys` is given, candidates are filtered down to only invoices in
    that set before returning.

    This filter is not optional in a real system. Without it, the first
    time any vendor is evaluated, its entire history would surface as "new"
    candidates simultaneously: a single new invoice for a vendor whose
    baseline happens to contain a few old outliers would flood out alerts
    for invoices from years ago, alongside the one thing that actually just
    happened. A dedup guard on write only stops the SAME invoice+rule from
    alerting *twice* -- it does nothing to stop that first-touch flood the
    first time a vendor is looked at, which is exactly why the filter has
    to live here, before persistence, not after it.

  - `persist_candidates` applies the SQLite dedup guard and writes new
    `Alert` rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from invoice_anomaly_demo.db.models import Alert
from invoice_anomaly_demo.detection.rules.amount_spike import AmountSpikeRule
from invoice_anomaly_demo.detection.rules.base import AlertCandidate
from invoice_anomaly_demo.detection.rules.duplicate import DuplicateRule
from invoice_anomaly_demo.detection.rules.same_day_count import SameDayCountRule
from invoice_anomaly_demo.domain import Invoice

_ALL_RULES = [DuplicateRule(), SameDayCountRule(), AmountSpikeRule()]


@dataclass
class DetectResult:
    vendors_checked: int
    alerts_created: int
    alerts_skipped: int  # dedup guard blocked re-creation
    candidates: list[AlertCandidate] = field(default_factory=list)


def evaluate_vendor_ledger(
    ledger: list[Invoice],
    rules_config: dict,
    new_keys: set[str] | None = None,
) -> list[AlertCandidate]:
    """Run every enabled rule against one vendor's full ledger.

    `new_keys`, if given, is the set of `invoice_id`s eligible to become
    candidates -- everything else in `ledger` is context only. See the
    module docstring for why this matters.
    """
    rules_section: dict = rules_config.get("rules", {})
    candidates: list[AlertCandidate] = []

    for rule in _ALL_RULES:
        rule_cfg = rules_section.get(rule.name, {})
        if not rule_cfg.get("enabled", True):
            continue
        rule_candidates = rule.run(ledger, rules_section)
        if new_keys is not None:
            rule_candidates = [c for c in rule_candidates if c.invoice.invoice_id in new_keys]
        candidates.extend(rule_candidates)

    return candidates


def persist_candidates(session: Session, candidates: list[AlertCandidate]) -> DetectResult:
    """Apply the dedup guard and write new Alert rows for the given candidates."""
    result = DetectResult(vendors_checked=0, alerts_created=0, alerts_skipped=0)

    for candidate in candidates:
        inv = candidate.invoice

        existing = session.execute(
            select(Alert).where(
                Alert.vendor_id == inv.vendor_id,
                Alert.invoice_id == inv.invoice_id,
                Alert.rule_name == candidate.rule_name,
            )
        ).scalar_one_or_none()

        if existing is not None:
            result.alerts_skipped += 1
            continue

        session.add(
            Alert(
                vendor_id=inv.vendor_id,
                vendor_name=inv.vendor_name,
                invoice_id=inv.invoice_id,
                amount=inv.amount,
                posting_date=inv.posting_date,
                rule_name=candidate.rule_name,
                severity=candidate.severity,
                message=candidate.message,
            )
        )
        result.alerts_created += 1
        result.candidates.append(candidate)

    return result


def run_monitoring_cycle(
    session: Session,
    rules_config: dict,
    history_by_vendor: dict[str, list[Invoice]],
    todays_activity_by_vendor: dict[str, list[Invoice]],
) -> DetectResult:
    """The orchestration a real run performs: for every vendor with activity
    today, build their full ledger (history + today), evaluate it, and
    persist genuinely new alerts.

    Vendors with no activity today are never touched -- there is nothing new
    to alert on, and re-scanning a dormant vendor's full history for no
    reason is wasted work in a system where "full history" can span years.
    """
    total = DetectResult(vendors_checked=0, alerts_created=0, alerts_skipped=0)

    for vendor_id, todays_invoices in todays_activity_by_vendor.items():
        history = history_by_vendor.get(vendor_id, [])
        ledger = history + todays_invoices
        new_keys = {inv.invoice_id for inv in todays_invoices}

        candidates = evaluate_vendor_ledger(ledger, rules_config, new_keys=new_keys)
        vendor_result = persist_candidates(session, candidates)

        total.vendors_checked += 1
        total.alerts_created += vendor_result.alerts_created
        total.alerts_skipped += vendor_result.alerts_skipped
        total.candidates.extend(vendor_result.candidates)

    return total
