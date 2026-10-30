"""The test that matters most here is the temporal-leakage one: if it starts failing,
block II metrics turn optimistic and the whole experiment loses its value.
"""

import joblib
import pytest

from collection.data.anonymize import anonymize
from collection.features.build import build_features, build_splits
from collection.features.preprocess import fit_and_save


@pytest.fixture(scope="module")
def tables(dataset):
    """The anonymized frames, in memory: building features needs no directory."""
    return anonymize(dataset)


@pytest.mark.parametrize("task", ["propensity", "default"])
def test_features_have_a_binary_target_and_one_row_per_receivable(task, tables):
    df = build_features(task, tables)
    target = next(c for c in df.columns if c.startswith("target_"))
    assert df["invoice_id"].is_unique
    assert set(df[target].unique()) <= {0, 1}
    assert 0 < df[target].mean() < 1


@pytest.mark.parametrize("task", ["propensity", "default"])
def test_no_feature_carries_outcome_information(task, tables):
    df = build_features(task, tables)
    forbidden = {"payment_date", "days_late", "status", "was_late", "defaulted"}
    assert not forbidden & set(df.columns)


def test_split_is_chronological_and_does_not_overlap(tables):
    s = build_splits("propensity", tables)
    assert s.train["reference_date"].max() <= s.validation["reference_date"].min()
    assert s.validation["reference_date"].max() <= s.test["reference_date"].min()
    assert len(s.train) + len(s.validation) + len(s.test) == len(
        build_features("propensity", tables)
    )


def test_a_customers_first_receivable_has_empty_history(tables):
    df = build_features("propensity", tables).sort_values("reference_date")
    first = df.groupby("customer_id").head(1)
    assert (first["hist_invoice_count"] == 0).all()
    assert (first["hist_avg_days_late"] == 0).all()


def test_contact_history_never_counts_future_events(dataset, tables):
    df = build_features("propensity", tables)
    assert (df["hist_contact_count"] >= 0).all()
    assert df["hist_contact_count"].max() <= len(dataset.events)


def test_fitted_preprocessor_transforms_validation_and_test(tables, tmp_path):
    s = build_splits("propensity", tables)
    fit_and_save(s, tmp_path)
    preprocessor = joblib.load(tmp_path / "preprocessor_propensity.joblib")
    assert preprocessor.transform(s.validation).shape[0] == len(s.validation)
    assert preprocessor.transform(s.test).shape[1] == preprocessor.transform(s.train).shape[1]
