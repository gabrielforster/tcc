"""Ingestion: extract from the source, store the raw tables, anonymize and validate.

data/raw/       raw source output (never committed; contains personal data)
data/interim/   anonymized dataset, the starting point for EDA and features
"""

from pathlib import Path

import pandas as pd

from collection.config import settings
from collection.data.anonymize import anonymize
from collection.data.sources import get_source
from collection.data.sources.base import RawDataset

TABLE_FILES = ["customers", "invoices", "collection_events", "agreements"]


def extract(source: str | None = None) -> RawDataset:
    """Run the extraction and write the raw tables to data/raw."""
    settings.prepare_directories()
    dataset = get_source(source).extract()
    for name, df in dataset.as_dict().items():
        df.to_parquet(settings.dir_raw / f"{name}.parquet", index=False)
    return dataset


def load_raw() -> RawDataset:
    tables = {n: pd.read_parquet(settings.dir_raw / f"{n}.parquet") for n in TABLE_FILES}
    return RawDataset(
        customers=tables["customers"],
        invoices=tables["invoices"],
        events=tables["collection_events"],
        agreements=tables["agreements"],
    )


def ingest() -> dict[str, Path]:
    """Anonymize the raw dataset, validate the schema and write to data/interim."""
    settings.prepare_directories()
    dataset = load_raw()
    problems = dataset.validate()
    if problems:
        raise ValueError("Invalid raw dataset:\n  - " + "\n  - ".join(problems))

    paths: dict[str, Path] = {}
    for name, df in anonymize(dataset).items():
        path = settings.dir_interim / f"{name}.parquet"
        df.to_parquet(path, index=False)
        paths[name] = path
    return paths


def load_interim() -> dict[str, pd.DataFrame]:
    return {n: pd.read_parquet(settings.dir_interim / f"{n}.parquet") for n in TABLE_FILES}
