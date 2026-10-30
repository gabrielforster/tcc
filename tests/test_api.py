"""Scoring API.

The API is what the active module will call to decide who gets contacted, so the tests
here are about the contract: that the ranking is ordered, that the tuned threshold (not
0.5) decides, and that a malformed payload is rejected loudly rather than scored wrongly.
"""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.dummy import DummyClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer

from collection.api.app import ModelRegistry, create_app

FEATURES = ["amount", "hist_late_rate"]


def _select_features(frame):
    """Module-level so the pipeline can be pickled the way joblib.dump needs."""
    return frame[FEATURES].to_numpy()


class _RankingModel(ClassifierMixin, BaseEstimator):
    """Returns a probability proportional to `amount`, so ordering is predictable."""

    def fit(self, x, y=None):
        self.classes_ = np.array([0, 1])
        return self

    def predict_proba(self, x):
        amounts = np.asarray(x)[:, 0].astype(float)
        positive = amounts / 1000.0
        return np.column_stack([1 - positive, positive])


def _stub_bundle() -> dict:
    pipeline = Pipeline(
        [("preprocess", FunctionTransformer(_select_features)), ("model", _RankingModel())]
    )
    pipeline.fit(pd.DataFrame({"amount": [100.0, 900.0], "hist_late_rate": [0.1, 0.5]}), [0, 1])
    return {
        "pipeline": pipeline,
        "threshold": 0.30,
        "target": "target_payment_propensity",
        "task": "propensity",
        "source": "test",
        "champion": "ranking_stub",
        "features": FEATURES,
    }


@pytest.fixture
def client():
    registry = ModelRegistry(loader=lambda: {"propensity": _stub_bundle()})
    with TestClient(create_app(registry)) as test_client:
        yield test_client


def test_health_lists_the_loaded_models(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "models": ["propensity"]}


def test_model_info_exposes_the_threshold_and_feature_contract(client):
    body = client.get("/models/propensity").json()
    assert body["threshold"] == 0.30
    assert body["features"] == FEATURES
    assert body["champion"] == "ranking_stub"


def test_scoring_ranks_by_probability_with_one_as_the_first_to_contact(client):
    response = client.post(
        "/score",
        json={
            "task": "propensity",
            "receivables": [
                {"invoice_id": "A", "amount": 100.0, "hist_late_rate": 0.1},
                {"invoice_id": "B", "amount": 900.0, "hist_late_rate": 0.5},
                {"invoice_id": "C", "amount": 500.0, "hist_late_rate": 0.2},
            ],
        },
    )
    assert response.status_code == 200
    results = {r["invoice_id"]: r for r in response.json()["results"]}
    assert results["B"]["priority"] == 1
    assert results["C"]["priority"] == 2
    assert results["A"]["priority"] == 3
    assert results["B"]["probability"] > results["A"]["probability"]


def test_the_decision_uses_the_tuned_threshold_not_a_default_of_half(client):
    """Probability 0.4 is below 0.5 but above the model's tuned 0.30, so it is contacted."""
    response = client.post(
        "/score",
        json={"receivables": [{"invoice_id": "A", "amount": 400.0, "hist_late_rate": 0.1}]},
    )
    result = response.json()["results"][0]
    assert result["probability"] == pytest.approx(0.4)
    assert result["decision"] is True
    assert response.json()["threshold"] == 0.30


def test_missing_feature_columns_are_rejected(client):
    response = client.post("/score", json={"receivables": [{"invoice_id": "A", "amount": 100.0}]})
    assert response.status_code == 422
    assert "hist_late_rate" in response.json()["detail"]


def test_unknown_columns_are_rejected_rather_than_ignored(client):
    """A typo must not silently become a missing value and a wrong score."""
    response = client.post(
        "/score",
        json={
            "receivables": [
                {"invoice_id": "A", "amount": 100.0, "hist_late_rate": 0.1, "amout": 999}
            ]
        },
    )
    assert response.status_code == 422
    assert "amout" in response.json()["detail"]


def test_an_untrained_task_explains_what_to_run(client):
    response = client.post(
        "/score",
        json={"task": "default", "receivables": [{"amount": 1.0, "hist_late_rate": 0.1}]},
    )
    assert response.status_code == 404
    assert "collection train" in response.json()["detail"]


def test_an_empty_batch_is_rejected_by_validation(client):
    assert client.post("/score", json={"receivables": []}).status_code == 422


def test_passthrough_identifiers_are_optional(client):
    response = client.post(
        "/score", json={"receivables": [{"amount": 100.0, "hist_late_rate": 0.1}]}
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["invoice_id"] is None


def test_a_real_bundle_round_trips_through_the_registry():
    """Guards the bundle contract the training step writes."""
    pipeline = Pipeline(
        [
            ("preprocess", FunctionTransformer(_select_features)),
            ("model", DummyClassifier(strategy="prior")),
        ]
    )
    pipeline.fit(pd.DataFrame({"amount": [1.0, 2.0], "hist_late_rate": [0.0, 1.0]}), [0, 1])
    bundle = {
        "pipeline": pipeline,
        "threshold": 0.5,
        "target": "t",
        "task": "default",
        "source": "test",
        "champion": "dummy",
        "features": FEATURES,
    }
    registry = ModelRegistry(loader=lambda: {"default": bundle})
    with TestClient(create_app(registry)) as client:
        body = client.post(
            "/score",
            json={"task": "default", "receivables": [{"amount": 1.0, "hist_late_rate": 0.0}]},
        ).json()
    assert body["scored"] == 1
    assert 0.0 <= body["results"][0]["probability"] <= 1.0
    assert isinstance(pd.DataFrame(body["results"]), pd.DataFrame)


def test_an_unreachable_record_leaves_the_registry_empty_instead_of_failing(monkeypatch):
    from sqlalchemy.exc import OperationalError

    from collection.api import app as app_module

    def unreachable(*args, **kwargs):
        raise OperationalError("select 1", {}, Exception("connection refused"))

    monkeypatch.setattr(app_module, "unit_of_work", unreachable)
    registry = ModelRegistry()
    with TestClient(create_app(registry)) as client:
        assert client.get("/health").json() == {"status": "degraded", "models": []}
        response = client.get("/models/propensity")
    assert response.status_code == 503
    assert "connection refused" in response.json()["detail"]


def test_a_degraded_registry_recovers_on_the_next_request_after_the_outage(monkeypatch):
    from collection.api.app import Loaded

    outcomes = [
        Loaded(problems={"propensity": "OperationalError: connection refused"}),
        Loaded(bundles={"propensity": _stub_bundle()}),
    ]
    registry = ModelRegistry(loader=lambda: outcomes.pop(0))
    monkeypatch.setattr(ModelRegistry, "RETRY_SECONDS", 0.0)
    with TestClient(create_app(registry)) as client:
        assert client.get("/health").json()["status"] == "degraded"
        assert client.get("/models/propensity").status_code == 200
        assert client.get("/health").json() == {"status": "ok", "models": ["propensity"]}


def test_one_unreadable_artifact_does_not_discard_the_others(monkeypatch):
    from collection.api import app as app_module

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def load_bundle(session, task):
        if task == "default":
            raise AttributeError("incompatible pickle")
        return _stub_bundle()

    monkeypatch.setattr(app_module, "unit_of_work", lambda **kw: _Session())
    monkeypatch.setattr(app_module, "load_bundle", load_bundle)

    loaded = app_module.load_published()

    assert set(loaded.bundles) == {"propensity"}
    assert "incompatible pickle" in loaded.problems["default"]
