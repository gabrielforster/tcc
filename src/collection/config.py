"""Central configuration, read from environment variables or the .env file."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://collection:collection@localhost:5433/collection"
    # Tests run against this database when set, so a developer's data is never rolled
    # through by a fixture; empty means "use database_url".
    test_database_url: str = ""
    redis_url: str = "redis://localhost:6380/0"

    # S3-compatible object storage (MinIO locally). The defaults match docker-compose.yml.
    s3_endpoint_url: str = "http://localhost:9000"
    s3_bucket: str = "collection"
    s3_access_key: str = "collection"
    s3_secret_key: str = "collection-secret"

    # Embeddings. The dimension is part of the schema (pgvector columns are fixed-width),
    # so changing the model to one with another width needs a migration, not just a setting.
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dimensions: int = 384

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
    # The tenant a chat request belongs to when it names none -- the demo page, and any
    # single-tenant deployment. Empty means there is no database behind the chat at all:
    # the channel runs on the in-memory store, which is what a fresh clone and the test
    # suite do. A key is public (it travels in an embed) and identifies, never authorises.
    default_tenant_key: str = ""

    # Generation. The key is read from the environment like any other secret and is never
    # committed; without it the LLM responder refuses and the template arm still runs.
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-5-5"
    anthropic_api_key: str = ""
    # The `openai` provider: any server speaking the OpenAI chat protocol. The default is
    # the Ollama service in docker-compose.yml; a hosted one (Groq, OpenRouter) is a base
    # URL and a key. The timeout is generous because a local model runs on a CPU.
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = ""
    llm_timeout: float = 180.0

    # Parallelism for training, search and permutation importance, passed as `n_jobs`.
    # -1 takes every core, right for `make train`; the test suite lowers it so two runs
    # side by side do not each claim the whole machine.
    n_jobs: int = -1

    # How long a closed conversation's transcript is kept. LGPD art. 15-16: processing
    # ends and the data is eliminated, so a transcript needs a window rather than a
    # default of forever. Six months is the span in which a complaint about how a
    # collection was conducted is still live and the transcript is still the evidence
    # for it; past that the thread's shape (when, which channel, whether it escalated)
    # is all the reporting needs, and the words are a liability without a purpose.
    transcript_retention_days: int = 180

    # The voice call. LiveKit runs self-hosted from docker-compose in dev mode, whose
    # well-known key pair is `devkey`/`secret`; a deployment overrides all three. The
    # recordings and voices live under `data/`, which is never committed: a recording is
    # biometric personal data, and a voice model is a 60 MB download, not source.
    livekit_url: str = "ws://127.0.0.1:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "secret"
    dir_calls: Path = ROOT / "data" / "calls"
    dir_voices: Path = ROOT / "data" / "voices"

    # `dir_raw` is a download cache, not pipeline output: the public sources read local
    # files, so a fetched Kaggle or UCI archive is kept here rather than downloaded
    # again. Everything the pipeline produces -- frames, splits, preprocessors, models,
    # metrics -- is an object under the tenant prefix. `data/interim` and
    # `data/processed` are gone with the paths that named them: nothing had written to
    # either since the move, and a configured path nothing writes is a claim about where
    # the data lives that is no longer true.
    dir_raw: Path = ROOT / "data" / "raw"
    dir_docs: Path = ROOT / "docs"
    dir_knowledge: Path = ROOT / "knowledge"

    @property
    def chat_origins(self) -> list[str]:
        return [origin.strip() for origin in self.chat_allowed_origins.split(",") if origin.strip()]

    def prepare_directories(self) -> None:
        for d in (self.dir_raw, self.dir_docs / "eda"):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
