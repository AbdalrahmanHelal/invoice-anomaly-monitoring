"""Rule: duplicate invoice, suppressed when it is routine for that vendor.

Fires when a vendor has >= min_count invoices for the exact same amount on
the exact same day, unless that vendor already repeats amounts on more than
`normal_repeat_rate` of their *other* billing days (see README for why).
The day being judged is excluded from its own baseline so it can never
justify itself.
"""
from __future__ import annotations

from invoice_anomaly_demo.domain import Invoice
from invoice_anomaly_demo.formatting import format_amount

from .base import AlertCandidate, BaseRule


class DuplicateRule(BaseRule):
    name = "duplicate"

    def run(self, ledger: list[Invoice], rules_config: dict) -> list[AlertCandidate]:
        cfg = rules_config.get(self.name, {})
        min_count: int = cfg.get("min_count", 2)
        severity: str = cfg.get("severity", "high")
        normal_repeat_rate: float = cfg.get("normal_repeat_rate", 0.05)

        groups: dict[tuple, list[Invoice]] = {}
        for inv in ledger:
            groups.setdefault((inv.posting_date, inv.amount), []).append(inv)

        active_days = {inv.posting_date for inv in ledger}
        repeat_days = {
            posting_date
            for (posting_date, _amount), invoices in groups.items()
            if len(invoices) >= min_count
        }

        candidates: list[AlertCandidate] = []
        for (posting_date, amount), invoices in groups.items():
            if len(invoices) < min_count:
                continue

            other_days = active_days - {posting_date}
            other_repeat_days = repeat_days - {posting_date}
            if other_days:
                rate = len(other_repeat_days) / len(other_days)
                if rate > normal_repeat_rate:
                    continue  # routine for this vendor -- not an anomaly

            if not other_repeat_days:
                context = "This is the first time they have billed the same amount more than once in a day."
            else:
                context = (
                    f"They have only done this on {len(other_repeat_days)} of their "
                    f"{len(other_days)} other billing days."
                )

            invoice_ids = ", ".join(sorted(i.invoice_id for i in invoices))
            for inv in invoices:
                candidates.append(
                    AlertCandidate(
                        invoice=inv,
                        rule_name=self.name,
                        severity=severity,
                        message=(
                            f"This vendor has {len(invoices)} invoices for the same amount "
                            f"of {format_amount(amount)}, all posted on {posting_date}. "
                            f"{context} Invoices: {invoice_ids}."
                        ),
                    )
                )

        return candidates
