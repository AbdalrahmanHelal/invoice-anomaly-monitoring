"""Human-readable amount formatting, shared by rule messages and the digest."""
from __future__ import annotations

from decimal import Decimal


def format_amount(value: Decimal | float | int | None) -> str:
    """Thousands separators, no decimals: "1800000.00" -> "1,800,000".

    These amounts can run to seven or eight figures. A bare Decimal is hard
    to scan at a glance -- the separators are what let a reader tell "large"
    from "very large" without counting digits -- and a trailing ".00" adds
    nothing when nothing here is decided on fractions of a currency unit.
    """
    if value is None:
        return ""
    return f"{Decimal(str(value)):,.0f}"
