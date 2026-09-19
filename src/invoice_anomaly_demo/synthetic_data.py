"""Synthetic vendor ledgers for the demo.

Stands in for a live source system (in the system this demo is based on,
that was an ERP export). Nothing here is real: vendor names, amounts, and
invoice IDs are all generated. What IS deliberately preserved is the
*shape* of real accounts-payable data that made naive anomaly rules
unusable in practice:

  - most vendors have a stable "normal" amount and cadence
  - a handful of vendors bill the exact same amount more than once on the
    same day as a matter of routine (instalments, per-unit/per-vehicle
    billing) -- not a duplicate, just how they invoice
  - one vendor does routine month-end batching: many documents on the
    same day, every month, which a flat "more than N per day" rule
    mistakes for a spike
  - a couple of genuine anomalies are seeded on purpose so the demo has
    something real to catch: an amount far above a vendor's historical
    range, and a duplicate-looking pair for a vendor who has never
    repeated an amount before

`generate_demo_dataset()` returns each vendor's full history (everything
before "today") separately from "today's activity" (what a monitoring run
would just have fetched) -- the same split the real pipeline makes between
a vendor's baseline and the invoices actually eligible to alert on.
"""
from __future__ import annotations

import datetime
import random
from dataclasses import dataclass
from decimal import Decimal

from invoice_anomaly_demo.domain import Invoice

_TODAY = datetime.date(2026, 6, 30)


@dataclass(frozen=True)
class DemoDataset:
    today: datetime.date
    # vendor_id -> history strictly before `today`
    history: dict[str, list[Invoice]]
    # vendor_id -> invoices posted "today" (the eligible-to-alert set)
    todays_activity: dict[str, list[Invoice]]


def _daterange_business_days(start: datetime.date, end: datetime.date):
    day = start
    while day < end:
        if day.weekday() < 5:  # skip weekends -- irrelevant to the logic, just realistic
            yield day
        day += datetime.timedelta(days=1)


def _regular_vendor(rng: random.Random, vendor_id: str, name: str, base_amount: int, months: int) -> list[Invoice]:
    """A vendor billing roughly once every 1-2 weeks at a stable amount with
    small natural variation. This is the "boring majority" every real
    anomaly rule has to stay quiet for."""
    start = _TODAY - datetime.timedelta(days=months * 30)
    invoices = []
    day = start
    seq = 0
    while day < _TODAY:
        day += datetime.timedelta(days=rng.randint(9, 16))
        if day >= _TODAY:
            break
        seq += 1
        amount = Decimal(base_amount) * Decimal(str(round(rng.uniform(0.92, 1.08), 3)))
        invoices.append(
            Invoice(
                vendor_id=vendor_id,
                vendor_name=name,
                invoice_id=f"{vendor_id}-{seq:04d}",
                posting_date=day,
                amount=amount.quantize(Decimal("1")),
            )
        )
    return invoices


def _repeat_billing_vendor(rng: random.Random, vendor_id: str, name: str, unit_amount: int, months: int) -> list[Invoice]:
    """A vendor who routinely bills the same amount 2-3 times on one day
    (per-unit/per-vehicle billing, or instalments). Real production data
    showed this pattern occurring on 20-50% of a vendor's billing days --
    a naive "same vendor + same day + same amount = duplicate" rule fires
    constantly on vendors like this and teaches readers to ignore alerts."""
    start = _TODAY - datetime.timedelta(days=months * 30)
    invoices = []
    seq = 0
    for day in _daterange_business_days(start, _TODAY):
        if rng.random() > 0.12:  # bills on ~12% of business days
            continue
        units = rng.choice([1, 2, 2, 3])  # repeats more often than not, when billing
        for _ in range(units):
            seq += 1
            invoices.append(
                Invoice(
                    vendor_id=vendor_id,
                    vendor_name=name,
                    invoice_id=f"{vendor_id}-{seq:04d}",
                    posting_date=day,
                    amount=Decimal(unit_amount),
                )
            )
    return invoices


