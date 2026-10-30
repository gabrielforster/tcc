"""The pipeline against the record: rows and objects, and no file left behind.

`bucket` and `db` give a throwaway bucket and a transaction that is rolled back, so a
run here leaves nothing in the developer's data. A tenant's session serves one tenant and
a platform session serves none, so each gets its own `Session` over the test transaction,
the way `test_object_store` does it.

Training six real models takes about ten seconds, so the fitted result is shared by the
tests that need a model and each of them publishes it into its own transaction.
"""

import io
import re

import joblib
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from collection.config import settings
from collection.data.ingest import IngestReport, ingest, load_interim, source_metadata
from collection.data.sources.base import DataSource, RawDataset
from collection.db.models import (
    Customer,
    CustomerScore,
    PlatformModel,
    Receivable,
    StoredObject,
    Tenant,
)
from collection.db.repository import TenantScope
from collection.db.session import bind_tenant
from collection.models.scoring import score_customers
from collection.models.store import evaluate_task, latest_model, load_bundle, publish
from collection.models.train import fit_task
from collection.storage.objects import ObjectStore


class _Fixed(DataSource):
    """A source that hands back a dataset already in memory."""

    name = "fixed"

    def __init__(self, dataset: RawDataset) -> None:
        self._dataset = dataset

    def extract(self) -> RawDataset:
        return self._dataset


def _scope(db: Session, slug: str) -> TenantScope:
    tenant = Tenant(name=slug, slug=slug)
    db.add(tenant)
    db.flush()
    session = Session(bind=db.connection(), join_transaction_mode="create_savepoint")
    return TenantScope(session, tenant.id)


def _platform(db: Session) -> Session:
    session = Session(bind=db.connection(), join_transaction_mode="create_savepoint")
    bind_tenant(session, None, platform=True)
    return session


@pytest.fixture(autouse=True)
def _own_bucket(bucket):
    """Every test here writes objects; none may reach the configured bucket."""


@pytest.fixture(scope="module")
def fitted(dataset):
    from collection.data.anonymize import anonymize

    return fit_task("default", anonymize(dataset), source="fixed", tune=False)


@pytest.mark.db
def test_ingest_writes_customers_and_receivables_for_the_tenant(db, dataset):
    scope = _scope(db, "one")
    other = _scope(db, "two")

    report = ingest(scope, _Fixed(dataset))

    assert isinstance(report, IngestReport)
    assert report.customers == len(dataset.customers) == len(scope.all(Customer))
    assert report.receivables == len(dataset.invoices) == len(scope.all(Receivable))
    # The pseudonym is the identifier the anonymised tables carry, so a score computed on
    # them can be mapped back to the customer row and to no one else's.
    external = dataset.customers["customer_id"].iloc[0]
    customer = scope.all(Customer, Customer.external_id == external)[0]
    assert customer.pseudonym.startswith("cus_")
    assert other.all(Customer) == []
    assert other.all(Receivable) == []


@pytest.mark.db
def test_ingesting_twice_replaces_receivables_instead_of_doubling_them(db, dataset):
    scope = _scope(db, "one")

    ingest(scope, _Fixed(dataset))
    ingest(scope, _Fixed(dataset))

    assert len(scope.all(Customer)) == len(dataset.customers)
    assert len(scope.all(Receivable)) == len(dataset.invoices)


@pytest.mark.db
def test_ingest_stores_the_raw_frame_as_an_object_and_writes_no_file(
    db, dataset, fitted, tmp_path, monkeypatch
):
    """The window covers a whole run: ingest, publishing a model, and scoring with it."""
    directories = {}
    for name in ("dir_raw", "dir_interim", "dir_processed"):
        directories[name] = tmp_path / name
        directories[name].mkdir()
        monkeypatch.setattr(settings, name, directories[name])
    scope = _scope(db, "one")

    ingest(scope, _Fixed(dataset))
    publish(fitted, session=_platform(db))
    assert score_customers(scope, "default") > 0

    store = ObjectStore(scope)
    raw = store.get(f"{store.prefix}raw/customers.parquet")
    import pandas as pd

    frame = pd.read_parquet(io.BytesIO(raw))
    # The raw frame is the one that still carries the direct identifiers.
    assert "name" in frame.columns
    assert len(frame) == len(dataset.customers)
    kinds = {row.kind for row in scope.all(StoredObject)}
    assert {"raw", "interim"} <= kinds
    assert source_metadata(scope)["source"] == "fixed"
    assert set(load_interim(scope)) == {"customers", "invoices", "collection_events", "agreements"}
    for name, directory in directories.items():
        assert list(directory.iterdir()) == [], f"{name} was written to"
    assert not (tmp_path / "data").exists()


