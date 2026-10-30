"""Inference API.

Serves the priority ranking that the active module consumes: given a batch of receivables,
it returns the probability, the decision at the model's tuned threshold, and the position
in the contact queue.

The ranking, not the probability, is the operational output. The active module works down
a finite queue every day, so what matters is the ordering -- which is also why the models
are selected on average precision.

The models are the platform's, so the registry asks the record which version of each is
current and fetches its weights from object storage. It never looks in a directory: a
replica started on a machine that has never trained anything serves the same model as any
other. If the record or the bucket cannot be reached at startup the registry comes up
empty and says so, and `/score` answers 404 for every task, as it does when nothing has
been trained; one dependency being down must not stop the chat from serving.
"""

import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from collection.api.chat import ChatService, create_router
from collection.api.schemas import (
    HealthResponse,
    ModelInfo,
    ScoredReceivable,
    ScoreRequest,
    ScoreResponse,
)
from collection.config import settings
from collection.db.session import unit_of_work
from collection.models.store import load_bundle
from collection.storage.objects import ObjectStoreError

logger = logging.getLogger(__name__)

TASKS = ("propensity", "default")
#: Columns carried through to the response but never fed to the model.
PASSTHROUGH = ("invoice_id", "customer_id")


@dataclass(frozen=True)
class Loaded:
    """What a load found, and why anything is missing from it."""

    bundles: dict[str, dict[str, Any]] = field(default_factory=dict)
    problems: dict[str, str] = field(default_factory=dict)


def load_published() -> Loaded:
    """The latest published bundle of each task, from the record and the platform prefix.

    Each task is loaded on its own: one unreadable artifact must not take the others with
    it. The per-task catch is broad on purpose -- a truncated or incompatible pickle raises
    `UnpicklingError`, `ModuleNotFoundError` or `AttributeError`, none of which say
    "storage" -- and every failure is kept as a reason instead of being swallowed.
    """
    bundles: dict[str, dict[str, Any]] = {}
    problems: dict[str, str] = {}
    try:
        with unit_of_work(platform=True) as session:
            for task in TASKS:
                try:
                    bundle = load_bundle(session, task)
                except Exception as exc:  # noqa: BLE001 - see the docstring
                    logger.warning("model '%s' could not be loaded: %s", task, exc)
                    problems[task] = f"{type(exc).__name__}: {exc}"
                    continue
                if bundle is not None:
                    bundles[task] = bundle
    except (SQLAlchemyError, ObjectStoreError) as exc:
        logger.warning("no models loaded: the record or object storage is unreachable: %s", exc)
        return Loaded(bundles, {task: f"{type(exc).__name__}: {exc}" for task in TASKS})
    return Loaded(bundles, problems)


class ModelRegistry:
    """Loads the published champions at startup, and again if that load was degraded.

    An outage at startup must not become a permanent "nothing is trained": the registry
    remembers why a task is missing, `/health` reports it, `get` answers 503 (retry) rather
    than 404 (train something) while it is, and the load is retried lazily, at most once
    per `RETRY_SECONDS`, by the next request that needs a model.
    """

    RETRY_SECONDS = 5.0

    def __init__(self, loader: Callable[[], Loaded | dict[str, dict[str, Any]]] = load_published):
        self._loader = loader
        self._bundles: dict[str, dict[str, Any]] = {}
        self._problems: dict[str, str] = {}
        self._attempted_at = float("-inf")

    def load(self) -> None:
        self._attempted_at = time.monotonic()
        loaded = self._loader()
        if not isinstance(loaded, Loaded):
            loaded = Loaded(dict(loaded))
        self._bundles, self._problems = dict(loaded.bundles), dict(loaded.problems)

    @property
    def degraded(self) -> bool:
        return bool(self._problems)

    def get(self, task: str) -> dict[str, Any]:
        if (
            task not in self._bundles
            and self._problems
            and time.monotonic() - self._attempted_at >= self.RETRY_SECONDS
        ):
            self.load()
        if task not in self._bundles:
            if self._problems:
                raise HTTPException(
                    status_code=503,
                    detail=f"Models are still unavailable ({self._problems_text()}). "
                    "Try again in a moment.",
                )
            trained = ", ".join(self._bundles) or "none"
            raise HTTPException(
                status_code=404,
                detail=f"No model for task '{task}'. Trained: {trained}. Run `collection train`.",
            )
        return self._bundles[task]

    def _problems_text(self) -> str:
        return "; ".join(f"{task}: {why}" for task, why in sorted(self._problems.items()))

    @property
    def tasks(self) -> list[str]:
        return sorted(self._bundles)


