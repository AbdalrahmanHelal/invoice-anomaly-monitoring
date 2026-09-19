"""Rule: invoice amount spike, judged against a trailing historical window.

Fires when an invoice is more than `percent_above`% above the largest
invoice that vendor billed in the preceding `comparison_months` -- a new
high over a meaningful stretch of time, not just "bigger than usual".
A trailing time window (rather than a trailing invoice count) stays
meaningful regardless of how often a vendor bills; see README for the two
simpler designs this replaced and why they failed on real data.

Cold-start guarded by `min_history_count`: a vendor needs at least that
many invoices inside the window before "new high" means anything.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from invoice_anomaly_demo.domain import Invoice
from invoice_anomaly_demo.formatting import format_amount

from .base import AlertCandidate, BaseRule

_DAYS_PER_MONTH = 30.44  # average; only used to turn months into a cutoff date


class AmountSpikeRule(BaseRule):
    name = "amount_spike"

    def run(self, ledger: list[Invoice], rules_config: dict) -> list[AlertCandidate]:
        cfg = rules_config.get(self.name, {})
        months: float = cfg.get("comparison_months", 12)
        percent_above = Decimal(str(cfg.get("percent_above", 50)))
        min_history: int = cfg.get("min_history_count", 3)
        severity: str = cfg.get("severity", "high")

        ordered = sorted(ledger, key=lambda inv: (inv.posting_date, inv.invoice_id))
        multiplier = Decimal(1) + percent_above / Decimal(100)
        window = datetime.timedelta(days=round(months * _DAYS_PER_MONTH))

        candidates: list[AlertCandidate] = []
        for index, invoice in enumerate(ordered):
            cutoff = invoice.posting_date - window
            preceding = [p for p in ordered[:index] if p.posting_date >= cutoff]
            if len(preceding) < min_history:
                continue  # not enough recent billing for "a new high" to mean anything

            baseline = max(p.amount for p in preceding)
            if baseline <= 0:
                continue  # nothing positive to compare against

            if invoice.amount <= baseline * multiplier:
                continue

            over = (invoice.amount / baseline - Decimal(1)) * Decimal(100)
            last_two = ", ".join(
                f"{format_amount(p.amount)} on {p.posting_date}"
                for p in reversed(preceding[-2:])
            )

            candidates.append(
                AlertCandidate(
                    invoice=invoice,
                    rule_name=self.name,
                    severity=severity,
                    message=(
                        f"{format_amount(invoice.amount)} is the largest invoice from this "
                        f"vendor in the past {months:.0f} months -- {over:.0f}% above their "
                        f"previous highest of {format_amount(baseline)}. "
                        f"Their last 2 invoices: {last_two}."
                    ),
                )
            )

        return candidates
