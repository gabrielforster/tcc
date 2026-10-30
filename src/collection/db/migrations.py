"""One way to build the Alembic Config, shared by the CLI and the test fixtures.

Alembic stores options in a ConfigParser, where `%` starts an interpolation, so a URL
with a percent-encoded password has to be escaped on the way in. Doing it here means no
caller can forget.
"""

from alembic.config import Config

from collection.config import ROOT


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    if url:
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg
