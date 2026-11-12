import os

# Cap every thread pool before numpy, torch or the boosters load: each one otherwise
# starts a thread per core, and two suites running side by side saturate the machine.
# Raise it per run with COLLECTION_TEST_THREADS.
_THREADS = os.environ.get("COLLECTION_TEST_THREADS", "2")
for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, _THREADS)
os.environ.setdefault("N_JOBS", _THREADS)
# Never export: a developer whose .env names a collector for `uv run` would otherwise ship
# the suite's spans to it. The environment beats .env. tests/test_telemetry.py turns
# telemetry on itself, with in-memory exporters.
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = ""

import fcntl  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import uuid  # noqa: E402
from pathlib import Path  # noqa: E402

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

#: One suite at a time per machine, across every checkout and worktree. Each run is
#: affordable alone; five at once is what took a laptop down. A run that finds the lock
#: taken waits for it rather than failing. The holder exports COLLECTION_TEST_LOCK_HELD so
#: the suites a test starts in a subprocess (tests/test_db_setup.py) do not wait on their
#: own parent. COLLECTION_TEST_LOCK=0 opts out.
_LOCK_PATH = Path(tempfile.gettempdir()) / "collection-tests.lock"


def pytest_configure(config):
    if os.environ.get("COLLECTION_TEST_LOCK") == "0":
        return
    if os.environ.get("COLLECTION_TEST_LOCK_HELD") == "1":
        return
    handle = _LOCK_PATH.open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"waiting for another test run to finish ({_LOCK_PATH})", file=sys.stderr)
        fcntl.flock(handle, fcntl.LOCK_EX)
    # Held until the process exits; the kernel releases it even if the run is killed.
    config._collection_test_lock = handle
    os.environ["COLLECTION_TEST_LOCK_HELD"] = "1"


@pytest.fixture(scope="session")
def dataset():
    return SyntheticSource(n_customers=120, n_invoices=1500, seed=7).extract()


@pytest.fixture(scope="session")
def tenant_id():
    """A tenant to pseudonymize under, for the tests that are not about tenancy.

    Pseudonyms are keyed per tenant, so anonymizing frames outside a `TenantScope` -- as
    the feature and model tests do -- still needs one. Fixed rather than random so a
    failure is reproducible.
    """
    return uuid.UUID("44444444-4444-4444-4444-444444444444")


@pytest.fixture(scope="session")
def index():
    return VectorIndex().add_documents(load_directory(settings.dir_knowledge)).build()


@pytest.fixture(scope="session")
def db_engine():
    """An engine on a migrated database.

    Only ever TEST_DATABASE_URL. Falling back to DATABASE_URL migrated a developer's own
    database and ran the suite against whatever it held -- a model published by a real
    `make train` turned "nothing was trained" tests red. A git worktree has no `.env`, so
    the fallback was the default there, not an edge case.

    Skips when the test database is unset or unreachable so the suite runs on a laptop with
    nothing started; fails instead when COLLECTION_REQUIRE_DB=1, which CI sets, so a CI run
    cannot go green by skipping every database test.
    """
    url = settings.test_database_url
    if not url:
        message = "TEST_DATABASE_URL is not set; the suite never falls back to DATABASE_URL"
        if os.environ.get("COLLECTION_REQUIRE_DB") == "1":
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
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
