"""Training and evaluation for the two predictive tasks.

Protocol, fixed before any result is read:

1. Candidates are cross-validated on the training split with `TimeSeriesSplit`. The
   schedule asks for k=5 cross-validation; plain k-fold would shuffle future rows into
   past folds, so the temporal variant is used and k stays at 5.
2. The two tunable families get a small randomised search over the same folds.
3. Every candidate is fitted on the training split and scored on validation. The champion
   is whichever maximises average precision there.
4. The champion is refitted on train + validation and scored **once** on the test split.
   Test is touched exactly once per task, at the end, and never informs a choice.

The decision threshold is tuned on validation too: the operational question is which
receivables enter the queue, so the threshold that maximises F1 on validation is carried
to test rather than an untested 0.5.
"""

import json
from dataclasses import asdict, dataclass, field
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit

from collection.config import settings
from collection.data.ingest import source_metadata
from collection.features.build import build_splits
from collection.models.candidates import SEED, Candidate, build_pipeline, candidates
from collection.models.metrics import SELECTION_METRIC, Scores, baseline_scores, score

CV_FOLDS = 5
SEARCH_ITERATIONS = 8


@dataclass
class CandidateResult:
    name: str
    family: str
    imbalance: str
    is_baseline: bool
    cv_mean: float
    cv_std: float
    validation: dict[str, float]
    tuned_params: dict[str, Any]


@dataclass
class TaskResult:
    task: str
    target: str
    source: str
    champion: str
    threshold: float
    candidates: list[CandidateResult]
    test: Scores
    test_baseline: dict[str, float]
    split_sizes: dict[str, int]
    feature_columns: list[str] = field(default_factory=list)

    def comparison_table(self) -> pd.DataFrame:
        rows = []
        for c in self.candidates:
            rows.append(
                {
                    "model": c.name + (" (baseline)" if c.is_baseline else ""),
                    "cv_average_precision": round(c.cv_mean, 4),
                    "cv_std": round(c.cv_std, 4),
                    **{k: round(v, 4) for k, v in c.validation.items()},
                }
            )
        return pd.DataFrame(rows).sort_values("average_precision", ascending=False)


def _features_and_target(frame: pd.DataFrame, target: str) -> tuple[pd.DataFrame, np.ndarray]:
    return frame.drop(columns=[target]), frame[target].to_numpy()


def _best_threshold(y_true: np.ndarray, y_proba: np.ndarray) -> float:
    """Threshold maximising F1, searched on validation only."""
    grid = np.linspace(0.05, 0.95, 91)
    scores = [score(y_true, y_proba, threshold=t).f1 for t in grid]
    return float(grid[int(np.argmax(scores))])


def _tune(candidate: Candidate, splits, x_train, y_train, cv) -> tuple[Any, dict[str, Any]]:
    pipeline = build_pipeline(candidate, splits)
    if not candidate.search_space:
        return pipeline, {}
    search = RandomizedSearchCV(
        pipeline,
        candidate.search_space,
        n_iter=SEARCH_ITERATIONS,
        scoring="average_precision",
        cv=cv,
        random_state=SEED,
        n_jobs=-1,
        refit=True,
    )
    search.fit(x_train, y_train)
    return search.best_estimator_, search.best_params_


def train_task(task: str = "propensity", tune: bool = True) -> TaskResult:
    splits = build_splits(task)
    x_train, y_train = _features_and_target(splits.train, splits.target)
    x_validation, y_validation = _features_and_target(splits.validation, splits.target)
    x_test, y_test = _features_and_target(splits.test, splits.target)

    cv = TimeSeriesSplit(n_splits=CV_FOLDS)
    results: list[CandidateResult] = []
    fitted: dict[str, Any] = {}

    for candidate in candidates(splits):
        if tune and candidate.search_space:
            estimator, params = _tune(candidate, splits, x_train, y_train, cv)
        else:
            estimator, params = build_pipeline(candidate, splits), {}

        cv_scores = _cross_validate(estimator, x_train, y_train, cv)
        estimator.fit(x_train, y_train)
        proba = estimator.predict_proba(x_validation)[:, 1]

        results.append(
            CandidateResult(
                name=candidate.name,
                family=candidate.family,
                imbalance=candidate.imbalance,
                is_baseline=candidate.is_baseline,
                cv_mean=float(np.mean(cv_scores)),
                cv_std=float(np.std(cv_scores)),
                validation=score(y_validation, proba).as_row(),
                tuned_params={k: _plain(v) for k, v in params.items()},
            )
        )
        fitted[candidate.name] = estimator

    champion = max(results, key=lambda r: r.validation[SELECTION_METRIC])
    champion_estimator = fitted[champion.name]
    threshold = _best_threshold(y_validation, champion_estimator.predict_proba(x_validation)[:, 1])

    # Refit on train + validation so the final model uses everything before the test period.
    x_full = pd.concat([x_train, x_validation])
    y_full = np.concatenate([y_train, y_validation])
    champion_estimator.fit(x_full, y_full)
    test_proba = champion_estimator.predict_proba(x_test)[:, 1]

    result = TaskResult(
        task=task,
        target=splits.target,
        source=source_metadata()["source"],
        champion=champion.name,
        threshold=threshold,
        candidates=results,
        test=score(y_test, test_proba, threshold=threshold),
        test_baseline=baseline_scores(y_test),
        split_sizes={
            "train": len(splits.train),
            "validation": len(splits.validation),
            "test": len(splits.test),
        },
        feature_columns=list(x_train.columns),
    )
    _persist(result, champion_estimator)
    return result


def _cross_validate(estimator, x, y, cv) -> list[float]:
    from sklearn.base import clone
    from sklearn.metrics import average_precision_score

    scores = []
    for train_index, test_index in cv.split(x):
        fold = clone(estimator)
        fold.fit(x.iloc[train_index], y[train_index])
        proba = fold.predict_proba(x.iloc[test_index])[:, 1]
        scores.append(float(average_precision_score(y[test_index], proba)))
    return scores


def _plain(value: Any) -> Any:
    return value.item() if hasattr(value, "item") else value


def _persist(result: TaskResult, estimator: Any) -> None:
    settings.prepare_directories()
    joblib.dump(
        {
            "pipeline": estimator,
            "threshold": result.threshold,
            "target": result.target,
            "task": result.task,
            "source": result.source,
            "champion": result.champion,
            # The serving layer validates incoming payloads against these.
            "features": result.feature_columns,
        },
        settings.dir_processed / f"model_{result.task}.joblib",
    )
    payload = asdict(result)
    payload["test"] = asdict(result.test)
    (settings.dir_processed / f"metrics_{result.task}.json").write_text(
        json.dumps(payload, indent=2, default=str)
    )


def evaluate_task(task: str = "propensity") -> dict:
    """Read back the metrics of the last training run."""
    path = settings.dir_processed / f"metrics_{task}.json"
    if not path.exists():
        raise FileNotFoundError(f"No metrics for '{task}'. Run `collection train` first.")
    return json.loads(path.read_text())
