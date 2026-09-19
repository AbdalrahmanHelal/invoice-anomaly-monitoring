"""Human-readable alert digest: plain text to stdout, no mail server needed."""
from __future__ import annotations

from invoice_anomaly_demo.detection.rules.base import AlertCandidate
from invoice_anomaly_demo.formatting import format_amount

_RULE_LABELS = {
    "amount_spike": "Unusually large amount",
    "duplicate": "Possible duplicate invoice",
    "same_day_count": "Many invoices in one day",
}
_SEVERITY_ORDER = ["high", "medium", "low"]


def render_digest(candidates: list[AlertCandidate]) -> str:
    if not candidates:
        return "No new alerts this cycle.\n"

    ordered = sorted(
        candidates,
        key=lambda c: _SEVERITY_ORDER.index(c.severity) if c.severity in _SEVERITY_ORDER else 99,
    )

    lines = [f"{len(ordered)} alert(s) this cycle", "=" * 40, ""]
    for c in ordered:
        label = _RULE_LABELS.get(c.rule_name, c.rule_name.replace("_", " ").capitalize())
        lines.append(f"[{c.severity.upper()}] {label}")
        lines.append(f"  Vendor:  {c.invoice.vendor_name} ({c.invoice.vendor_id})")
        lines.append(f"  Amount:  {format_amount(c.invoice.amount)}")
        lines.append(f"  Why:     {c.message}")
        lines.append(f"  Invoice: {c.invoice.invoice_id}  Posted: {c.invoice.posting_date}")
        lines.append("")

    return "\n".join(lines)