def create_app(
    registry: ModelRegistry | None = None,
    chat: ChatService | None = None,
) -> FastAPI:
    registry = registry or ModelRegistry()
    chat = chat or ChatService()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        registry.load()
        if settings.chat_enabled:
            # Building the retrieval index costs a second or two. Paying it at startup
            # keeps it off the first customer's latency, which is the one measured.
            chat.load()
            if settings.chat_seed_demo:
                # The demo's outbound half, run against *this* channel. A mailbox is an
                # in-memory dict, so seeding from another process would fill a mailbox
                # nobody serves; doing it here is what makes "open the widget and the
                # message is waiting" true rather than a claim.
                from collection.channels.outbound import run as seed_contacts
                from collection.channels.outbound import summary_lines

                for line in summary_lines(seed_contacts(channel=chat.channel)):
                    # Printed rather than logged: uvicorn configures its own loggers and
                    # leaves the root logger alone, so an INFO record from this module
                    # would be dropped exactly where it is supposed to be visible.
                    # Flushed, because stdout is block-buffered when the server's output
                    # is piped -- which is precisely when someone is reading the log.
                    print(line, flush=True)
        yield

    app = FastAPI(
        title="Collection API",
        description="Priority ranking for the active module, and the web chat channel.",
        version="0.1.0",
        lifespan=lifespan,
    )

    if settings.chat_enabled:
        # The widget is embedded on another origin by definition, so without CORS the
        # browser refuses every call before it reaches this process.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.chat_origins,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type"],
        )
        app.include_router(create_router(chat))

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="degraded" if registry.degraded else "ok", models=registry.tasks
        )

    @app.get("/models/{task}", response_model=ModelInfo)
    def model_info(task: str) -> ModelInfo:
        bundle = registry.get(task)
        return ModelInfo(
            task=task,
            target=bundle["target"],
            champion=bundle["champion"],
            source=bundle["source"],
            threshold=bundle["threshold"],
            features=bundle["features"],
        )

    @app.post("/score", response_model=ScoreResponse)
    def score(request: ScoreRequest) -> ScoreResponse:
        bundle = registry.get(request.task)
        frame = pd.DataFrame(request.receivables)

        expected = list(bundle["features"])
        missing = [c for c in expected if c not in frame.columns]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"Missing feature columns: {missing[:10]}"
                + (f" (+{len(missing) - 10} more)" if len(missing) > 10 else ""),
            )
        unexpected = [c for c in frame.columns if c not in expected and c not in PASSTHROUGH]
        if unexpected:
            raise HTTPException(status_code=422, detail=f"Unknown columns: {unexpected[:10]}")

        probabilities = bundle["pipeline"].predict_proba(frame[expected])[:, 1]
        threshold = float(bundle["threshold"])

        ordered = sorted(range(len(frame)), key=lambda i: float(probabilities[i]), reverse=True)
        priority = {index: rank for rank, index in enumerate(ordered, start=1)}

        results = [
            ScoredReceivable(
                invoice_id=_optional(frame, "invoice_id", i),
                customer_id=_optional(frame, "customer_id", i),
                probability=round(float(probabilities[i]), 6),
                decision=bool(probabilities[i] >= threshold),
                priority=priority[i],
            )
            for i in range(len(frame))
        ]

        return ScoreResponse(
            task=request.task,
            target=bundle["target"],
            model=bundle["champion"],
            source=bundle["source"],
            threshold=threshold,
            scored=len(results),
            results=results,
        )

    return app


def _optional(frame: pd.DataFrame, column: str, index: int) -> str | None:
    if column not in frame.columns:
        return None
    value = frame.iloc[index][column]
    return None if pd.isna(value) else str(value)


app = create_app()
