import os
import subprocess
import sys

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from collection.config import ROOT
from collection.db.base import Base, TenantScoped


@pytest.mark.db
def test_pgvector_extension_is_installed(db):
    assert (
        db.execute(text("select extname from pg_extension where extname='vector'")).scalar()
        == "vector"
    )


@pytest.mark.db
def test_migrations_run_to_head_and_back(db_engine):
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_engine.url.render_as_string(hide_password=False))

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
    assert Base.metadata.tables == {}


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
