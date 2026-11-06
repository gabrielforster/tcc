"""One replication's world: a cohort of receivables, what the system can see of it, and what
it cannot.

The split between `Visible` and `Latent` is the experiment's leakage rule made structural.
An arm decides whom to contact from `Visible` only -- amount, due date, the model's risk
scores -- and is never handed a `Latent`. The simulator holds the other half: the
customer's propensity (which drives how they answer a contact) and the date they would
have paid had nobody called (the untreated outcome). An arm that could read either would
be measured against an oracle of itself.

How a replication is built, for a seed:

1. Generate the synthetic book with the same generator the predictive module trains on,
   run far enough past the decision day that every untreated payment inside the horizon
   has happened.
2. Freeze what was knowable on the decision day `T0`: payments after it are erased,
   delays are recomputed against it, contacts and agreements after it are dropped.
3. Train the two champions' families on the labelled rows of that frozen view, and score
   the cohort -- receivables already issued on `T0` and due in the 30 days after it.

The cohort's untreated outcome comes from the unfrozen book. The generator draws payment
dates independently of its own contact history, so that date really is the
no-intervention outcome, which is what lets every arm be measured against the same
counterfactual.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from collection.data.sources.synthetic import SyntheticSource
from collection.domain.schema import (
    DEFAULT_THRESHOLD_DAYS,
    PROPENSITY_WINDOW_DAYS,
    TARGET_TASK_1,
    TARGET_TASK_2,
)
from collection.features.build import Splits, build_features
from collection.models.candidates import build_pipeline, candidates

#: The decision day. Fixed, not `today()`: a replication is a function of its seed alone.
DECISION_DAY = date(2026, 3, 2)
#: Receivables falling due in this many days after the decision day form the cohort.
COHORT_DAYS = 30
#: How long after the last due date the outcome is read. Matches the 60-day default line.
FOLLOW_UP_DAYS = DEFAULT_THRESHOLD_DAYS
#: Upper bound on attempts per receivable the random draws are prepared for.
MAX_DRAWS = 12

#: The champion family per task (docs/model-synthetic-*.md), with their default settings:
#: tuning per replication would cost minutes and measure the search, not the ranking.
RANKERS = {
    "propensity": "xgboost + scale_pos_weight",
    "default": "random_forest + class_weight",
}


@dataclass(frozen=True)
class Visible:
    """What the collection system knows about one receivable on the decision day."""

    index: int
    invoice_id: str
    customer_id: str
    amount: float
    due: date
    #: 1 - P(settled within 30 days of the due date), from the propensity model.
    risk_overdue: float
    #: P(more than 60 days late), from the default model.
    risk_default: float
    opted_out: bool


@dataclass(frozen=True)
class Latent:
    """What only the simulator knows."""

    propensity: float
    #: The day the customer pays if nobody contacts them; None if they never do.
    untreated_payment: date | None


@dataclass
class Population:
    seed: int
    start: date
    end: date
    visible: list[Visible]
    latent: list[Latent]
    #: Uniform draws per (receivable, attempt), shared by every arm: common random numbers,
    #: so two arms that make the same contact see the same answer.
    draws: np.ndarray
    #: Days between a commitment and the payment, per (receivable, attempt).
    lags: np.ndarray
    mean_propensity: float
    #: Ranking quality on the cohort, each score against its own task's untreated outcome.
    #: A sanity check on the freeze: an AUC near 1 would mean the future leaked into the scores.
    auc_overdue: float
    auc_default: float

    def at_risk(self) -> list[int]:
        """Receivables that, untreated, are not paid by their due date."""
        return [
            v.index
            for v, latent in zip(self.visible, self.latent, strict=True)
            if latent.untreated_payment is None or latent.untreated_payment > v.due
        ]


def freeze(tables: dict[str, pd.DataFrame], day: date) -> dict[str, pd.DataFrame]:
    """The book as it stood at the start of `day`: nothing dated on or after it survives."""
    invoices = tables["invoices"].copy()
    invoices = invoices[invoices["issue_date"] < day].copy()
    paid_later = invoices["payment_date"].apply(lambda d: d is not None and d >= day)
    invoices.loc[paid_later, "payment_date"] = None

    def days_late(payment: date | None, due: date) -> int:
        end = payment if payment is not None else day
        return max((end - due).days, 0)

    invoices["days_late"] = [
        days_late(p, d) for p, d in zip(invoices["payment_date"], invoices["due_date"], strict=True)
    ]

    def status(payment: date | None, due: date, late: int) -> str:
        if payment is not None:
            return "paid" if late <= 0 else "paid_late"
        if due >= day:
            return "open"
        return "defaulted" if late > DEFAULT_THRESHOLD_DAYS else "open"

    invoices["status"] = [
        status(p, d, late)
        for p, d, late in zip(
            invoices["payment_date"], invoices["due_date"], invoices["days_late"], strict=True
        )
    ]
    events = tables["collection_events"]
    events = events[pd.to_datetime(events["event_time"]) < pd.Timestamp(day)]
    agreements = tables["agreements"]
    if not agreements.empty:
        agreements = agreements[pd.to_datetime(agreements["agreement_date"]) < pd.Timestamp(day)]
    return {
        "customers": tables["customers"],
        "invoices": invoices,
        "collection_events": events,
        "agreements": agreements,
    }


def _rank(task: str, frozen: dict[str, pd.DataFrame], cohort_ids: set[str]) -> pd.Series:
    """Train `task`'s champion family on the frozen past and score the cohort."""
    target = TARGET_TASK_1 if task == "propensity" else TARGET_TASK_2
    train = build_features(task, frozen)
    empty = train.iloc[:0]
    splits = Splits(task=task, train=train, validation=empty, test=empty, target=target)
    candidate = next(c for c in candidates(splits) if c.name == RANKERS[task])
    pipeline = build_pipeline(candidate, splits)
    pipeline.fit(train.drop(columns=[target]), train[target].to_numpy())

    unlabelled = build_features(task, frozen, labelled=False)
    cohort = unlabelled[unlabelled["invoice_id"].isin(cohort_ids)]
    probability = pipeline.predict_proba(cohort.drop(columns=[target]))[:, 1]
    return pd.Series(probability, index=cohort["invoice_id"].to_numpy())


