"""Exploratory data analysis.

Writes `docs/eda-<source>.md` with charts under `docs/eda/<source>/`, so reports from
different data sources sit side by side instead of overwriting each other.

Two report shapes, chosen by what the ingested source actually covers:

* sources with receivables (synthetic, home-credit, erp) get the standard report --
  descriptive statistics, class imbalance, missing values and outliers, plus the delay
  distribution, default rate by segment, seasonality and correlation heatmap;
* sources with contacts but no receivables (bank-marketing) get a contact-strategy report
  built around how fast repeated attempts stop paying off.
"""

from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import seaborn as sns  # noqa: E402

from collection.config import settings  # noqa: E402
from collection.data.ingest import load_interim, source_metadata  # noqa: E402
from collection.features.build import build_features  # noqa: E402

sns.set_theme(style="whitegrid", palette="deep")

#: Set per run so reports from different sources do not overwrite each other.
_SOURCE = "synthetic"


def _save(fig: plt.Figure, name: str) -> Path:
    destination = settings.dir_docs / "eda" / _SOURCE / f"{name}.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(destination, dpi=150)
    plt.close(fig)
    return destination


def _chart_delay(invoices: pd.DataFrame) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    late = invoices.loc[invoices["days_late"] > 0, "days_late"].clip(upper=180)
    sns.histplot(late, bins=45, ax=ax)
    ax.set(
        title="Distribution of days late (late receivables)",
        xlabel="days late (capped at 180)",
        ylabel="receivables",
    )
    return _save(fig, "01-days-late-distribution")


def _chart_segment(invoices: pd.DataFrame, customers: pd.DataFrame) -> Path:
    d = invoices.merge(
        customers[["customer_id", "segment", "size_tier"]], on="customer_id", how="left"
    )
    d["defaulted"] = (d["status"] == "defaulted").astype(int)
    summary = d.groupby(["segment", "size_tier"])["defaulted"].mean().reset_index()
    fig, ax = plt.subplots(figsize=(8, 4.5))
    sns.barplot(summary, x="segment", y="defaulted", hue="size_tier", ax=ax)
    ax.set(title="Default rate by segment and customer size", ylabel="share defaulted")
    return _save(fig, "02-default-rate-by-segment")


def _chart_seasonality(invoices: pd.DataFrame) -> Path:
    d = invoices.copy()
    d["month"] = pd.to_datetime(d["due_date"]).dt.to_period("M").astype(str)
    d["was_late"] = (d["days_late"] > 0).astype(int)
    series = d.groupby("month")["was_late"].mean().reset_index()
    fig, ax = plt.subplots(figsize=(9, 4.5))
    sns.lineplot(series, x="month", y="was_late", marker="o", ax=ax)
    ax.set(
        title="Seasonality: late rate by due month",
        ylabel="share paid late",
        xlabel="due month",
    )
    ax.tick_params(axis="x", rotation=60)
    return _save(fig, "03-seasonality")


def _chart_correlation(features: pd.DataFrame, target: str) -> Path:
    numeric = features.select_dtypes("number")
    if target in numeric:
        order = numeric.corr()[target].abs().sort_values(ascending=False).head(14).index
        numeric = numeric[order]
    fig, ax = plt.subplots(figsize=(9, 7.5))
    sns.heatmap(numeric.corr(), cmap="vlag", center=0, annot=False, ax=ax)
    ax.set(title="Correlation between numeric features and the target")
    return _save(fig, "04-correlation-heatmap")


def _chart_response_by_attempt(events: pd.DataFrame) -> Path:
    d = events.copy()
    d["responded"] = d["outcome"].ne("no_answer").astype(int)
    d["attempt"] = d["attempt_number"].clip(upper=8)
    summary = d.groupby("attempt")["responded"].agg(["mean", "size"]).reset_index()
    fig, ax = plt.subplots(figsize=(8, 4.5))
    sns.barplot(summary, x="attempt", y="mean", ax=ax, color="#4C72B0")
    ax.set(
        title="Response rate by attempt number (capped at 8)",
        xlabel="attempt within the campaign",
        ylabel="share that responded",
    )
    for i, row in summary.iterrows():
        ax.text(i, row["mean"], f"n={int(row['size'])}", ha="center", va="bottom", fontsize=8)
    return _save(fig, "01-response-by-attempt")


def _chart_response_by_channel(events: pd.DataFrame) -> Path:
    d = events.copy()
    d["responded"] = d["outcome"].ne("no_answer").astype(int)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    sns.barplot(d, x="channel", y="responded", ax=ax)
    ax.set(title="Response rate by channel", ylabel="share that responded")
    return _save(fig, "02-response-by-channel")


def _chart_contact_volume(events: pd.DataFrame) -> Path:
    d = events.copy()
    d["month"] = pd.to_datetime(d["event_time"]).dt.to_period("M").astype(str)
    d["responded"] = d["outcome"].ne("no_answer").astype(int)
    summary = d.groupby("month")["responded"].agg(["size", "mean"]).reset_index()
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    sns.lineplot(summary, x="month", y="size", marker="o", ax=axes[0])
    axes[0].set(title="Contact volume and response rate over time", ylabel="contacts")
    sns.lineplot(summary, x="month", y="mean", marker="o", ax=axes[1], color="#DD8452")
    axes[1].set(ylabel="response rate", xlabel="month")
    axes[1].tick_params(axis="x", rotation=60)
    return _save(fig, "03-contact-volume")


