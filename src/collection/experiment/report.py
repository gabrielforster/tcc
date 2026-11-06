"""Turn replications into the tables of docs/experiment-results.md, and one chart.

The document has a hand-written part (what the numbers mean, and what they cannot mean)
and a generated part. Only the generated part is rewritten: it sits between two markers,
so re-running the experiment refreshes the tables without erasing the reading of them. A
reading that no longer matches its tables is then visible in a diff, which is the point.
"""

from datetime import date
from pathlib import Path

import numpy as np

from collection.experiment.metrics import Outcome, bootstrap, paired
from collection.experiment.run import CAPACITIES, SCENARIOS, Results

START = "<!-- generated:start -->"
END = "<!-- generated:end -->"
ARM_LABEL = {"none": "no contact", "manual": "manual", "semi": "semi-automated", "full": "system"}


def _cap(capacity: int | None) -> str:
    return "unlimited" if capacity is None else str(capacity)


def _values(series: list[Outcome], name: str) -> np.ndarray:
    return np.array([getattr(o, name) for o in series], dtype=float)


def _cost_per_thousand(series: list[Outcome], baseline: list[Outcome]) -> str:
    """Cost of each R$ 1,000 recovered within 30 days beyond what no contact recovers."""
    cost = _values(series, "cost").sum()
    gained = (
        _values(series, "recovered_30_amount") - _values(baseline, "recovered_30_amount")
    ).sum()
    if gained <= 0:
        return "n/a"
    return f"{1000 * cost / gained:.2f}"


def population_table(results: Results) -> list[str]:
    rows = results.populations

    def stat(values: list[float], digits: int) -> str:
        return bootstrap(np.array(values)).format(digits)

    return [
        "| quantity | mean [95% CI] over replications |",
        "|:--|:--|",
        f"| receivables in the cohort | {stat([r.receivables for r in rows], 1)} |",
        f"| at risk (unpaid on the due date, untreated) | {stat([r.at_risk for r in rows], 1)} |",
        f"| at-risk amount (R$) | {stat([r.at_risk_amount for r in rows], 0)} |",
        f"| opted out before the horizon | {stat([r.opted_out for r in rows], 1)} |",
        f"| ranker AUC, not paid within 30 days | {stat([r.auc_overdue for r in rows], 3)} |",
        f"| ranker AUC, more than 60 days late | {stat([r.auc_default for r in rows], 3)} |",
    ]


def arms_table(results: Results, scenario: str = "main") -> list[str]:
    baseline = results.series(scenario, "none", None)
    lines = [
        "| capacity/day | arm | recovered ≤ 30 days | recovered by horizon | days outstanding "
        "| contacts | cost (R$) | R$/contact | R$ per R$ 1,000 gained | violations |",
        "|--:|:--|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    rows = [(None, "none")] + [
        (capacity, arm)
        for capacity in CAPACITIES
        for arm in ("manual", "semi", "full")
        if not (arm == "manual" and capacity is None)
    ]
    for capacity, arm in rows:
        series = results.series(scenario, arm, capacity)
        cap = "—" if arm == "none" else _cap(capacity)
        contacts = _values(series, "contacts")
        cost = _values(series, "cost")
        per_contact = cost.sum() / contacts.sum() if contacts.sum() else 0.0
        lines.append(
            f"| {cap} | {ARM_LABEL[arm]} "
            f"| {bootstrap(_values(series, 'recovery_rate_30')).format(3)} "
            f"| {bootstrap(_values(series, 'recovery_rate')).format(3)} "
            f"| {bootstrap(_values(series, 'days_outstanding')).format(1)} "
            f"| {contacts.mean():.0f} | {cost.mean():.0f} | {per_contact:.2f} "
            f"| {'—' if arm == 'none' else _cost_per_thousand(series, baseline)} "
            f"| {int(_values(series, 'violations').sum())} |"
        )
    return lines