def build_population(
    seed: int,
    *,
    n_customers: int = 1200,
    n_invoices: int = 12000,
    decision_day: date = DECISION_DAY,
) -> Population:
    end = decision_day + timedelta(days=COHORT_DAYS + FOLLOW_UP_DAYS)
    # Generated past the horizon, so every untreated payment inside it is known.
    source = SyntheticSource(
        n_customers=n_customers,
        n_invoices=n_invoices,
        seed=seed,
        as_of=end + timedelta(days=30),
    )
    raw, propensity = source.extract_with_propensity()
    tables = {
        "customers": raw.customers,
        "invoices": raw.invoices,
        "collection_events": raw.events,
        "agreements": raw.agreements,
    }
    frozen = freeze(tables, decision_day)

    invoices = raw.invoices
    in_cohort = (
        (invoices["issue_date"] < decision_day)
        & (invoices["due_date"] >= decision_day)
        & (invoices["due_date"] < decision_day + timedelta(days=COHORT_DAYS))
    )
    cohort = invoices[in_cohort].sort_values(["due_date", "invoice_id"]).reset_index(drop=True)
    ids = set(cohort["invoice_id"])

    paid_in_30 = _rank("propensity", frozen, ids)
    default = _rank("default", frozen, ids)
    opted_out = dict(zip(raw.customers["customer_id"], raw.customers["opt_out"], strict=True))

    visible: list[Visible] = []
    latent: list[Latent] = []
    for i, row in enumerate(cohort.itertuples(index=False)):
        visible.append(
            Visible(
                index=i,
                invoice_id=row.invoice_id,
                customer_id=row.customer_id,
                amount=float(row.amount),
                due=row.due_date,
                risk_overdue=float(1.0 - paid_in_30[row.invoice_id]),
                risk_default=float(default[row.invoice_id]),
                opted_out=bool(opted_out[row.customer_id]),
            )
        )
        latent.append(
            Latent(
                propensity=float(propensity[row.customer_id]),
                untreated_payment=row.payment_date,
            )
        )

    rng = np.random.default_rng([seed, 2026])
    draws = rng.random((len(visible), MAX_DRAWS))
    lags = rng.integers(0, 6, (len(visible), MAX_DRAWS))

    late = np.array(
        [
            lt.untreated_payment is None
            or lt.untreated_payment > v.due + timedelta(days=PROPENSITY_WINDOW_DAYS)
            for v, lt in zip(visible, latent, strict=True)
        ]
    )
    defaulted = np.array(
        [
            lt.untreated_payment is None
            or lt.untreated_payment > v.due + timedelta(days=DEFAULT_THRESHOLD_DAYS)
            for v, lt in zip(visible, latent, strict=True)
        ]
    )
    return Population(
        seed=seed,
        start=decision_day,
        end=end,
        visible=visible,
        latent=latent,
        draws=draws,
        lags=lags,
        mean_propensity=float(propensity.mean()),
        auc_overdue=_auc(late, [v.risk_overdue for v in visible]),
        auc_default=_auc(defaulted, [v.risk_default for v in visible]),
    )


def _auc(truth: np.ndarray, score: list[float]) -> float:
    if truth.all() or not truth.any():
        return float("nan")
    return float(roc_auc_score(truth, score))
