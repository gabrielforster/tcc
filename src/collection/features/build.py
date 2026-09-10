"""Feature engineering and temporal split (deliverable 5 of the schedule).

Two feature tables, one per predictive task:

* Task 1 -- payment propensity. Population: already-due receivables. Reference date: the
  due date. Target: settled within 30 days of the due date.
* Task 2 -- future default. Population: receivables not yet due at decision time.
  Reference date: the issue date. Target: goes past 60 days late without settling.

The rule that governs this module: **no feature may use information dated after
`reference_date`**. Customer history is assembled with `expanding().shift(1)` over prior
receivables, and events/agreements enter through `merge_asof`, which only ever looks
backwards. The split is chronological (70/15/15) for the same reason: evaluate on a future
period, the way the system runs in production.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from collection.config import settings
from collection.data.ingest import load_interim
from collection.domain.schema import (
    DEFAULT_THRESHOLD_DAYS,
    PROPENSITY_WINDOW_DAYS,
    TARGET_TASK_1,
    TARGET_TASK_2,
)

CATEGORICAL = ["segment", "size_tier", "state", "preferred_channel", "payment_method", "due_month"]
BOOLEAN = ["opt_out"]
TASKS = ("propensity", "default")


@dataclass
class Splits:
    task: str
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame
    target: str

    def summary(self) -> pd.DataFrame:
        rows = []
        for name, df in [
            ("train", self.train),
            ("validation", self.validation),
            ("test", self.test),
        ]:
            rows.append(
                {
                    "split": name,
                    "n": len(df),
                    "start": df["reference_date"].min(),
                    "end": df["reference_date"].max(),
                    "positive_rate": round(float(df[self.target].mean()), 4),
                }
            )
        return pd.DataFrame(rows)


# ------------------------------------------------------------------------ history
def _invoice_history(invoices: pd.DataFrame, order_column: str) -> pd.DataFrame:
    """Customer history aggregates, always excluding the current receivable."""
    d = invoices.sort_values(["customer_id", order_column]).copy()
    d["was_late"] = (d["days_late"] > 0).astype(int)
    d["defaulted"] = (d["status"] == "defaulted").astype(int)

    g = d.groupby("customer_id", sort=False)
    prior = lambda s: s.shift(1)  # noqa: E731 - the current receivable never counts

    d["hist_invoice_count"] = g.cumcount()
    d["hist_avg_days_late"] = g["days_late"].apply(lambda s: prior(s).expanding().mean()).to_numpy()
    d["hist_max_days_late"] = g["days_late"].apply(lambda s: prior(s).expanding().max()).to_numpy()
    d["hist_late_rate"] = g["was_late"].apply(lambda s: prior(s).expanding().mean()).to_numpy()
    d["hist_default_rate"] = g["defaulted"].apply(lambda s: prior(s).expanding().mean()).to_numpy()
    d["hist_avg_amount"] = g["amount"].apply(lambda s: prior(s).expanding().mean()).to_numpy()
    # Trend: average delay over the last 3 invoices minus the lifetime average.
    d["hist_recent_days_late"] = (
        g["days_late"].apply(lambda s: prior(s).rolling(3, min_periods=1).mean()).to_numpy()
    )
    d["hist_days_late_trend"] = d["hist_recent_days_late"] - d["hist_avg_days_late"]
    d["hist_days_since_prev_invoice"] = (d[order_column] - g[order_column].shift(1)).dt.days
    return d


def _contact_history(
    base: pd.DataFrame, events: pd.DataFrame, agreements: pd.DataFrame
) -> pd.DataFrame:
    """Counts of contacts and agreements prior to the reference date (merge_asof)."""
    ref = base[["invoice_id", "customer_id", "reference_date"]].copy()
    # merge_asof requires both sides to share the same datetime resolution.
    ref["reference_date"] = ref["reference_date"].astype("datetime64[ns]")
    ref = ref.sort_values("reference_date")

    evt = events.copy()
    evt["event_time"] = pd.to_datetime(evt["event_time"]).astype("datetime64[ns]")
    evt = evt.sort_values("event_time")
    evt["replied"] = (evt["outcome"] != "no_answer").astype(int)
    g = evt.groupby("customer_id", sort=False)
    evt["hist_contact_count"] = g.cumcount() + 1
    evt["hist_response_rate"] = g["replied"].transform(lambda s: s.expanding().mean())
    evt["hist_contact_cost"] = g["contact_cost"].transform(lambda s: s.expanding().sum())

    ref = pd.merge_asof(
        ref,
        evt[
            [
                "event_time",
                "customer_id",
                "hist_contact_count",
                "hist_response_rate",
                "hist_contact_cost",
            ]
        ],
        left_on="reference_date",
        right_on="event_time",
        by="customer_id",
        allow_exact_matches=False,
    ).drop(columns=["event_time"])

    if not agreements.empty:
        agr = agreements.copy()
        agr["agreement_date"] = pd.to_datetime(agr["agreement_date"]).astype("datetime64[ns]")
        agr = agr.sort_values("agreement_date")
        ga = agr.groupby("customer_id", sort=False)
        agr["hist_agreement_count"] = ga.cumcount() + 1
        agr["hist_broken_agreements"] = ga["status"].transform(
            lambda s: (s == "broken").astype(int).expanding().sum()
        )
        ref = pd.merge_asof(
            ref,
            agr[
                ["agreement_date", "customer_id", "hist_agreement_count", "hist_broken_agreements"]
            ],
            left_on="reference_date",
            right_on="agreement_date",
            by="customer_id",
            allow_exact_matches=False,
        ).drop(columns=["agreement_date"])
    else:
        ref["hist_agreement_count"] = 0
        ref["hist_broken_agreements"] = 0

    columns = [c for c in ref.columns if c.startswith("hist_")]
    ref[columns] = ref[columns].fillna(0)
    return ref[["invoice_id", *columns]]


# ----------------------------------------------------------------------- features
def build_features(task: str = "propensity") -> pd.DataFrame:
    """Assemble the feature table for one of the two predictive tasks."""
    if task not in TASKS:
        raise ValueError(f"task must be one of {TASKS}")

    tables = load_interim()
    customers, invoices = tables["customers"], tables["invoices"].copy()
    if invoices.empty:
        raise ValueError(
            "The ingested dataset has no receivables, so the predictive tasks cannot be "
            "built from it. Sources covering contacts only (bank-marketing) feed "
            "contact-strategy analysis instead. Use DATA_SOURCE=home-credit, erp or "
            "synthetic for tasks 1 and 2."
        )
    for col in ("issue_date", "due_date", "payment_date"):
        invoices[col] = pd.to_datetime(invoices[col])

    # The dataset's "today": the last observed fact. Using the largest due date would
    # include not-yet-due receivables and censor payments still to come, inflating the
    # negative class near the end of the period.
    as_of = invoices["payment_date"].max()
    if pd.isna(as_of):
        as_of = invoices["due_date"].max()

    reference_column = "due_date" if task == "propensity" else "issue_date"
    d = _invoice_history(invoices, reference_column)
    d["reference_date"] = d[reference_column]

    # Population and target ---------------------------------------------------
    if task == "propensity":
        window = pd.Timedelta(days=PROPENSITY_WINDOW_DAYS)
        # Only receivables whose outcome is already observable within the window.
        d = d[d["due_date"] + window <= as_of]
        paid_in_window = d["payment_date"].notna() & (d["payment_date"] <= d["due_date"] + window)
        d[TARGET_TASK_1] = paid_in_window.astype(int)
        target = TARGET_TASK_1
    else:
        threshold = pd.Timedelta(days=DEFAULT_THRESHOLD_DAYS)
        d = d[d["due_date"] + threshold <= as_of]
        went_default = d["payment_date"].isna() | (d["payment_date"] > d["due_date"] + threshold)
        d[TARGET_TASK_2] = went_default.astype(int)
        target = TARGET_TASK_2

    # Attributes of the receivable itself -------------------------------------
    d["term_days"] = (d["due_date"] - d["issue_date"]).dt.days
    d["due_month"] = d["due_date"].dt.month
    d["due_weekday"] = d["due_date"].dt.dayofweek
    d["due_quarter"] = d["due_date"].dt.quarter

    d = d.merge(customers, on="customer_id", how="left")
    d["credit_limit"] = d["credit_limit"].replace(0, np.nan)
    d["amount_to_limit_ratio"] = d["amount"] / d["credit_limit"]
    d["months_since_signup"] = d["reference_date"].dt.to_period("M").astype(int) - pd.PeriodIndex(
        d["signup_date"], freq="M"
    ).astype(int)

    d = d.merge(
        _contact_history(d, tables["collection_events"], tables["agreements"]),
        on="invoice_id",
        how="left",
    )

    history_columns = [c for c in d.columns if c.startswith("hist_")]
    columns = [
        "invoice_id",
        "customer_id",
        "reference_date",
        target,
        "amount",
        "amount_to_limit_ratio",
        "credit_limit",
        "term_days",
        "due_month",
        "due_weekday",
        "due_quarter",
        "months_since_signup",
        *CATEGORICAL[:-1],
        *BOOLEAN,
        *history_columns,
    ]
    columns = list(dict.fromkeys(columns))
    output = d[columns].sort_values("reference_date").reset_index(drop=True)
    # A customer with no history: zero prior invoices, neutral metrics at zero.
    output[history_columns] = output[history_columns].fillna(0)
    return output


def build_splits(task: str = "propensity", ratios: tuple[float, float] = (0.70, 0.15)) -> Splits:
    """Chronological 70/15/15 split: train on the past, test on the future."""
    features = build_features(task)
    target = TARGET_TASK_1 if task == "propensity" else TARGET_TASK_2
    n = len(features)
    train_end = int(n * ratios[0])
    validation_end = int(n * (ratios[0] + ratios[1]))
    return Splits(
        task=task,
        train=features.iloc[:train_end].copy(),
        validation=features.iloc[train_end:validation_end].copy(),
        test=features.iloc[validation_end:].copy(),
        target=target,
    )


def save_splits(task: str = "propensity") -> dict[str, str]:
    settings.prepare_directories()
    splits = build_splits(task)
    paths = {}
    for name, df in [
        ("train", splits.train),
        ("validation", splits.validation),
        ("test", splits.test),
    ]:
        path = settings.dir_processed / f"{task}_{name}.parquet"
        df.to_parquet(path, index=False)
        paths[name] = str(path)
    return paths
