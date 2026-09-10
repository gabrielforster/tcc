"""Central configuration, read from environment variables or the .env file."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://collection:collection@localhost:5433/collection"
    redis_url: str = "redis://localhost:6380/0"

    data_source: str = "synthetic"
    synthetic_seed: int = 42
    synthetic_n_customers: int = 1200
    synthetic_n_invoices: int = 12000

    anonymization_salt: str = "change-this-salt"
    erp_database_url: str = ""

    # Pipeline directories
    dir_raw: Path = ROOT / "data" / "raw"
    dir_interim: Path = ROOT / "data" / "interim"
    dir_processed: Path = ROOT / "data" / "processed"
    dir_docs: Path = ROOT / "docs"

    def prepare_directories(self) -> None:
        for d in (self.dir_raw, self.dir_interim, self.dir_processed, self.dir_docs / "eda"):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
