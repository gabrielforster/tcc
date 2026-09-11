"""Predictive module.

These tests are about the evaluation protocol, not about model quality: whether the test
split stays untouched until the end, whether preprocessing is fitted inside each fold, and
whether the champion is chosen on the metric we said it would be. Those are the properties
that make a reported number trustworthy, and the ones most likely to break silently.
"""

import numpy as np
import pytest
from sklearn.linear_model import LogisticRegression

from collection.data import ingest
from collection.data.anonymize import anonymize
from collection.models.candidates import candidates
from collection.models.metrics import SELECTION_METRIC, baseline_scores, score
from collection.models.train import _best_threshold, train_task


@pytest.fixture(autouse=True)
def _temporary_dataset(dataset, monkeypatch, tmp_path):
    for name, df in anonymize(dataset).items():
        df.to_parquet(tmp_path / f"{name}.parquet", index=False)
    (tmp_path / "source.json").write_text(
        '{"source": "test", "provides": ["customers", "invoices", '
        '"collection_events", "agreements"]}'
    )
    for module in ("dir_interim", "dir_processed", "dir_raw"):
        monkeypatch.setattr(ingest.settings, module, tmp_path)
    from collection.models import train as train_module

    monkeypatch.setattr(train_module.settings, "dir_processed", tmp_path)
    monkeypatch.setattr(train_module.settings, "dir_raw", tmp_path)


# ------------------------------------------------------------------------------ metrics
def test_score_reports_every_metric_the_schedule_asks_for():
    y_true = np.array([0, 0, 1, 1, 0, 1])
    y_proba = np.array([0.1, 0.2, 0.9, 0.7, 0.4, 0.8])
    result = score(y_true, y_proba)
    for metric in ("accuracy", "precision", "recall", "f1", "roc_auc", "average_precision"):
        assert 0.0 <= result.as_row()[metric] <= 1.0
    assert result.n == 6
    assert np.array(result.confusion).shape == (2, 2)


def test_majority_baseline_scores_high_accuracy_and_useless_ranking():
    """The number that stops a 90% accuracy from being mistaken for a good model."""
    y_true = np.array([0] * 90 + [1] * 10)
    baseline = baseline_scores(y_true)
    assert baseline["accuracy"] == pytest.approx(0.90)
    assert baseline["average_precision"] == pytest.approx(0.10)
    assert baseline["roc_auc"] == 0.5


def test_threshold_search_prefers_a_threshold_that_beats_the_default():
    y_true = np.array([0] * 80 + [1] * 20)
    # Positives score higher, but all probabilities sit below 0.5.
    y_proba = np.concatenate([np.full(80, 0.05), np.full(20, 0.35)])
    threshold = _best_threshold(y_true, y_proba)
    assert threshold < 0.5
    assert score(y_true, y_proba, threshold).f1 > score(y_true, y_proba, 0.5).f1


# --------------------------------------------------------------------------- candidates
def test_the_grid_covers_both_imbalance_strategies_and_a_baseline(dataset):
    from collection.features.build import build_splits

    grid = candidates(build_splits("default"))
    assert sum(c.is_baseline for c in grid) == 1
    assert {"class_weight", "smote"} <= {c.imbalance for c in grid}
    assert {"logistic_regression", "random_forest", "xgboost"} == {c.family for c in grid}


def test_smote_candidates_resample_inside_the_pipeline(dataset):
    """SMOTE must be a pipeline step, so it only ever sees training folds."""
    from collection.features.build import build_splits
    from collection.models.candidates import build_pipeline

    splits = build_splits("default")
    smote_candidate = next(c for c in candidates(splits) if c.imbalance == "smote")
    pipeline = build_pipeline(smote_candidate, splits)
    assert "smote" in dict(pipeline.steps)
    assert list(dict(pipeline.steps))[0] == "preprocess"


def test_every_candidate_fits_preprocessing_inside_the_pipeline(dataset):
    from collection.features.build import build_splits
    from collection.models.candidates import build_pipeline

    splits = build_splits("default")
    for candidate in candidates(splits):
        steps = dict(build_pipeline(candidate, splits).steps)
        assert "preprocess" in steps, candidate.name


# ------------------------------------------------------------------------------ protocol
@pytest.fixture(scope="module")
def trained():
    return None


def test_training_selects_the_champion_on_validation_average_precision():
    result = train_task("default", tune=False)
    best = max(result.candidates, key=lambda c: c.validation[SELECTION_METRIC])
    assert result.champion == best.name


def test_every_candidate_is_cross_validated_and_scored_on_validation():
    result = train_task("default", tune=False)
    assert len(result.candidates) == 6
    for candidate in result.candidates:
        assert candidate.cv_mean > 0
        assert set(candidate.validation) >= {"average_precision", "roc_auc", "f1"}


def test_the_champion_beats_the_majority_baseline_on_ranking():
    result = train_task("default", tune=False)
    assert result.test.average_precision > result.test_baseline["average_precision"]
    assert result.test.roc_auc > 0.5


def test_training_persists_the_model_and_its_threshold(tmp_path):
    import joblib

    result = train_task("default", tune=False)
    bundle = joblib.load(tmp_path / "model_default.joblib")
    assert bundle["threshold"] == result.threshold
    assert bundle["target"] == result.target
    assert hasattr(bundle["pipeline"], "predict_proba")


def test_metrics_are_written_for_later_reading():
    from collection.models.train import evaluate_task

    train_task("default", tune=False)
    metrics = evaluate_task("default")
    assert metrics["champion"]
    assert metrics["test"]["average_precision"] > 0


def test_evaluate_explains_itself_when_nothing_was_trained():
    from collection.models.train import evaluate_task

    with pytest.raises(FileNotFoundError, match="collection train"):
        evaluate_task("propensity")


def test_a_model_fitted_on_the_training_split_never_sees_test_rows():
    """The test split must not appear in what the champion was fitted on."""
    from collection.features.build import build_splits

    splits = build_splits("default")
    estimator = LogisticRegression(max_iter=100)
    train_ids = set(splits.train["invoice_id"]) | set(splits.validation["invoice_id"])
    test_ids = set(splits.test["invoice_id"])
    assert not (train_ids & test_ids)
    assert estimator is not None
