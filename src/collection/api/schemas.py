"""Request and response bodies for the scoring API."""

from typing import Any

from pydantic import BaseModel, Field


class ScoreRequest(BaseModel):
    """A batch of receivables to score.

    Each record carries the feature columns the model was trained on. Unknown keys are
    rejected rather than silently ignored, because a typo in a feature name would otherwise
    become a missing value and a quietly wrong score.
    """

    task: str = Field(default="propensity", description="propensity | default")
    receivables: list[dict[str, Any]] = Field(min_length=1, max_length=5000)


class ScoredReceivable(BaseModel):
    invoice_id: str | None = None
    customer_id: str | None = None
    probability: float = Field(ge=0.0, le=1.0)
    decision: bool
    priority: int = Field(description="1 is contacted first")


class ScoreResponse(BaseModel):
    task: str
    target: str
    model: str
    source: str
    threshold: float
    scored: int
    results: list[ScoredReceivable]


class ModelInfo(BaseModel):
    task: str
    target: str
    champion: str
    source: str
    threshold: float
    features: list[str]


class HealthResponse(BaseModel):
    status: str
    models: list[str]
