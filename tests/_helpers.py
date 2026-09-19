import datetime
from decimal import Decimal

from invoice_anomaly_demo.domain import Invoice

_SEQ = 0


def make_invoice(
    *,
    vendor_id: str = "V001",
    vendor_name: str = "Test Vendor",
    amount: str,
    posting_date: str,
    invoice_id: str | None = None,
) -> Invoice:
    global _SEQ
    _SEQ += 1
    return Invoice(
        vendor_id=vendor_id,
        vendor_name=vendor_name,
        invoice_id=invoice_id or f"TEST-{_SEQ:06d}",
        posting_date=datetime.date.fromisoformat(posting_date),
        amount=Decimal(amount),
    )
