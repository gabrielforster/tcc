from collection.config import settings
from collection.data.sources.bank_marketing import BankMarketingSource
from collection.data.sources.base import DataSource, RawDataset
from collection.data.sources.erp import ERPSource
from collection.data.sources.home_credit import HomeCreditSource
from collection.data.sources.synthetic import SyntheticSource

SOURCES: dict[str, type[DataSource]] = {
    "synthetic": SyntheticSource,
    "home-credit": HomeCreditSource,
    "bank-marketing": BankMarketingSource,
    "erp": ERPSource,
}


def get_source(name: str | None = None) -> DataSource:
    name = name or settings.data_source
    if name not in SOURCES:
        available = ", ".join(SOURCES)
        raise ValueError(f"Unknown data source: {name!r}. Available: {available}")
    return SOURCES[name]()


__all__ = [
    "SOURCES",
    "BankMarketingSource",
    "DataSource",
    "ERPSource",
    "HomeCreditSource",
    "RawDataset",
    "SyntheticSource",
    "get_source",
]
