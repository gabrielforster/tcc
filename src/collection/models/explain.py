"""Interpretability for the champion model.

A collection system makes decisions about people's debts, so "the model said so" is not an
acceptable answer — not for the operator deciding who to call, and not for the paper. Two
complementary views are produced:

* **Permutation importance**, computed on the test split. It works for every model family
  in the comparison and measures what the fitted pipeline actually relies on, rather than
  an internal split-count that favours high-cardinality features.
* **SHAP values**, which additionally give the direction of each effect and let a single
  decision be explained, one receivable at a time.

SHAP is computed with `TreeExplainer` for tree ensembles and the model-agnostic explainer
otherwise, on a sample of the test split, because the exact computation is expensive and
the ranking stabilises well before the full split is used.
"""

from dataclasses import dataclass
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import seaborn as sns  # noqa: E402
from sklearn.inspection import permutation_importance  # noqa: E402

from collection.config import settings  # noqa: E402

sns.set_theme(style="whitegrid", palette="deep")

SHAP_SAMPLE = 500
PERMUTATION_REPEATS = 5
SEED = 42


@dataclass
class Explanation:
    task: str
    source: str
    permutation: pd.DataFrame
    shap_importance: pd.DataFrame | None
    charts: list[Path]

    def top_features(self, n: int = 10) -> list[str]:
        return self.permutation.head(n)["feature"].tolist()


def _feature_names(pipeline) -> list[str]:
    """Column names after preprocessing, so SHAP values can be labelled."""
    preprocessor = pipeline.named_steps["preprocess"]
    try:
        return list(preprocessor.get_feature_names_out())
    except Exception:  # pragma: no cover - depends on the transformer set
        return []


def permutation_table(pipeline, x: pd.DataFrame, y: np.ndarray) -> pd.DataFrame:
    """Drop in average precision when a column is shuffled. Higher means more relied upon."""
    result = permutation_importance(
        pipeline,
        x,
        y,
        scoring="average_precision",
        n_repeats=PERMUTATION_REPEATS,
        random_state=SEED,
        n_jobs=-1,
    )
    return (
        pd.DataFrame(
            {
                "feature": x.columns,
                "importance": result.importances_mean,
                "std": result.importances_std,
            }
        )
        .sort_values("importance", ascending=False)
        .reset_index(drop=True)
    )


def shap_table(pipeline, x: pd.DataFrame) -> tuple[pd.DataFrame | None, np.ndarray | None]:
    """Mean absolute SHAP value per encoded feature, or None if SHAP is unavailable."""
    try:
        import shap
    except ImportError:  # pragma: no cover - shap lives in the optional ml extra
        return None, None

    sample = x.sample(min(SHAP_SAMPLE, len(x)), random_state=SEED)
    transformed = pipeline.named_steps["preprocess"].transform(sample)
    model = pipeline.named_steps["model"]
    names = _feature_names(pipeline)

    try:
        explainer = shap.TreeExplainer(model)
        values = explainer.shap_values(transformed)
    except Exception:
        # Linear and any other non-tree model.
        explainer = shap.Explainer(model, transformed)
        values = explainer(transformed).values

    values = np.asarray(values)
    if values.ndim == 3:
        # Some explainers return one matrix per class; the positive class is the one we act on.
        values = values[:, :, -1] if values.shape[-1] == 2 else values[..., 0]

    importance = np.abs(values).mean(axis=0)
    if names and len(names) == len(importance):
        frame = pd.DataFrame({"feature": names, "mean_abs_shap": importance})
    else:  # pragma: no cover - only if the transformer cannot name its output
        frame = pd.DataFrame(
            {"feature": [f"f{i}" for i in range(len(importance))], "mean_abs_shap": importance}
        )
    return frame.sort_values("mean_abs_shap", ascending=False).reset_index(drop=True), values


def _chart_permutation(table: pd.DataFrame, source: str, task: str) -> Path:
    top = table.head(15).iloc[::-1]
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.barh(top["feature"], top["importance"], xerr=top["std"], color="#4C72B0")
    ax.set(
        title="Permutation importance (drop in average precision)",
        xlabel="decrease in average precision when shuffled",
    )
    return _save(fig, source, f"{task}-permutation-importance")


def _chart_shap(table: pd.DataFrame, source: str, task: str) -> Path:
    top = table.head(15).iloc[::-1]
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.barh(top["feature"], top["mean_abs_shap"], color="#DD8452")
    ax.set(title="Mean absolute SHAP value", xlabel="mean |SHAP| on the positive class")
    return _save(fig, source, f"{task}-shap-importance")


def _save(fig, source: str, name: str) -> Path:
    destination = settings.dir_docs / "models" / source / f"{name}.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(destination, dpi=150)
    plt.close(fig)
    return destination


def explain(pipeline, x: pd.DataFrame, y: np.ndarray, task: str, source: str) -> Explanation:
    permutation = permutation_table(pipeline, x, y)
    shap_importance, _ = shap_table(pipeline, x)

    charts = [_chart_permutation(permutation, source, task)]
    if shap_importance is not None:
        charts.append(_chart_shap(shap_importance, source, task))

    return Explanation(
        task=task,
        source=source,
        permutation=permutation,
        shap_importance=shap_importance,
        charts=charts,
    )


def write_report(explanation: Explanation) -> Path:
    parts = [
        f"# Interpretability — {explanation.task} (`{explanation.source}`)\n",
        "> Generated by `collection explain`. Permutation importance is computed on the\n"
        "> test split; SHAP values on a sample of it.\n",
        "## Permutation importance\n",
        "How much average precision the champion loses when a single column is shuffled.\n"
        "A feature near zero is one the model does not actually use.\n",
        explanation.permutation.head(20).round(5).to_markdown(index=False),
    ]
    if explanation.shap_importance is not None:
        parts += [
            "\n## SHAP\n",
            "Mean absolute SHAP value per encoded feature, on the positive class.\n",
            explanation.shap_importance.head(20).round(5).to_markdown(index=False),
        ]
    else:
        parts.append("\n## SHAP\n\nNot computed: the `ml` extra is not installed.\n")

    parts.append("\n## Charts\n")
    parts += [f"![{c.stem}](models/{explanation.source}/{c.name})\n" for c in explanation.charts]

    destination = settings.dir_docs / f"interpretability-{explanation.source}-{explanation.task}.md"
    destination.write_text("\n".join(parts), encoding="utf-8")
    return destination
