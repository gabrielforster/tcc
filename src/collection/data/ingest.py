"""Ingestion: extract from the source, validate, anonymize, and write to the record.

Nothing here touches a local directory. The tenant's customers and receivables become
rows, which is what the application queries; the raw frame and the anonymized frame become
objects, because pandas still needs whole tables in memory to build features and a table
is not something to query row by row. Pandas does the work as before -- only the read and
write ends moved. A developer who wants a file asks for one explicitly; the `dir_*`
settings remain for that and for the public-dataset downloads, and nothing in the serving
path reads them.

Two copies are kept on purpose. `raw/` still carries the direct identifiers, the way
`data/raw` did, so an anonymization rule can be corrected and re-run without going back to
the ERP. `interim/` is what features and models read; it is the only copy that may leave
the tenant's prefix, and it has already lost the identifiers. The rows hold `external_id`
(the ERP's own key) and a `pseudonym` that is exactly the key `interim/` uses, so a score
computed on anonymized frames can be mapped back to one customer and to no other tenant's.

Validation runs before the first upload: a dataset that is going to be refused must not
leave objects behind that a later read would trust. That includes the two rules only the
record knows -- how wide `preferred_channel` is and which statuses the enum names -- so a
source that would fail at the INSERT fails before anything is stored instead.

Re-ingesting replaces. Customers are matched on `external_id` and updated; a receivable
has no key of its own in the source that survives anonymization, so a tenant's
receivables are replaced as a set -- a re-import is a fresh picture of what is owed, not
an addition to the last one.
`days_overdue` is a fact about the day of ingestion (`as_of`), as the model documents.
"""

import io
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pandas as pd

from collection.data.anonymize import anonymize, pseudonym
from collection.data.sources import get_source
from collection.data.sources.base import ALL_TABLES, DataSource, RawDataset
from collection.db.models.customer import Customer, Receivable, ReceivableStatus, days_overdue
from collection.db.repository import TenantScope
from collection.storage.objects import ObjectStore

TABLE_FILES = ["customers", "invoices", "collection_events", "agreements"]
METADATA_FILE = "source.json"
PARQUET = "application/vnd.apache.parquet"


@dataclass(frozen=True)
class IngestReport:
    source: str
    customers: int
    receivables: int
    raw_keys: tuple[str, ...]
    interim_keys: tuple[str, ...]


