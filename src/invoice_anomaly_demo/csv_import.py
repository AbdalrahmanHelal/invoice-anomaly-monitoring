"""Turn an uploaded invoice export (CSV / Excel) into `Invoice` objects.

The UI accepts whatever a finance team can export — a SAP FBL1N line-item
report, an ERP "vendor invoices" list, a hand-made spreadsheet — so this
module does two things only:

1. `guess_column_mapping` suggests which column holds which field, from a
   list of common header spellings (English SAP labels, SAP technical field
   names, generic names). The user can always override it in the UI.
2. `dataframe_to_invoices` converts rows using that mapping, and reports
   every row it had to skip instead of failing the whole file.

Nothing here touches detection logic.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pandas as pd

from invoice_anomaly_demo.domain import Invoice

#: Field name -> header spellings we recognise (compared case/space/punct-insensitively).
FIELD_ALIASES: dict[str, list[str]] = {
    "vendor_id": [
        "vendor_id", "vendor id", "vendor", "vendor number", "vendor no", "vendor code",
        "supplier", "supplier id", "supplier number", "account", "lifnr", "creditor",
    ],
    "vendor_name": [
        "vendor_name", "vendor name", "supplier name", "name 1", "name1", "name",
        "account name", "name of vendor",
    ],
    "invoice_id": [
        "invoice_id", "invoice id", "invoice number", "invoice no", "document number",
        "document no", "doc number", "belnr", "reference", "xblnr", "invoice",
    ],
    "posting_date": [
        "posting_date", "posting date", "pstng date", "budat", "invoice date",
        "document date", "doc date", "date",
    ],
    "amount": [
        "amount", "amount in local currency", "amount in lc", "amt in loc cur",
        "local currency amount", "dmbtr", "wrbtr", "invoice amount", "gross amount", "total",
    ],
}

REQUIRED_FIELDS = ["vendor_id", "invoice_id", "posting_date", "amount"]


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(header).lower())


def guess_column_mapping(columns: list[str]) -> dict[str, str | None]:
    """Best-effort guess of field -> column. Earlier aliases win; a column is
    never assigned to two fields."""
    normalised = {_norm(c): c for c in columns}
    used: set[str] = set()
    mapping: dict[str, str | None] = {}
    for field_name, aliases in FIELD_ALIASES.items():
        mapping[field_name] = None
        for alias in aliases:
            col = normalised.get(_norm(alias))
            if col is not None and col not in used:
                mapping[field_name] = col
                used.add(col)
                break
    return mapping


def parse_amount(raw) -> Decimal | None:
    """Accepts 12,500.00 / 12500 / (12,500.00) / 12,500.00- / "EGP 12,500"."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    if isinstance(raw, (int, float, Decimal)):
        return Decimal(str(raw))
    text = str(raw).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")") or text.endswith("-") or text.startswith("-")
    digits = re.sub(r"[^0-9.]", "", text)
    if not digits:
        return None
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    return -value if negative else value


def parse_date(raw, dayfirst: bool) -> datetime.date | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    if isinstance(raw, datetime.datetime):
        return raw.date()
    if isinstance(raw, datetime.date):
        return raw
    text = str(raw).strip()
    if re.fullmatch(r"\d{8}", text):  # SAP internal format YYYYMMDD
        try:
            return datetime.datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", text):  # ISO is never ambiguous
        dayfirst = False
    ts = pd.to_datetime(text, dayfirst=dayfirst, errors="coerce")
    return None if pd.isna(ts) else ts.date()


@dataclass
class ImportResult:
    invoices: list[Invoice]
    skipped: list[tuple[int, str]] = field(default_factory=list)  # (row number, reason)
    negatives_flipped: int = 0


def dataframe_to_invoices(
    df: pd.DataFrame,
    mapping: dict[str, str | None],
    *,
    dayfirst: bool = True,
    use_absolute_amounts: bool = True,
) -> ImportResult:
    """Convert rows to invoices. SAP vendor line items are usually negative
    (credit side); with `use_absolute_amounts` they are flipped to positive
    so the rules compare magnitudes. Zero amounts are skipped."""
    missing = [f for f in REQUIRED_FIELDS if not mapping.get(f)]
    if missing:
        raise ValueError(f"Missing column mapping for: {', '.join(missing)}")

    invoices: list[Invoice] = []
    result = ImportResult(invoices=invoices)
    seen: set[tuple[str, str]] = set()

    for pos, (_, row) in enumerate(df.iterrows(), start=2):  # row 1 = header
        vendor_id = str(row[mapping["vendor_id"]]).strip()
        invoice_id = str(row[mapping["invoice_id"]]).strip()
        if not vendor_id or vendor_id.lower() == "nan":
            result.skipped.append((pos, "empty vendor"))
            continue
        if not invoice_id or invoice_id.lower() == "nan":
            result.skipped.append((pos, "empty invoice number"))
            continue

        key = (vendor_id, invoice_id)
        if key in seen:
            result.skipped.append((pos, f"repeated row for invoice {invoice_id}"))
            continue

        posting_date = parse_date(row[mapping["posting_date"]], dayfirst)
        if posting_date is None:
            result.skipped.append((pos, f"unreadable date: {row[mapping['posting_date']]!r}"))
            continue

        amount = parse_amount(row[mapping["amount"]])
        if amount is None:
            result.skipped.append((pos, f"unreadable amount: {row[mapping['amount']]!r}"))
            continue
        if amount < 0:
            if not use_absolute_amounts:
                result.skipped.append((pos, "negative amount (credit note?)"))
                continue
            amount = -amount
            result.negatives_flipped += 1
        if amount == 0:
            result.skipped.append((pos, "zero amount"))
            continue

        seen.add(key)

        name_col = mapping.get("vendor_name")
        vendor_name = str(row[name_col]).strip() if name_col else vendor_id
        if not vendor_name or vendor_name.lower() == "nan":
            vendor_name = vendor_id

        invoices.append(
            Invoice(
                vendor_id=vendor_id,
                vendor_name=vendor_name,
                invoice_id=invoice_id,
                posting_date=posting_date,
                amount=amount,
            )
        )

    return result


def invoices_to_dataframe(invoices: list[Invoice]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Vendor": i.vendor_id,
                "Vendor Name": i.vendor_name,
                "Invoice Number": i.invoice_id,
                "Posting Date": i.posting_date.isoformat(),
                "Amount": float(i.amount),
            }
            for i in invoices
        ]
    )
