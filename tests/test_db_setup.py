import os
import subprocess
import sys

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import make_url

from collection.config import ROOT, settings
from collection.db.base import TenantScoped
from collection.db.migrations import alembic_config


@pytest.mark.db
def test_pgvector_extension_is_installed(db):
    assert (
        db.execute(text("select extname from pg_extension where extname='vector'")).scalar()
        == "vector"
    )


def _same_database(a: str, b: str) -> bool:
    def key(url: str):
        u = make_url(url)
        host = "localhost" if u.host in (None, "127.0.0.1", "::1") else u.host
        return host, u.port or 5432, u.database

    return key(a) == key(b)


_SHARED_OR_UNSET = not settings.test_database_url or _same_database(
    settings.test_database_url, settings.database_url
)


@pytest.mark.db
@pytest.mark.skipif(
    _SHARED_OR_UNSET,
    reason=(
        "downgrades to base: TEST_DATABASE_URL is unset or the same database as "
        "DATABASE_URL; point it at a separate throwaway database"
    ),
)
def test_migrations_run_to_head_and_back(db_engine):
    """Round-trip the migrations.

    Gated on TEST_DATABASE_URL because `downgrade base` drops everything the migrations
    created. Without a dedicated test database the fixture falls back to DATABASE_URL, and
    this test would wipe a developer's own data.
    """
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
