"""The models under comparison and how each one handles class imbalance.

The schedule asks for algorithms of increasing complexity, and for imbalance to be treated
either by resampling (SMOTE) or by weighting. Both are here as explicit, comparable
candidates rather than a single pre-chosen configuration, so the paper can report what the
comparison actually showed instead of asserting a choice.

Every candidate is a full pipeline: preprocessing is fitted inside each cross-validation
fold, never once over the whole training set, so fold statistics cannot leak.
"""

from dataclasses import dataclass, field
from typing import Any

from imblearn.over_sampling import SMOTE
from imblearn.pipeline import Pipeline as ImbPipeline
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from collection.features.build import Splits
from collection.features.preprocess import build_preprocessor

SEED = 42


@dataclass
class Candidate:
    name: str
    family: str
    estimator: Any
    imbalance: str  # "none" | "class_weight" | "smote"
    #: Distributions for the randomised search; empty means no tuning for this candidate.
    search_space: dict[str, list] = field(default_factory=dict)
    is_baseline: bool = False

    def build(self, preprocessor: ColumnTransformer) -> Pipeline:
        steps = [("preprocess", preprocessor)]
        if self.imbalance == "smote":
            # SMOTE has to run after encoding and only on training folds, which is exactly
            # what imblearn's pipeline guarantees and a plain sklearn one does not.
            steps.append(("smote", SMOTE(random_state=SEED)))
            steps.append(("model", self.estimator))
            return ImbPipeline(steps)
        steps.append(("model", self.estimator))
        return Pipeline(steps)


def _xgboost(scale_pos_weight: float | None) -> Any:
    from xgboost import XGBClassifier

    return XGBClassifier(
        n_estimators=300,
        learning_rate=0.08,
        max_depth=5,
        subsample=0.9,
        colsample_bytree=0.9,
        reg_lambda=1.0,
        eval_metric="aucpr",
        tree_method="hist",
        random_state=SEED,
        n_jobs=-1,
        **({"scale_pos_weight": scale_pos_weight} if scale_pos_weight else {}),
    )


def candidates(splits: Splits) -> list[Candidate]:
    """The comparison grid, with imbalance ratio taken from the training split."""
    y = splits.train[splits.target]
    positives = int(y.sum())
    negatives = int(len(y) - positives)
    ratio = negatives / max(positives, 1)

    return [
        Candidate(
            name="logistic_regression",
            family="logistic_regression",
            estimator=LogisticRegression(max_iter=2000, random_state=SEED),
            imbalance="none",
            is_baseline=True,
        ),
        Candidate(
            name="logistic_regression + class_weight",
            family="logistic_regression",
            estimator=LogisticRegression(max_iter=2000, class_weight="balanced", random_state=SEED),
            imbalance="class_weight",
        ),
        Candidate(
            name="random_forest + class_weight",
            family="random_forest",
            estimator=RandomForestClassifier(
                n_estimators=400,
                min_samples_leaf=2,
                class_weight="balanced_subsample",
                random_state=SEED,
                n_jobs=-1,
            ),
            imbalance="class_weight",
            search_space={
                "model__max_depth": [None, 8, 14, 20],
                "model__min_samples_leaf": [1, 2, 5, 10],
                "model__max_features": ["sqrt", 0.3, 0.5],
            },
        ),
        Candidate(
            name="random_forest + smote",
            family="random_forest",
            estimator=RandomForestClassifier(
                n_estimators=400, min_samples_leaf=2, random_state=SEED, n_jobs=-1
            ),
            imbalance="smote",
        ),
        Candidate(
            name="xgboost + scale_pos_weight",
            family="xgboost",
            estimator=_xgboost(scale_pos_weight=ratio),
            imbalance="class_weight",
            search_space={
                "model__max_depth": [3, 5, 7, 9],
                "model__learning_rate": [0.03, 0.08, 0.15],
                "model__subsample": [0.7, 0.9, 1.0],
                "model__min_child_weight": [1, 5, 10],
            },
        ),
        Candidate(
            name="xgboost + smote",
            family="xgboost",
            estimator=_xgboost(scale_pos_weight=None),
            imbalance="smote",
        ),
    ]


def build_pipeline(candidate: Candidate, splits: Splits) -> Pipeline:
    return candidate.build(build_preprocessor(splits))
