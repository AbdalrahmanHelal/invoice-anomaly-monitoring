import datetime
from decimal import Decimal

import pandas as pd
import pytest

from invoice_anomaly_demo.csv_import import (
    dataframe_to_invoices,
    guess_column_mapping,
    parse_amount,
    parse_date,
)


def test_guess_mapping_sap_fbl1n_headers():
    cols = ["Account", "Name 1", "DocumentNo", "Pstng Date", "Amount in LC", "LCurr"]
    m = guess_column_mapping(cols)
    assert m == {
        "vendor_id": "Account",
        "vendor_name": "Name 1",
        "invoice_id": "DocumentNo",
        "posting_date": "Pstng Date",
        "amount": "Amount in LC",
    }


def test_guess_mapping_never_reuses_a_column():
    m = guess_column_mapping(["Vendor", "Invoice", "Date", "Amount"])
    used = [c for c in m.values() if c]
    assert len(used) == len(set(used))
    assert m["vendor_name"] is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("12,500.00", Decimal("12500.00")),
        ("12,500.00-", Decimal("-12500.00")),
        ("(900)", Decimal("-900")),
        ("EGP 4,200", Decimal("4200")),
        (3600, Decimal("3600")),
        ("", None),
        ("abc", None),
    ],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


def test_parse_date_formats():
    assert parse_date("31.12.2025", dayfirst=True) == datetime.date(2025, 12, 31)
    assert parse_date("20251231", dayfirst=True) == datetime.date(2025, 12, 31)
    assert parse_date("2025-12-31", dayfirst=True) == datetime.date(2025, 12, 31)
    assert parse_date("not a date", dayfirst=True) is None


def test_rows_are_converted_and_bad_rows_reported():
    df = pd.DataFrame(
        {
            "Vendor": ["V1", "V1", "V1", "", "V2", "V2"],
            "Invoice": ["A", "B", "A", "C", "D", "E"],
            "Date": ["01.01.2026", "02.01.2026", "01.01.2026", "01.01.2026", "bad", "03.01.2026"],
            "Amount": ["1,000.00-", "2,000", "1,000.00-", "5", "7", "0"],
        }
    )
    res = dataframe_to_invoices(df, guess_column_mapping(list(df.columns)))
    assert [(i.invoice_id, i.amount) for i in res.invoices] == [("A", Decimal("1000.00")), ("B", Decimal("2000"))]
    assert res.negatives_flipped == 1
    reasons = [r for _, r in res.skipped]
    assert any("repeated row" in r for r in reasons)
    assert any("empty vendor" in r for r in reasons)
    assert any("unreadable date" in r for r in reasons)
    assert any("zero amount" in r for r in reasons)
    # vendor name falls back to vendor id when there is no name column
    assert res.invoices[0].vendor_name == "V1"


def test_missing_required_mapping_raises():
    df = pd.DataFrame({"Vendor": ["V1"], "Amount": ["1"]})
    with pytest.raises(ValueError):
        dataframe_to_invoices(df, guess_column_mapping(list(df.columns)))
