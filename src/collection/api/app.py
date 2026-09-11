"""Inference API.

Serves the priority ranking that the active module consumes: given a batch of receivables,
it returns the probability, the decision at the model's tuned threshold, and the position
in the contact queue.

The ranking, not the probability, is the operational output. The active module works down
a finite queue every day, so what matters is the ordering -- which is also why the models
are selected on average precision.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException

from collection.api.schemas import (
    HealthResponse,
    ModelInfo,
    ScoredReceivable,
    ScoreRequest,
    ScoreResponse,
)
from collection.config import settings

TASKS = ("propensity", "default")
#: Columns carried through to the response but never fed to the model.
PASSTHROUGH = ("invoice_id", "customer_id")


def _model_path(task: str) -> Path:
    return settings.dir_processed / f"model_{task}.joblib"


def available_models() -> list[str]:
    return [task for task in TASKS if _model_path(task).exists()]


class ModelRegistry:
    """Loads the serialized champions once, at startup."""

    def __init__(self) -> None:
        self._bundles: dict[str, dict[str, Any]] = {}

    def load(self) -> None:
        for task in available_models():
            self._bundles[task] = joblib.load(_model_path(task))

    def get(self, task: str) -> dict[str, Any]:
        if task not in self._bundles:
            trained = ", ".join(self._bundles) or "none"
            raise HTTPException(
                status_code=404,
                detail=f"No model for task '{task}'. Trained: {trained}. Run `collection train`.",
            )
        return self._bundles[task]

    @property
    def tasks(self) -> list[str]:
        return sorted(self._bundles)


def create_app(registry: ModelRegistry | None = None) -> FastAPI:
    registry = registry or ModelRegistry()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        registry.load()
        yield

    app = FastAPI(
        title="Collection scoring API",
        description="Priority ranking for the active collection module.",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", models=registry.tasks)

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
