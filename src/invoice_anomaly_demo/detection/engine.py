"""Detection engine: evaluate one vendor's ledger, then persist candidates.

`evaluate_vendor_ledger` is pure -- no DB, no I/O -- and takes an optional
`new_keys` filter so a vendor's full history can be used as rule context
without every historical match becoming an alert (see its docstring).
`persist_candidates` writes candidates through the DB's own dedup
constraint rather than checking first, so a duplicate write is a no-op
instead of a race.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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

    Rules see the *complete* ledger (history + today) so they can judge
    what's normal for that vendor, but if `new_keys` is given, candidates
    are filtered to only invoices in that set -- otherwise a vendor's
    entire history would surface as "new" the first time it's evaluated.
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
    """Write new Alert rows, relying on the DB's unique constraint (not a
    prior SELECT) as the dedup guard: each insert targets
    `ON CONFLICT (vendor_id, invoice_id, rule_name) DO NOTHING`, so a
    duplicate candidate is a genuine no-op at the database level rather
    than a check-then-write that a concurrent writer could race.
    """
    result = DetectResult(vendors_checked=0, alerts_created=0, alerts_skipped=0)

    for candidate in candidates:
        inv = candidate.invoice

        stmt = (
            sqlite_insert(Alert)
            .values(
                vendor_id=inv.vendor_id,
                vendor_name=inv.vendor_name,
                invoice_id=inv.invoice_id,
                amount=inv.amount,
                posting_date=inv.posting_date,
                rule_name=candidate.rule_name,
                severity=candidate.severity,
                message=candidate.message,
            )
            .on_conflict_do_nothing(index_elements=["vendor_id", "invoice_id", "rule_name"])
        )
        inserted = session.execute(stmt).rowcount

        if inserted:
            result.alerts_created += 1
            result.candidates.append(candidate)
        else:
            result.alerts_skipped += 1

    return result


def run_monitoring_cycle(
    session: Session,
    rules_config: dict,
    history_by_vendor: dict[str, list[Invoice]],
    todays_activity_by_vendor: dict[str, list[Invoice]],
) -> DetectResult:
    """For every vendor with activity today, build their full ledger
    (history + today), evaluate it, and persist genuinely new alerts.
    Vendors with no activity today are skipped -- nothing new to alert on.
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
