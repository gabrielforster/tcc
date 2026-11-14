"""The contact queue the demo page shows: a synthetic open book, ranked by the live model.

The book is drawn from `SyntheticSource`, the generator `collection tenant seed` ingests,
and anonymized the way ingestion anonymizes, so the page shows pseudonyms and the
features are built exactly as training built them. It is generated in-process rather than
read from a tenant's ingested frames because the page must also work where there is no
tenant at all -- a fresh clone, the test suite -- and because a public page that reads a
tenant's record would be one more path to a tenant's data that the gate does not guard.

The population is the one the propensity model is *applied* to: receivables already due,
still open, and inside the 30-day window whose outcome the model predicts. Ranking goes
through `rank`, the function behind `POST /score`, so the page cannot disagree with the API.

What happens without a model is the registry's answer, unchanged: 404 while nothing has
been trained, 503 while the record or the bucket is unreachable. The page turns both into
a message rather than an empty table, which would read as "nobody owes anything".
"""

import uuid
from datetime import date
from functools import lru_cache
from typing import TYPE_CHECKING

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from collection.api.ranking import PASSTHROUGH, InvalidBatch, rank
from collection.data.anonymize import anonymize
from collection.data.sources.synthetic import SyntheticSource
from collection.domain.schema import PROPENSITY_WINDOW_DAYS
from collection.features.build import build_features

if TYPE_CHECKING:  # app.py includes this router, so it cannot be imported at runtime
    from collection.api.app import ModelRegistry

TASK = "propensity"
#: Small enough to build in well under a second, large enough to yield a queue of a few
#: dozen open receivables. Fixed, so two visitors on the same day see the same book.
BOOK_CUSTOMERS = 200
BOOK_INVOICES = 3000
BOOK_SEED = 2026
#: Pseudonyms are keyed per tenant; the demo book belongs to no tenant, so it gets a key
#: of its own that can never collide with a real tenant's.
BOOK_TENANT = uuid.UUID("00000000-0000-0000-0000-00000000f11a")


class QueueEntry(BaseModel):
    priority: int
    invoice_id: str | None
    customer_id: str | None
    amount: float
    days_late: int
    probability: float
    decision: bool


class QueueResponse(BaseModel):
    task: str
    model: str
    source: str
    threshold: float
    as_of: date
    scored: int
    entries: list[QueueEntry]


@lru_cache(maxsize=2)
def demo_book(as_of: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The open receivables of the day and their feature rows, as `(invoices, features)`.

    Cached per day: the book only changes when the date does, and building it is the one
    slow step of the page.
    """
    raw = SyntheticSource(
        n_customers=BOOK_CUSTOMERS, n_invoices=BOOK_INVOICES, seed=BOOK_SEED, as_of=as_of
    ).extract()
    tables = anonymize(raw, tenant=BOOK_TENANT)
    invoices = tables["invoices"]
    open_book = invoices[
        (invoices["status"] == "open")
        & (invoices["days_late"] > 0)
        & (invoices["days_late"] <= PROPENSITY_WINDOW_DAYS)
    ]
    # Unlabelled: these receivables' outcomes are still in the future, which is the
    # whole point of ranking them.
    features = build_features(TASK, tables, labelled=False)
    features = features[features["invoice_id"].isin(open_book["invoice_id"])]
    return open_book.reset_index(drop=True), features.reset_index(drop=True)


def create_queue_router(registry: "ModelRegistry") -> APIRouter:
    router = APIRouter()

    @router.get("/queue", response_model=QueueResponse)
    def queue() -> QueueResponse:
        """The demo's open book, ranked by the published propensity model."""
        bundle = registry.get(TASK)
        today = date.today()
        invoices, features = demo_book(today)
        columns = [c for c in (*PASSTHROUGH, *bundle["features"]) if c in features.columns]
        try:
            ranked = rank(bundle, features[columns], TASK)
        except InvalidBatch as error:
            # The published model expects columns this build does not produce: a model
            # trained by another version of the code. A server fault, not the caller's.
            raise HTTPException(status_code=500, detail=str(error)) from error

        facts = invoices.set_index("invoice_id")
        entries = sorted(
            (
                QueueEntry(
                    priority=row.priority,
                    invoice_id=row.invoice_id,
                    customer_id=row.customer_id,
                    amount=round(float(facts.at[row.invoice_id, "amount"]), 2),
                    days_late=int(facts.at[row.invoice_id, "days_late"]),
                    probability=row.probability,
                    decision=row.decision,
                )
                for row in ranked.results
            ),
            key=lambda entry: entry.priority,
        )
        return QueueResponse(
            task=TASK,
            model=ranked.model,
            source=ranked.source,
            threshold=ranked.threshold,
            as_of=today,
            scored=ranked.scored,
            entries=entries,
        )

    return router