@pytest.mark.db
def test_an_invalid_dataset_is_refused_before_anything_is_uploaded(db, dataset):
    broken = RawDataset(
        customers=dataset.customers.drop(columns=["segment"]),
        invoices=dataset.invoices,
        events=dataset.events,
        agreements=dataset.agreements,
    )
    scope = _scope(db, "one")

    with pytest.raises(ValueError, match="Invalid raw dataset"):
        ingest(scope, _Fixed(broken))

    assert scope.all(StoredObject) == []
    assert scope.all(Customer) == []


@pytest.mark.db
def test_training_records_a_platform_model_row_with_an_artifact_key(db, fitted):
    session = _platform(db)

    row = publish(fitted, session=session)

    assert row.kind == "default"
    assert row.version == 1
    assert row.trained_at is not None
    assert re.fullmatch(r"platform/models/default-v1-[0-9a-f]{8}\.joblib", row.artifact_key)
    assert row.metrics["champion"] == fitted.result.champion
    assert row.metrics["test"]["average_precision"] == fitted.result.test.average_precision
    store = ObjectStore(session=session)
    assert store.exists(row.artifact_key)
    assert store.exists(
        row.artifact_key.replace("/models/", "/metrics/").replace(".joblib", ".json")
    )
    assert row.metrics["trained_on"] == {
        "tenant_id": None,
        "source": "fixed",
        "rows": sum(fitted.result.split_sizes.values()),
    }
    bundle = joblib.load(io.BytesIO(store.get(row.artifact_key)))
    assert bundle["threshold"] == fitted.result.threshold
    assert bundle["target"] == fitted.result.target
    assert bundle["features"] == fitted.result.feature_columns
    assert hasattr(bundle["pipeline"], "predict_proba")
    # A model belongs to the platform: its row is not tied to any tenant.
    assert not hasattr(PlatformModel, "tenant_id")


@pytest.mark.db
def test_each_training_run_is_a_new_version_and_the_latest_wins(db, fitted):
    session = _platform(db)

    first = publish(fitted, session=session)
    second = publish(fitted, session=session)

    assert (first.version, second.version) == (1, 2)
    assert latest_model(session, "default").id == second.id
    assert latest_model(session, "propensity") is None
    assert evaluate_task("default", session=session)["champion"] == fitted.result.champion
    assert load_bundle(session, "default")["task"] == "default"


@pytest.mark.db
def test_evaluate_explains_itself_when_nothing_was_trained(db):
    with pytest.raises(FileNotFoundError, match="collection train"):
        evaluate_task("propensity", session=_platform(db))


@pytest.mark.db
def test_scoring_writes_one_score_per_customer_against_that_model_version(db, dataset, fitted):
    scope = _scope(db, "one")
    ingest(scope, _Fixed(dataset))
    model = publish(fitted, session=_platform(db))
    # Same transaction, different session: the model row is visible to the tenant's
    # session because it is not a tenant-scoped table.

    written = score_customers(scope, "default")

    scores = scope.all(CustomerScore)
    assert written == len(scores) > 0
    assert {s.platform_model_id for s in scores} == {model.id}
    customers = {c.id: c for c in scope.all(Customer)}
    assert len({s.customer_id for s in scores}) == len(scores)
    assert all(s.customer_id in customers for s in scores)
    assert all(0.0 <= s.score <= 1.0 for s in scores)
    assert all(s.scored_at is not None for s in scores)
    # Scoring again against the same version replaces, never doubles.
    assert score_customers(scope, "default") == written
    assert scope.session.scalar(select(func.count()).select_from(CustomerScore)) == written


@pytest.mark.db
def test_scoring_without_a_trained_model_says_so(db, dataset):
    scope = _scope(db, "one")
    ingest(scope, _Fixed(dataset))

    with pytest.raises(LookupError, match="collection train"):
        score_customers(scope, "propensity")
