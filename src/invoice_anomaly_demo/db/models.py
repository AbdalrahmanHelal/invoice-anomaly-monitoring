from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Alert(Base):
    """One row per anomaly. Carries its own snapshot of the invoice fields
    it refers to rather than a foreign key, since this demo -- like the
    system it is based on -- treats the source ledger as the system of
    record and never mirrors it locally. Only the alert itself persists.

    The (vendor_id, invoice_id, rule_name) unique constraint is the dedup
    guard: a given invoice+rule pair only ever produces one alert row, no
    matter how many times a later run re-evaluates that vendor's ledger.
    """

    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("vendor_id", "invoice_id", "rule_name", name="uq_alert_dedup_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    vendor_id: Mapped[str] = mapped_column(String(20))
    vendor_name: Mapped[str] = mapped_column(String(200))
    invoice_id: Mapped[str] = mapped_column(String(40))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    posting_date: Mapped[datetime.date] = mapped_column(Date)

    rule_name: Mapped[str] = mapped_column(String(50))
    severity: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
