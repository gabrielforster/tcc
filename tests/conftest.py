import os

# Cap every thread pool before numpy, torch or the boosters load: each one otherwise
# starts a thread per core, and two suites running side by side saturate the machine.
# Raise it per run with COLLECTION_TEST_THREADS.
_THREADS = os.environ.get("COLLECTION_TEST_THREADS", "2")
for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, _THREADS)
os.environ.setdefault("N_JOBS", _THREADS)

import uuid  # noqa: E402

import boto3  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from botocore.config import Config  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from collection.config import settings  # noqa: E402
from collection.data.sources.synthetic import SyntheticSource  # noqa: E402
from collection.db.migrations import alembic_config  # noqa: E402
from collection.rag.documents import load_directory  # noqa: E402
from collection.rag.index import VectorIndex  # noqa: E402


@pytest.fixture(scope="session")
def dataset():
    return SyntheticSource(n_customers=120, n_invoices=1500, seed=7).extract()


@pytest.fixture(scope="session")
def index():
    return VectorIndex().add_documents(load_directory(settings.dir_knowledge)).build()


@pytest.fixture(scope="session")
def db_engine():
    """An engine on a migrated database.

    Skips when Postgres is unreachable so the suite runs on a laptop with nothing started;
    fails instead when COLLECTION_REQUIRE_DB=1, which CI sets, so a CI run cannot go green
    by skipping every database test.
    """
    url = settings.test_database_url or settings.database_url
    engine = create_engine(url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except OperationalError as exc:
        engine.dispose()
        message = f"Postgres is unreachable at {engine.url.render_as_string()}: {exc}"
        if os.environ.get("COLLECTION_REQUIRE_DB") == "1":
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    command.upgrade(alembic_config(url), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db(db_engine):
    """A Session inside a transaction that is rolled back after the test."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(scope="session")
def monkeypatch_session():
    with pytest.MonkeyPatch.context() as patch:
        yield patch


@pytest.fixture(scope="session")
def bucket(monkeypatch_session):
    name = f"collection-test-{uuid.uuid4().hex[:12]}"
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name="us-east-1",
        config=Config(connect_timeout=3, retries={"max_attempts": 1}),
    )
    try:
        client.list_buckets()
    except Exception as exc:
        # Not "unreachable": bad credentials land here too, and a CI run that says
        # unreachable when the keys are wrong sends the reader to the wrong file.
        message = f"MinIO at {settings.s3_endpoint_url} is unusable: {exc}"
        if os.environ.get("COLLECTION_REQUIRE_DB") == "1":
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    # A store built without `bucket=` must not reach the configured bucket either.
    monkeypatch_session.setattr(settings, "s3_bucket", name)
    yield name
    try:
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=name):
            for item in page.get("Contents", []):
                client.delete_object(Bucket=name, Key=item["Key"])
        client.delete_bucket(Bucket=name)
    except ClientError as exc:
        # Never created if no test got as far as a first use; anything else is real.
        if exc.response.get("Error", {}).get("Code") != "NoSuchBucket":
            raise