def differences_table(results: Results, scenario: str = "main") -> list[str]:
    lines = [
        "| capacity/day | comparison | Δ recovered ≤ 30 days (pp) | d_z "
        "| Δ days outstanding | Δ cost (R$) |",
        "|--:|:--|--:|--:|--:|--:|",
    ]
    for capacity in CAPACITIES:
        pairs = [("full", "semi"), ("full", "manual"), ("semi", "manual")]
        for a, b in pairs:
            if "manual" in (a, b) and capacity is None:
                continue
            sa, sb = results.series(scenario, a, capacity), results.series(scenario, b, capacity)
            rec = paired(_values(sa, "recovery_rate_30"), _values(sb, "recovery_rate_30"))
            days = paired(_values(sa, "days_outstanding"), _values(sb, "days_outstanding"))
            cost = paired(_values(sa, "cost"), _values(sb, "cost"))
            effect = "—" if rec.effect is None else f"{rec.effect:+.2f}"
            lines.append(
                f"| {_cap(capacity)} | {ARM_LABEL[a]} − {ARM_LABEL[b]} "
                f"| {rec.format(1, scale=100, signed=True)} | {effect} "
                f"| {days.format(1, signed=True)} | {cost.format(0, signed=True)} |"
            )
    return lines


def sensitivity_table(results: Results) -> list[str]:
    lines = [
        "| capacity/day | " + " | ".join(SCENARIOS) + " |",
        "|--:|" + "--:|" * len(SCENARIOS),
    ]
    for capacity in CAPACITIES:
        cells = []
        for scenario in SCENARIOS:
            full = results.series(scenario, "full", capacity)
            semi = results.series(scenario, "semi", capacity)
            diff = paired(_values(full, "recovery_rate_30"), _values(semi, "recovery_rate_30"))
            cells.append(diff.format(1, scale=100, signed=True))
        lines.append(f"| {_cap(capacity)} | " + " | ".join(cells) + " |")
    return lines


def chart(results: Results, path: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    finite = [c for c in CAPACITIES if c is not None]
    fig, ax = plt.subplots(figsize=(7, 4))
    for arm, colour in (("manual", "#8c8c8c"), ("semi", "#4c78a8"), ("full", "#e45756")):
        means, lows, highs = [], [], []
        for capacity in finite:
            estimate = bootstrap(_values(results.series("main", arm, capacity), "recovery_rate_30"))
            means.append(estimate.mean)
            lows.append(estimate.low)
            highs.append(estimate.high)
        ax.plot(finite, means, marker="o", color=colour, label=ARM_LABEL[arm])
        ax.fill_between(finite, lows, highs, color=colour, alpha=0.15)
    none = bootstrap(_values(results.series("main", "none", None), "recovery_rate_30")).mean
    ax.axhline(none, color="black", linestyle=":", linewidth=1, label="no contact")
    ax.set_xscale("log", base=2)
    ax.set_xticks(finite, [str(c) for c in finite])
    ax.set_xlabel("contacts per day")
    ax.set_ylabel("at-risk amount recovered within 30 days")
    ax.set_title("Recovery by daily contact capacity (95% CI)")
    ax.legend(frameon=False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def generated(results: Results, chart_name: str) -> str:
    total_violations = sum(
        int(_values(series, "violations").sum()) for series in results.outcomes.values()
    )
    runs = sum(len(series) for series in results.outcomes.values())
    last = results.first_seed + results.replications - 1
    lines = [
        START,
        "",
        f"Generated by `make experiment` on {date.today():%Y-%m-%d}: {results.replications} "
        f"replications (seeds {results.first_seed}–{last}), {runs} simulated runs. "
        "Intervals are 95% percentile bootstrap intervals over replications "
        "(10,000 resamples); differences are paired by seed.",
        "",
        "### The simulated book",
        "",
        *population_table(results),
        "",
        "### Arms by daily capacity (main scenario)",
        "",
        *arms_table(results),
        "",
        f"![Recovery by daily contact capacity](images/{chart_name})",
        "",
        "### Paired differences (main scenario)",
        "",
        *differences_table(results),
        "",
        "### Sensitivity: system − semi-automated, Δ recovered ≤ 30 days (pp)",
        "",
        *sensitivity_table(results),
        "",
        "### Compliance",
        "",
        f"{total_violations} violations found by the independent audit across all {runs} runs.",
        "",
        END,
    ]
    return "\n".join(lines)


def write(results: Results, path: Path, images: Path) -> Path:
    chart_name = "experiment-capacity.png"
    chart(results, images / chart_name)
    block = generated(results, chart_name)
    if path.exists() and START in (text := path.read_text(encoding="utf-8")) and END in text:
        before, rest = text.split(START, 1)
        after = rest.split(END, 1)[1]
        content = before + block + after
    else:
        content = "# Controlled experiment: results\n\n" + block + "\n"
    path.write_text(content, encoding="utf-8")
    return path