def _chart_days_since_previous(events: pd.DataFrame) -> Path:
    d = events.loc[events["days_since_previous_contact"].notna()].copy()
    d["responded"] = d["outcome"].ne("no_answer").astype(int)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    if d.empty:
        ax.text(0.5, 0.5, "no repeat contacts in this dataset", ha="center")
    else:
        bins = pd.cut(d["days_since_previous_contact"], bins=[0, 3, 7, 14, 30, 999])
        summary = d.groupby(bins, observed=True)["responded"].mean().reset_index()
        summary["days_since_previous_contact"] = summary["days_since_previous_contact"].astype(str)
        sns.barplot(summary, x="days_since_previous_contact", y="responded", ax=ax)
    ax.set(
        title="Response rate by days since the previous contact",
        xlabel="days since previous contact",
        ylabel="share that responded",
    )
    return _save(fig, "04-days-since-previous-contact")


def _contact_strategy_report(tables: dict[str, pd.DataFrame]) -> Path:
    """Report for sources that carry contact attempts but no receivables.

    Bank Marketing is the case this exists for: the predictive tasks cannot run on it, but
    the contact-pressure question the active module has to answer -- how quickly repeated
    attempts stop paying off -- is exactly what it can answer, on real behavior.
    """
    events = tables["collection_events"]
    charts = [
        _chart_response_by_attempt(events),
        _chart_response_by_channel(events),
        _chart_contact_volume(events),
        _chart_days_since_previous(events),
    ]

    events = events.copy()
    events["responded"] = events["outcome"].ne("no_answer").astype(int)
    by_attempt = (
        events.assign(attempt=events["attempt_number"].clip(upper=6))
        .groupby("attempt")["responded"]
        .agg(contacts="size", response_rate="mean")
        .round(4)
    )
    marginal = by_attempt["response_rate"]
    decline = (
        f"{marginal.iloc[0]:.1%} on the first attempt down to {marginal.iloc[-1]:.1%} by the "
        f"sixth — a {1 - marginal.iloc[-1] / marginal.iloc[0]:.0%} fall"
        if len(marginal) > 1
        else "not enough attempts to measure"
    )

    parts = [
        f"# Exploratory data analysis — contact strategy (`{_SOURCE}`)\n",
        "> Generated by `collection eda`. This dataset carries contact attempts but no\n"
        "> receivables, so the predictive tasks do not run on it; what follows is about\n"
        "> contact pressure, which is what the active module needs.\n",
        "## Volume\n",
        pd.DataFrame(
            [{"table": n, "rows": len(df), "columns": df.shape[1]} for n, df in tables.items()]
        ).to_markdown(index=False),
        "\n## Outcomes\n",
        events["outcome"].value_counts().to_frame("contacts").to_markdown(),
        "\n## Diminishing returns from repeated contact\n",
        by_attempt.to_markdown(),
        f"\nResponse rate falls from {decline}. This is the empirical basis for a cooldown "
        "and an attempt cap in the rules engine, rather than escalating indefinitely.\n",
        "\n## Response rate by channel\n",
        events.groupby("channel")["responded"]
        .agg(contacts="size", response_rate="mean")
        .round(4)
        .to_markdown(),
        "\n## Charts\n",
        *[f"![{c.stem}](eda/{_SOURCE}/{c.name})\n" for c in charts],
    ]
    return _write(parts)


def _write(parts: list[str]) -> Path:
    destination = settings.dir_docs / f"eda-{_SOURCE}.md"
    destination.write_text("\n".join(parts), encoding="utf-8")
    return destination


def generate_report() -> Path:
    global _SOURCE
    settings.prepare_directories()
    _SOURCE = source_metadata()["source"]
    tables = load_interim()
    invoices, customers = tables["invoices"], tables["customers"]
    if invoices.empty:
        return _contact_strategy_report(tables)
    features = build_features("propensity")
    target = next(c for c in features.columns if c.startswith("target_"))

    charts = [
        _chart_delay(invoices),
        _chart_segment(invoices, customers),
        _chart_seasonality(invoices),
        _chart_correlation(features, target),
    ]

    positive_rate = float(features[target].mean())
    imbalance = (1 - positive_rate) / positive_rate if positive_rate else float("inf")
    missing = (
        features.isna()
        .mean()
        .sort_values(ascending=False)
        .head(10)
        .mul(100)
        .round(2)
        .to_frame("% missing")
    )
    p99 = invoices["amount"].quantile(0.99)

    parts = [
        f"# Exploratory data analysis (`{_SOURCE}`)\n",
        "> Generated by `collection eda`. Anonymized dataset in `data/interim/`.\n",
        "## Volume\n",
        pd.DataFrame(
            [{"table": n, "rows": len(df), "columns": df.shape[1]} for n, df in tables.items()]
        ).to_markdown(index=False),
        "\n## Receivable status\n",
        invoices["status"].value_counts().to_frame("receivables").to_markdown(),
        "\n## Descriptive statistics\n",
        invoices[["amount", "days_late"]].describe().round(2).to_markdown(),
        "\n## Target and class imbalance\n",
        f"- Target analysed: `{target}`\n"
        f"- Positive rate: **{positive_rate:.2%}**\n"
        f"- Negative:positive ratio: **{imbalance:.2f}:1**\n"
        f"- Eligible observations: **{len(features)}**\n",
        "\n## Missing values (top 10)\n",
        missing.to_markdown(),
        "\n## Outliers\n",
        f"- Receivables more than 180 days late: {(invoices['days_late'] > 180).sum()} "
        f"({(invoices['days_late'] > 180).mean():.2%}); capped in the charts and kept for "
        "modelling, since they are genuine write-off cases.\n"
        f"- Amounts above the 99th percentile ({p99:.2f}): "
        f"{(invoices['amount'] > p99).sum()} receivables.\n",
        "\n## Charts\n",
        *[f"![{c.stem}](eda/{_SOURCE}/{c.name})\n" for c in charts],
    ]
    return _write(parts)
