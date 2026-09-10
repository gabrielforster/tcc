from collection.config import settings
from collection.data.sources.base import DataSource, RawDataset
from collection.data.sources.erp import ERPSource
from collection.data.sources.synthetic import SyntheticSource

SOURCES: dict[str, type[DataSource]] = {
    "synthetic": SyntheticSource,
    "erp": ERPSource,
}


def get_source(name: str | None = None) -> DataSource:
    name = name or settings.data_source
    if name not in SOURCES:
        available = ", ".join(SOURCES)
        raise ValueError(f"Unknown data source: {name!r}. Available: {available}")
    return SOURCES[name]()


__all__ = ["SOURCES", "DataSource", "ERPSource", "RawDataset", "SyntheticSource", "get_source"]
