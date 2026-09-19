"""Rule: unusual same-day invoice volume, judged against the vendor's own record.

Fires when a vendor posts more invoices in one day than ever before -- a
new personal high -- and that count also clears `max_documents_per_day`
(a floor, not a per-vendor threshold on its own; see README for why both
parts matter).
"""
from __future__ import annotations

from decimal import Decimal

from invoice_anomaly_demo.domain import Invoice
from invoice_anomaly_demo.formatting import format_amount

from .base import AlertCandidate, BaseRule


class SameDayCountRule(BaseRule):
    name = "same_day_count"

    def run(self, ledger: list[Invoice], rules_config: dict) -> list[AlertCandidate]:
        cfg = rules_config.get(self.name, {})
        floor: int = cfg.get("max_documents_per_day", 5)
        severity: str = cfg.get("severity", "medium")

        by_date: dict = {}
        for inv in ledger:
            by_date.setdefault(inv.posting_date, []).append(inv)

        # Evaluate in date order so a later record-setting day can never
        # retroactively suppress an earlier one, whatever order the source
        # data arrives in.
        ordered_dates = sorted(by_date)

        candidates: list[AlertCandidate] = []
        prior_max = 0
        for posting_date in ordered_dates:
            invoices = by_date[posting_date]
            count = len(invoices)

            if count > floor and count > prior_max:
                total = sum((inv.amount for inv in invoices), start=Decimal("0"))
                candidates.extend(
                    AlertCandidate(
                        invoice=inv,
                        rule_name=self.name,
                        severity=severity,
                        message=(
                            f"This vendor posted {count} invoices on {posting_date}, "
                            f"totalling {format_amount(total)} -- more than they ever "
                            f"have in one day before (previous high: {prior_max})."
                        ),
                    )
                    for inv in invoices
                )

            prior_max = max(prior_max, count)

        return candidates
