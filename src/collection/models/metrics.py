"""Classification metrics for both predictive tasks.

Accuracy is reported because the schedule asks for it, but it is not what decides the
champion: with a 17% positive class, always predicting the majority already scores 83%.
Average precision (the area under the precision-recall curve) is the selection criterion,
because it measures exactly what the active module needs -- how well the ranking puts real
cases at the top of the queue -- and it does not flatter a model for getting the majority
class right.
"""

from dataclasses import dataclass, field

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

#: Metric used to pick the champion model.
SELECTION_METRIC = "average_precision"


@dataclass
class Scores:
    accuracy: float
    precision: float
    recall: float
    f1: float
    roc_auc: float
    average_precision: float
    brier: float
    positive_rate: float
    n: int
    confusion: list[list[int]] = field(default_factory=list)

    def as_row(self) -> dict[str, float]:
        return {
            "accuracy": self.accuracy,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "roc_auc": self.roc_auc,
            "average_precision": self.average_precision,
            "brier": self.brier,
        }


def score(y_true: np.ndarray, y_proba: np.ndarray, threshold: float = 0.5) -> Scores:
    y_pred = (y_proba >= threshold).astype(int)
    return Scores(
        accuracy=float(accuracy_score(y_true, y_pred)),
        precision=float(precision_score(y_true, y_pred, zero_division=0)),
        recall=float(recall_score(y_true, y_pred, zero_division=0)),
        f1=float(f1_score(y_true, y_pred, zero_division=0)),
        roc_auc=float(roc_auc_score(y_true, y_proba)),
        average_precision=float(average_precision_score(y_true, y_proba)),
        brier=float(brier_score_loss(y_true, y_proba)),
        positive_rate=float(np.mean(y_true)),
        n=int(len(y_true)),
        confusion=confusion_matrix(y_true, y_pred).tolist(),
    )


def baseline_scores(y_true: np.ndarray) -> dict[str, float]:
    """What a model has to beat: always predicting the majority class.

    Reported alongside every result so a high accuracy cannot be mistaken for a useful
    model.
    """
    majority = int(np.mean(y_true) >= 0.5)
    y_pred = np.full_like(y_true, majority)
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "average_precision": float(np.mean(y_true)),
        "roc_auc": 0.5,
    }
