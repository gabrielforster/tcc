"""Contract between data sources and the rest of the pipeline.

Swapping the synthetic generator for the real ERP extraction means implementing this
interface; nothing downstream (anonymization, EDA, features, models) has to change.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd

from collection.domain.schema import TABLES

ALL_TABLES = frozenset({"customers", "invoices", "collection_events", "agreements"})


@dataclass
class RawDataset:
    """The four domain tables, still carrying personal data.

    A source is not required to fill all four. Public datasets only cover part of the
    domain -- Home Credit has receivables but no collection contacts, Bank Marketing the
    other way around -- so `provides` declares which tables carry data. Tables outside it
    must still exist with the right columns, just empty, and validation skips them.
    """

    customers: pd.DataFrame
    invoices: pd.DataFrame
    events: pd.DataFrame
    agreements: pd.DataFrame
    provides: frozenset[str] = field(default=ALL_TABLES)

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
        unknown = self.provides - ALL_TABLES
        if unknown:
            problems.append(f"unknown table names declared in provides: {sorted(unknown)}")
        for table in TABLES:
            df = tables[table.name]
            missing = [c for c in table.columns if c not in df.columns]
            if missing:
                problems.append(f"{table.name}: missing columns {missing}")
            if df.empty and table.name in self.provides:
                problems.append(f"{table.name}: empty table")
            if not df.empty and table.name not in self.provides:
                problems.append(f"{table.name}: has rows but is not declared in provides")
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
    #: Domain tables this source populates. Sources covering the whole domain keep the default.
    provides: frozenset[str] = ALL_TABLES

    @abstractmethod
    def extract(self) -> RawDataset:
        """Return the four domain tables, untreated."""

    def empty_table(self, name: str) -> pd.DataFrame:
        """An empty frame with the declared columns, for tables this source does not cover."""
        table = next(t for t in TABLES if t.name == name)
        return pd.DataFrame({column: pd.Series(dtype="object") for column in table.columns})
