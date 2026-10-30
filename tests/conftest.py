import os

# Cap every thread pool before numpy, torch or the boosters load: each one otherwise
# starts a thread per core, and two suites running side by side saturate the machine.
# Raise it per run with COLLECTION_TEST_THREADS.
_THREADS = os.environ.get("COLLECTION_TEST_THREADS", "2")
for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, _THREADS)
os.environ.setdefault("N_JOBS", _THREADS)

import pytest  # noqa: E402
from alembic import command  # noqa: E402
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
