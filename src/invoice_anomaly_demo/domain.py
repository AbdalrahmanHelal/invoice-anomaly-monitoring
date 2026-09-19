"""In-memory invoice shapes.

Two grains, same distinction the real system makes: a raw posted invoice
(`Invoice`) versus the grouped view a vendor's whole ledger is judged on
(`VendorLedger`). Keeping them separate means a rule never has to know
whether it is looking at one just-posted invoice or the full history it is
being compared against — both are lists of `Invoice`.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Invoice:
    """One vendor invoice. `invoice_id` is unique within a vendor."""

    vendor_id: str
    vendor_name: str
    invoice_id: str
    posting_date: datetime.date
    amount: Decimal
