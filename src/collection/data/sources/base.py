"""Contract between data sources and the rest of the pipeline.

Swapping the synthetic generator for the real ERP extraction means implementing this
interface; nothing downstream (anonymization, EDA, features, models) has to change.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

from collection.domain.schema import TABLES


@dataclass
class RawDataset:
    """The four domain tables, still carrying personal data."""

    customers: pd.DataFrame
    invoices: pd.DataFrame
    events: pd.DataFrame
    agreements: pd.DataFrame

    def as_dict(self) -> dict[str, pd.DataFrame]:
        return {
            "customers": self.customers,
            "invoices": self.invoices,
            "collection_events": self.events,
            "agreements": self.agreements,
        }

    def validate(self) -> list[str]:
        """Check every table against the columns declared in the data dictionary."""
        problems: list[str] = []
        tables = self.as_dict()
        for table in TABLES:
            df = tables[table.name]
            missing = [c for c in table.columns if c not in df.columns]
            if missing:
                problems.append(f"{table.name}: missing columns {missing}")
            if df.empty:
                problems.append(f"{table.name}: empty table")
        invoices = self.invoices
        if not invoices.empty:
            orphans = set(invoices["customer_id"]) - set(self.customers["customer_id"])
            if orphans:
                problems.append(f"invoices: {len(orphans)} customer_id without a registry entry")
            inverted = (invoices["due_date"] < invoices["issue_date"]).sum()
            if inverted:
                problems.append(f"invoices: {inverted} receivables due before being issued")
        return problems


class DataSource(ABC):
    name: str

    @abstractmethod
    def extract(self) -> RawDataset:
        """Return the four domain tables, untreated."""
