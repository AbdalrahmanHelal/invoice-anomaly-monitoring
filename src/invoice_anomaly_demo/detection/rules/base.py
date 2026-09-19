"""Shared types for detection rules."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from invoice_anomaly_demo.domain import Invoice


@dataclass
class AlertCandidate:
    """A potential alert produced by a rule, before dedup/persistence."""

    invoice: Invoice
    rule_name: str
    severity: str
    message: str
    source: str = field(default="rule")


class BaseRule(ABC):
    """All detection rules implement this interface.

    The engine calls `run()` once per vendor, passing that vendor's full
    ledger (history + anything from today) -- rules don't group by vendor
    themselves, and don't need to know which invoices are "new"; the engine
    filters candidates down to eligible ones afterward.
    """

    #: Must match the key used in rules.yaml.
    name: str

    @abstractmethod
    def run(self, ledger: list[Invoice], rules_config: dict) -> list[AlertCandidate]:
        """Return alert candidates found within one vendor's invoice ledger.

        Args:
            ledger: All of one vendor's invoices (history plus anything
                posted today) -- rules need the full picture to establish
                what is normal for that vendor.
            rules_config: The ``rules:`` section of rules.yaml.
        """
