"""Run the existing rules over an uploaded ledger and shape the output for a
finance reader: one finding per real-world issue (not one per invoice), plus
the money figures a CFO asks about first.

This sits on top of `detection.engine.evaluate_vendor_ledger` and does not
change any rule. It is in-memory only — the SQLite dedup log stays the
CLI's concern.
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from invoice_anomaly_demo.detection.engine import evaluate_vendor_ledger
from invoice_anomaly_demo.detection.rules.base import AlertCandidate
from invoice_anomaly_demo.domain import Invoice

RULE_LABELS = {
    "amount_spike": "Unusually large amount",
    "duplicate": "Possible duplicate invoice",
    "same_day_count": "Unusual number of invoices in one day",
}
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass
class Finding:
    rule_name: str
    severity: str
    vendor_id: str
    vendor_name: str
    posting_date: datetime.date
    invoices: list[Invoice]
    message: str
    #: money that may have been paid twice (duplicates only)
    potential_overpayment: Decimal = Decimal(0)

    @property
    def label(self) -> str:
        return RULE_LABELS.get(self.rule_name, self.rule_name.replace("_", " ").capitalize())

    @property
    def amount_under_review(self) -> Decimal:
        return sum((i.amount for i in self.invoices), start=Decimal(0))


@dataclass
class ReviewSummary:
    findings: list[Finding]
    invoices_scanned: int
    vendors_scanned: int
    period_start: datetime.date | None
    period_end: datetime.date | None
    amount_under_review: Decimal = Decimal(0)
    potential_overpayment: Decimal = Decimal(0)
    vendors_flagged: int = 0
    by_rule: dict[str, int] = field(default_factory=dict)


def _group(candidates: list[AlertCandidate]) -> list[Finding]:
    """Collapse per-invoice candidates into one finding per issue:
    duplicates by (vendor, day, amount), same-day bursts by (vendor, day),
    spikes stay one per invoice."""
    buckets: dict[tuple, list[AlertCandidate]] = defaultdict(list)
    for c in candidates:
        inv = c.invoice
        if c.rule_name == "duplicate":
            key = (c.rule_name, inv.vendor_id, inv.posting_date, inv.amount)
        elif c.rule_name == "same_day_count":
            key = (c.rule_name, inv.vendor_id, inv.posting_date)
        else:
            key = (c.rule_name, inv.vendor_id, inv.invoice_id)
        buckets[key].append(c)

    findings: list[Finding] = []
    for key, group in buckets.items():
        first = group[0]
        invoices = sorted({c.invoice for c in group}, key=lambda i: i.invoice_id)
        overpay = Decimal(0)
        if first.rule_name == "duplicate" and len(invoices) > 1:
            overpay = invoices[0].amount * (len(invoices) - 1)
        findings.append(
            Finding(
                rule_name=first.rule_name,
                severity=first.severity,
                vendor_id=first.invoice.vendor_id,
                vendor_name=first.invoice.vendor_name,
                posting_date=first.invoice.posting_date,
                invoices=invoices,
                message=first.message,
                potential_overpayment=overpay,
            )
        )

    findings.sort(
        key=lambda f: (SEVERITY_RANK.get(f.severity, 9), -f.amount_under_review, f.vendor_name)
    )
    return findings


def run_review(
    invoices: list[Invoice],
    rules_config: dict,
    *,
    new_since: datetime.date | None = None,
    warmup_days: int = 90,
) -> ReviewSummary:
    """Evaluate every vendor against their own full ledger.

    `new_since=None` is an audit of the whole file: every invoice is eligible
    to alert, each judged against the history around it. With a date, only
    invoices posted on/after it can alert (daily-monitoring mode), and the
    rest of the file is context.

    In audit mode the first `warmup_days` of each vendor's history are
    context only: with little history behind them, "first time" and "new
    high" are true of almost everything, so alerting there is noise.
    """
    by_vendor: dict[str, list[Invoice]] = defaultdict(list)
    for inv in invoices:
        by_vendor[inv.vendor_id].append(inv)

    candidates: list[AlertCandidate] = []
    for ledger in by_vendor.values():
        if new_since is not None:
            cutoff = new_since
        else:
            cutoff = min(i.posting_date for i in ledger) + datetime.timedelta(days=warmup_days)
        new_keys = {i.invoice_id for i in ledger if i.posting_date >= cutoff}
        if not new_keys:
            continue
        candidates.extend(evaluate_vendor_ledger(ledger, rules_config, new_keys=new_keys))

    findings = _group(candidates)

    flagged_invoices = {(i.vendor_id, i.invoice_id): i for f in findings for i in f.invoices}
    by_rule: dict[str, int] = defaultdict(int)
    for f in findings:
        by_rule[f.rule_name] += 1

    dates = [i.posting_date for i in invoices]
    return ReviewSummary(
        findings=findings,
        invoices_scanned=len(invoices),
        vendors_scanned=len(by_vendor),
        period_start=min(dates) if dates else None,
        period_end=max(dates) if dates else None,
        amount_under_review=sum((i.amount for i in flagged_invoices.values()), start=Decimal(0)),
        potential_overpayment=sum((f.potential_overpayment for f in findings), start=Decimal(0)),
        vendors_flagged=len({f.vendor_id for f in findings}),
        by_rule=dict(by_rule),
    )
