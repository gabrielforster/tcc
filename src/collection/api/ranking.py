"""The ranking itself: a batch of receivables in, the contact queue out.

Kept apart from the route so that `POST /score` and the contact-queue page rank with the
same function. Two copies of "sort by probability, decide at the tuned threshold" would
drift, and the page exists to show what the API does -- not an approximation of it.
"""

from typing import Any

import pandas as pd

from collection.api.schemas import ScoredReceivable, ScoreResponse
from collection.telemetry import SCORING_BATCH, SCORING_DURATION, span, timed

#: Columns carried through to the response but never fed to the model.
PASSTHROUGH = ("invoice_id", "customer_id")


class InvalidBatch(ValueError):
    """The batch does not match the model's feature contract; the route answers 422."""


def rank(bundle: dict[str, Any], frame: pd.DataFrame, task: str) -> ScoreResponse:
    """Score `frame` with `bundle` and order it into the contact queue.

    Missing and unknown columns are refused rather than filled in: a typo in a feature
    name would otherwise become a missing value and a quietly wrong position in the queue.
    """
    expected = list(bundle["features"])
    missing = [c for c in expected if c not in frame.columns]
    if missing:
        raise InvalidBatch(
            f"Missing feature columns: {missing[:10]}"
            + (f" (+{len(missing) - 10} more)" if len(missing) > 10 else "")
        )
    unexpected = [c for c in frame.columns if c not in expected and c not in PASSTHROUGH]
    if unexpected:
        raise InvalidBatch(f"Unknown columns: {unexpected[:10]}")

    # The batch size and the champion are what move the latency, so they label it.
    labels = {"task": task, "model": str(bundle["champion"])}
    with (
        span("scoring.predict", batch_size=len(frame), **labels),
        timed(SCORING_DURATION, **labels),
    ):
        probabilities = bundle["pipeline"].predict_proba(frame[expected])[:, 1]
        ordered = sorted(range(len(frame)), key=lambda i: float(probabilities[i]), reverse=True)
    SCORING_BATCH.record(len(frame), labels)
    threshold = float(bundle["threshold"])

    priority = {index: position for position, index in enumerate(ordered, start=1)}

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
        task=task,
        target=bundle["target"],
        model=bundle["champion"],
        source=bundle["source"],
        threshold=threshold,
        scored=len(results),
        results=results,
    )


def _optional(frame: pd.DataFrame, column: str, index: int) -> str | None:
    if column not in frame.columns:
        return None
    value = frame.iloc[index][column]
    return None if pd.isna(value) else str(value)
