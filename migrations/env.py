"""Alembic environment.

The URL comes from `collection.config` rather than alembic.ini so there is one place that
says where the database is. A caller that needs another database (the test fixture) sets
`sqlalchemy.url` on the Config, which wins.
"""

from alembic import context
from sqlalchemy import create_engine, pool

from collection.config import settings
from collection.db.base import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or settings.database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