def _month_end_batch_vendor(rng: random.Random, vendor_id: str, name: str, unit_amount: int, months: int) -> list[Invoice]:
    """A vendor whose volume spikes predictably at month-end (e.g. a
    services company reconciling many small jobs at once). Real data showed
    a vendor exceeding a flat "more than 5 documents/day" threshold on
    ~8% of active days purely from this pattern, with a genuine historical
    high in the double digits -- a flat per-day cap flags it as new."""
    start = _TODAY - datetime.timedelta(days=months * 30)
    invoices = []
    seq = 0
    day = start
    while day < _TODAY:
        # small, ordinary days
        if day.weekday() < 5 and rng.random() < 0.15:
            seq += 1
            invoices.append(
                Invoice(
                    vendor_id=vendor_id,
                    vendor_name=name,
                    invoice_id=f"{vendor_id}-{seq:04d}",
                    posting_date=day,
                    amount=Decimal(unit_amount) * Decimal(str(round(rng.uniform(0.5, 1.5), 2))),
                )
            )
        day += datetime.timedelta(days=1)

    # month-end batches: 6-9 documents on the last business day of each month
    month_cursor = datetime.date(start.year, start.month, 1)
    while month_cursor < _TODAY:
        if month_cursor.month == 12:
            next_month = datetime.date(month_cursor.year + 1, 1, 1)
        else:
            next_month = datetime.date(month_cursor.year, month_cursor.month + 1, 1)
        last_day = next_month - datetime.timedelta(days=1)
        while last_day.weekday() >= 5:
            last_day -= datetime.timedelta(days=1)
        if start <= last_day < _TODAY:
            for _ in range(rng.randint(6, 9)):
                seq += 1
                invoices.append(
                    Invoice(
                        vendor_id=vendor_id,
                        vendor_name=name,
                        invoice_id=f"{vendor_id}-{seq:04d}",
                        posting_date=last_day,
                        amount=Decimal(unit_amount) * Decimal(str(round(rng.uniform(0.3, 1.2), 2))),
                    )
                )
        month_cursor = next_month
    return invoices


def generate_demo_dataset(seed: int = 7) -> DemoDataset:
    rng = random.Random(seed)

    history: dict[str, list[Invoice]] = {}
    todays_activity: dict[str, list[Invoice]] = {}

    # --- ordinary vendors: nothing should ever fire for these ---
    ordinary = [
        ("V-100", "Bluewave Office Supplies", 4200, 20),
        ("V-101", "Northgate Facilities Ltd", 15800, 18),
        ("V-102", "Aster Consulting Group", 62000, 14),
        ("V-103", "Rowan IT Managed Services", 27500, 16),
    ]
    for vendor_id, name, base, months in ordinary:
        history[vendor_id] = _regular_vendor(rng, vendor_id, name, base, months)

    # --- routine repeat-billing vendor (duplicate-rule false-positive case) ---
    history["V-200"] = _repeat_billing_vendor(rng, "V-200", "Fleetline Vehicle Rentals", 3600, 18)

    # --- month-end batching vendor (same-day-count false-positive case) ---
    history["V-300"] = _month_end_batch_vendor(rng, "V-300", "Harborview Facility Services", 2100, 18)

    # --- steady vendor about to get a genuine amount spike today ---
    history["V-400"] = _regular_vendor(rng, "V-400", "Solace Marketing Partners", 18000, 15)
    todays_activity["V-400"] = [
        Invoice(
            vendor_id="V-400",
            vendor_name="Solace Marketing Partners",
            invoice_id="V-400-9001",
            posting_date=_TODAY,
            amount=Decimal(210000),  # ~10x their normal range -- the intended catch
        )
    ]

    # --- steady vendor with a genuine first-time duplicate today ---
    history["V-500"] = _regular_vendor(rng, "V-500", "Kestrel Legal Advisory", 9400, 15)
    todays_activity["V-500"] = [
        Invoice(
            vendor_id="V-500",
            vendor_name="Kestrel Legal Advisory",
            invoice_id="V-500-9001",
            posting_date=_TODAY,
            amount=Decimal(9500),
        ),
        Invoice(
            vendor_id="V-500",
            vendor_name="Kestrel Legal Advisory",
            invoice_id="V-500-9002",
            posting_date=_TODAY,
            amount=Decimal(9500),  # same amount, same day, never done before -- genuine
        ),
    ]

    # --- routine vendors also get some harmless activity today, to prove
    # the false-positive suppression holds up on the day being judged too ---
    todays_activity["V-200"] = [
        Invoice(
            vendor_id="V-200",
            vendor_name="Fleetline Vehicle Rentals",
            invoice_id="V-200-9001",
            posting_date=_TODAY,
            amount=Decimal(3600),
        ),
        Invoice(
            vendor_id="V-200",
            vendor_name="Fleetline Vehicle Rentals",
            invoice_id="V-200-9002",
            posting_date=_TODAY,
            amount=Decimal(3600),  # repeat again today -- should stay quiet, it's routine
        ),
    ]
    todays_activity["V-300"] = [
        Invoice(
            vendor_id="V-300",
            vendor_name="Harborview Facility Services",
            invoice_id=f"V-300-900{i}",
            posting_date=_TODAY,
            amount=Decimal(2100) * Decimal(str(round(rng.uniform(0.5, 1.2), 2))),
        )
        for i in range(1, 7)  # 6 documents today -- within their normal batching range
    ]
    for vendor_id, _, base, _ in ordinary:
        if rng.random() < 0.5:
            todays_activity.setdefault(vendor_id, []).append(
                Invoice(
                    vendor_id=vendor_id,
                    vendor_name=history[vendor_id][0].vendor_name,
                    invoice_id=f"{vendor_id}-9001",
                    posting_date=_TODAY,
                    amount=Decimal(base) * Decimal(str(round(rng.uniform(0.95, 1.05), 3))),
                )
            )

    return DemoDataset(today=_TODAY, history=history, todays_activity=todays_activity)
