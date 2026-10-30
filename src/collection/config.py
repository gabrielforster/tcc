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

    # Web chat. The widget is embedded on another origin by definition, so the browser
    # will preflight every call; `*` is the default because an anonymous FAQ chat has
    # nothing to leak, and a deployment that identifies customers should narrow it to its
    # own domains.
    chat_enabled: bool = True
    chat_allowed_origins: str = "*"
    # Demo only, and off by default: seed the proactive agent's scheduled contacts into
    # the chat channel this process is serving, so the thread is waiting when the widget
    # opens. It is a setting rather than an argument because `collection serve` starts
    # uvicorn by import string, so the flag has to reach the app through the environment.
    # Never enable it in a deployment: it fabricates contacts for fixture customers.
    chat_seed_demo: bool = False

    # Generation. The key is read from the environment like any other secret and is never
    # committed; without it the LLM responder refuses and the template arm still runs.
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-5-5"
    anthropic_api_key: str = ""

    # Parallelism for training, search and permutation importance, passed as `n_jobs`.
    # -1 takes every core, right for `make train`; the test suite lowers it so two runs
    # side by side do not each claim the whole machine.
    n_jobs: int = -1

    # Pipeline directories
    dir_raw: Path = ROOT / "data" / "raw"
    dir_interim: Path = ROOT / "data" / "interim"
    dir_processed: Path = ROOT / "data" / "processed"
    dir_docs: Path = ROOT / "docs"
    dir_knowledge: Path = ROOT / "knowledge"

    @property
    def chat_origins(self) -> list[str]:
        return [origin.strip() for origin in self.chat_allowed_origins.split(",") if origin.strip()]

    def prepare_directories(self) -> None:
        for d in (self.dir_raw, self.dir_interim, self.dir_processed, self.dir_docs / "eda"):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
