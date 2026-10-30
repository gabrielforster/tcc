import importlib.util
import os
import re
import subprocess
import sys

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from collection.config import ROOT, settings
from collection.db.base import TenantScoped
from collection.db.migrations import alembic_config
from collection.db.models.conversation import ConversationState
from collection.db.models.customer import ReceivableStatus
from collection.db.models.knowledge import Audience
from collection.db.models.tenant import TenantState
from collection.domain.delivery import DeliveryStatus
from collection.domain.intent import Intent


@pytest.mark.db
def test_pgvector_extension_is_installed(db):
    assert (
        db.execute(text("select extname from pg_extension where extname='vector'")).scalar()
        == "vector"
    )


def _identity(url: str) -> tuple:
    """Which database a URL really reaches: its name and its cluster's identifier."""
    engine = create_engine(url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            return tuple(
                conn.execute(
                    text(
                        "select current_database(), "
                        "(select system_identifier from pg_control_system())"
                    )
                ).one()
            )
    finally:
        engine.dispose()


def _skip_unless_provably_separate() -> None:
    """Skip unless the test database is shown to differ from the development one.

    The server answers the question, so no spelling of a host (an alias, a compose service
    name, a LAN address) can fool it. Anything that stops the proof, such as a failed
    connection or an unusable URL, also skips: not knowing is not the same as different.
    """
    if not settings.test_database_url:
        pytest.skip("downgrades to base: set TEST_DATABASE_URL to a separate throwaway database")
    try:
        same = _identity(settings.test_database_url) == _identity(settings.database_url)
    except Exception as exc:  # noqa: BLE001
        # Deliberately broad: the only question is "can I prove these differ?", and every
        # failure (no driver, bad URL, unreachable server) answers no. pytest.skip raises
        # Skipped, a BaseException, so the skips below are not swallowed by this clause.
        pytest.skip(f"cannot prove TEST_DATABASE_URL differs from DATABASE_URL: {exc}")
    if same:
        pytest.skip(
            "downgrades to base: TEST_DATABASE_URL is the same database as DATABASE_URL; "
            "point it at a separate throwaway database"
        )


@pytest.mark.db
def test_migrations_run_to_head_and_back(db_engine):
    """Round-trip the migrations.

    Gated on TEST_DATABASE_URL because `downgrade base` drops everything the migrations
    created. Without a dedicated test database the fixture falls back to DATABASE_URL, and
    this test would wipe a developer's own data.
    """
    _skip_unless_provably_separate()
    cfg = alembic_config(db_engine.url.render_as_string(hide_password=False))

    def installed() -> bool:
        with db_engine.connect() as conn:
            return (
                conn.execute(text("select 1 from pg_extension where extname='vector'")).scalar()
                is not None
            )

    command.downgrade(cfg, "base")
    assert not installed()
    command.upgrade(cfg, "head")
    assert installed()


@pytest.mark.db
def test_db_session_is_rolled_back_between_tests(db):
    db.execute(text("create temp table scratch (n int)"))
    db.execute(text("insert into scratch values (1)"))
    assert db.execute(text("select count(*) from scratch")).scalar() == 1


@pytest.mark.db
def test_db_session_starts_clean(db):
    exists = db.execute(text("select to_regclass('pg_temp.scratch')")).scalar()
    assert exists is None


def _migration(name: str):
    """A migration module loaded by path, since `migrations/versions` is not a package."""
    path = ROOT / "migrations" / "versions" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"migration_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: Every native enum, as the migration froze it against the enum the application uses.
#: A migration must keep its literal -- it describes the database as it was created -- so
#: the agreement is asserted here instead, and a member added to one of these enums fails
#: in CI rather than at the first INSERT against an already-migrated database.
FROZEN_ENUMS = [
    ("0002_tenants", "STATES", TenantState),
    ("0003_domain", "STATUSES", ReceivableStatus),
    ("0004_knowledge", "AUDIENCES", Audience),
    ("0005_conversations", "STATES", ConversationState),
    ("0005_conversations", "INTENTS", Intent),
    ("0006_deliveries", "STATUSES", DeliveryStatus),
]


@pytest.mark.parametrize(
    ("module", "frozen", "enum"), FROZEN_ENUMS, ids=[f"{m}.{f}" for m, f, _ in FROZEN_ENUMS]
)
def test_the_frozen_migration_enums_equal_the_live_enums(module, frozen, enum):
    assert list(getattr(_migration(module), frozen)) == [m.value for m in enum]


def test_the_frozen_vector_dimension_equals_the_configured_one():
    """0004's `Vector(384)` and EMBEDDING_DIMENSIONS have to agree.

    They are two statements of one fact: the column was created at that width, and the
    runtime guard in `rag/store.py` checks the embedder against the setting, not against
    the column. Raising the setting for a wider model therefore passes every check and
    fails at the INSERT. Changing the dimension is a migration that re-embeds; this is
    what says so before the first row is written.
    """
    source = (ROOT / "migrations" / "versions" / "0004_knowledge.py").read_text()
    frozen = re.findall(r"Vector\((\d+)\)", source)
    assert frozen == [str(settings.embedding_dimensions)]


def test_tenant_scoped_declares_an_indexed_required_foreign_key():
    col = TenantScoped.tenant_id.column
    assert col.nullable is False
    assert col.index is True
    assert {fk.target_fullname for fk in col.foreign_keys} == {"tenants.id"}


def _run_fixture_in_subprocess(require_db: bool) -> str:
    env = {**os.environ, "TEST_DATABASE_URL": "postgresql+psycopg://x:x@127.0.0.1:1/x"}
    env.pop("COLLECTION_REQUIRE_DB", None)
    if require_db:
        env["COLLECTION_REQUIRE_DB"] = "1"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_db_setup.py::test_pgvector_extension_is_installed",
            "-p",
            "no:cacheprovider",
            "-rsE",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.stdout + proc.stderr


def test_db_engine_skips_when_postgres_is_unreachable():
    out = _run_fixture_in_subprocess(require_db=False)
    assert "1 skipped" in out
    assert "1 error" not in out
    assert "failed" not in out.split("short test summary")[-1].splitlines()[-1]


def test_db_engine_fails_when_the_database_is_required_but_unreachable():
    out = _run_fixture_in_subprocess(require_db=True)
    assert "1 error" in out
    assert "skipped" not in out