def _parquet(frame: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    return buffer.getvalue()


def _metadata(name: str, dataset: RawDataset) -> bytes:
    # Which tables this source covers has to survive the round trip through parquet,
    # otherwise validation would flag a legitimately empty table as missing data.
    return json.dumps({"source": name, "provides": sorted(dataset.provides)}, indent=2).encode()


def _resolve(source: DataSource | str | None) -> DataSource:
    return source if isinstance(source, DataSource) else get_source(source)


def _record_problems(dataset: RawDataset) -> list[str]:
    """What the columns will refuse, asked before anything is written.

    `RawDataset.validate` checks the dataset against the data dictionary; these two are
    the rules only the record knows -- the channel's column width and the status enum --
    and without them an ERP row with a longer channel or an unknown status reaches the
    database as a raw `DataError` or `ValueError`, after both frames are already uploaded.
    """
    problems: list[str] = []
    width = Customer.preferred_channel.type.length
    channels = dataset.customers.get("preferred_channel")
    if channels is not None:
        too_long = {str(c) for c in channels.dropna() if len(str(c)) > width}
        if too_long:
            problems.append(
                f"customers: preferred_channel over {width} characters: {sorted(too_long)[:5]}"
            )
    statuses = dataset.invoices.get("status")
    if statuses is not None:
        known = {s.value for s in ReceivableStatus}
        unknown = {str(s) for s in statuses.dropna()} - known
        if unknown:
            problems.append(f"invoices: status not in the record's enum: {sorted(unknown)[:5]}")
    return problems


def _pull(instance: DataSource, store: ObjectStore) -> tuple[RawDataset, tuple[str, ...]]:
    dataset = instance.extract()
    problems = dataset.validate() + _record_problems(dataset)
    if problems:
        raise ValueError("Invalid raw dataset:\n  - " + "\n  - ".join(problems))
    keys = _store_frames(store, "raw", dataset.as_dict())
    meta = store.put(
        METADATA_FILE,
        _metadata(instance.name, dataset),
        kind="raw",
        content_type="application/json",
    )
    return dataset, (*keys, meta.key)


def extract(
    scope: TenantScope, source: DataSource | str | None = None, *, store: ObjectStore | None = None
) -> RawDataset:
    """Pull the source, refuse it if invalid, and store the raw frames as objects."""
    return _pull(_resolve(source), store or ObjectStore(scope))[0]


def ingest(
    scope: TenantScope,
    source: DataSource | str | None = None,
    *,
    store: ObjectStore | None = None,
    as_of: date | None = None,
) -> IngestReport:
    """Extract, store the raw and anonymized frames, and write the rows."""
    instance = _resolve(source)
    store = store or ObjectStore(scope)
    dataset, raw_keys = _pull(instance, store)
    interim_keys = _store_frames(store, "interim", anonymize(dataset, tenant=scope.tenant_id))
    store.put(
        METADATA_FILE,
        _metadata(instance.name, dataset),
        kind="interim",
        content_type="application/json",
    )

    customers = _write_customers(scope, dataset.customers)
    receivables = _write_receivables(scope, dataset.invoices, customers, as_of or date.today())
    return IngestReport(
        source=instance.name,
        customers=len(customers),
        receivables=receivables,
        raw_keys=raw_keys,
        interim_keys=interim_keys,
    )


def _store_frames(
    store: ObjectStore, kind: str, frames: dict[str, pd.DataFrame]
) -> tuple[str, ...]:
    return tuple(
        store.put(f"{name}.parquet", _parquet(frame), kind=kind, content_type=PARQUET).key
        for name, frame in frames.items()
    )


def _write_customers(scope: TenantScope, frame: pd.DataFrame) -> dict[str, Customer]:
    """The tenant's customers named by `frame`, created or updated. Others are left alone."""
    existing = {c.external_id: c for c in scope.all(Customer)}
    written: dict[str, Customer] = {}
    for record in frame.to_dict("records"):
        external_id = str(record["customer_id"])
        channel = record.get("preferred_channel")
        customer = existing.get(external_id)
        if customer is None:
            customer = Customer(external_id=external_id, pseudonym="")
            scope.add(customer)
        customer.pseudonym = pseudonym(external_id, "cus_", tenant=scope.tenant_id)
        customer.preferred_channel = None if pd.isna(channel) else str(channel)
        written[external_id] = customer
    scope.session.flush()
    return written


def _write_receivables(
    scope: TenantScope, invoices: pd.DataFrame, customers: dict[str, Customer], as_of: date
) -> int:
    for old in scope.all(Receivable):
        scope.session.delete(old)
    scope.session.flush()
    for record in invoices.to_dict("records"):
        due = pd.Timestamp(record["due_date"]).date()
        scope.add(
            Receivable(
                customer_id=customers[str(record["customer_id"])].id,
                amount=Decimal(str(record["amount"])).quantize(Decimal("0.01")),
                due_date=due,
                days_overdue=days_overdue(due, as_of),
                status=ReceivableStatus(record["status"]),
            )
        )
    scope.session.flush()
    return len(invoices)


def _key(store: ObjectStore, name: str) -> str:
    return f"{store.prefix}interim/{name}"


def source_metadata(scope: TenantScope, store: ObjectStore | None = None) -> dict:
    """Name and covered tables of the source behind the tenant's interim frames."""
    store = store or ObjectStore(scope)
    key = _key(store, METADATA_FILE)
    if not store.exists(key):
        return {"source": "unknown", "provides": sorted(ALL_TABLES)}
    return json.loads(store.get(key))


def load_interim(scope: TenantScope, store: ObjectStore | None = None) -> dict[str, pd.DataFrame]:
    """The tenant's anonymized tables, read back from object storage."""
    store = store or ObjectStore(scope)
    tables: dict[str, pd.DataFrame] = {}
    for name in TABLE_FILES:
        key = _key(store, f"{name}.parquet")
        if not store.exists(key):
            raise FileNotFoundError("No ingested data for this tenant. Run `collection ingest`.")
        tables[name] = pd.read_parquet(io.BytesIO(store.get(key)))
    return tables
