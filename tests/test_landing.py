"""The public entry point: landing page, disclaimer gate, demo pages and contact queue.

The landing page is what the poster's QR code opens, so these tests hold it to the
promises the project makes in public: the banner is on every page, no demo is reachable
before the disclaimer is acknowledged, old links still land somewhere, and the queue page
ranks with the API's own function and says so plainly when there is no model to rank with.
"""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sklearn.base import BaseEstimator, ClassifierMixin

from collection.agents.responsive import TemplateResponder
from collection.api import queue as queue_module
from collection.api.app import Loaded, ModelRegistry, create_app
from collection.api.chat import ChatService
from collection.api.pages import ACK_COOKIE, BANNER_TEXT
from collection.channels.web import ChatChannel
from collection.config import settings

FEATURES = ["amount", "hist_late_rate"]


class _ByLateRate(ClassifierMixin, BaseEstimator):
    """Pays less often the later the customer has been before: predictable and monotone."""

    def fit(self, x, y=None):
        self.classes_ = np.array([0, 1])
        return self

    def predict_proba(self, x):
        positive = 1.0 - np.clip(np.asarray(x, dtype=float)[:, 1], 0.0, 1.0)
        return np.column_stack([1 - positive, positive])


def _bundle() -> dict:
    return {
        "pipeline": _ByLateRate().fit(None),
        "threshold": 0.6,
        "target": "target_payment_propensity",
        "task": "propensity",
        "source": "test",
        "champion": "late_rate_stub",
        "features": FEATURES,
    }


def _client(index, registry: ModelRegistry) -> TestClient:
    channel = ChatChannel(retriever=index, responder=TemplateResponder())
    return TestClient(create_app(registry=registry, chat=ChatService(channel=channel)))


@pytest.fixture
def client(index):
    with _client(index, ModelRegistry(loader=lambda: {"propensity": _bundle()})) as http:
        yield http


@pytest.fixture
def acknowledged(client):
    client.cookies.set(ACK_COOKIE, "1")
    return client


# ------------------------------------------------------------------ landing and gate
def test_the_landing_page_shows_the_disclaimer_and_hides_the_demos(client):
    page = client.get("/")
    assert page.status_code == 200
    assert BANNER_TEXT in page.text
    assert 'action="/aviso"' in page.text
    # Absent, not hidden: the picker is not in the page until the notice is read.
    for path in ("/app/chat", "/app/call", "/app/fila"):
        assert f'href="{path}"' not in page.text
    assert "no-store" in page.headers["cache-control"]


def test_the_disclaimer_states_what_a_visitor_must_know(client):
    text = client.get("/").text
    assert "Católica de Santa Catarina" in text and "Portfólio" in text
    assert "sintéticos" in text
    # The retention window comes from the setting, not from a sentence that can go stale.
    assert f"até\n          {settings.transcript_retention_days} dias" in text
    assert "gravada e transcrita" in text


def test_acknowledging_sets_a_cookie_and_shows_the_picker(client):
    response = client.post("/aviso", data={"next": ""}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/#demos"
    cookie = response.headers["set-cookie"]
    assert f"{ACK_COOKIE}=1" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    page = client.get("/").text
    for path in ("/app/chat", "/app/call", "/app/fila"):
        assert f'href="{path}"' in page
    assert 'action="/aviso"' not in page


def test_the_picker_does_not_link_to_grafana_or_the_wiki(acknowledged):
    page = acknowledged.get("/").text.lower()
    assert "grafana" not in page and "wiki" not in page


def test_the_cookie_is_secure_behind_https(client):
    response = client.post(
        "/aviso", data={}, headers={"X-Forwarded-Proto": "https"}, follow_redirects=False
    )
    assert "Secure" in response.headers["set-cookie"]


@pytest.mark.parametrize("path", ["/app/chat", "/app/fila"])
def test_a_demo_page_sends_an_unacknowledged_visitor_to_the_disclaimer(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/?next={path}"
    landing = client.get(response.headers["location"]).text
    assert f'name="next" value="{path}"' in landing


def test_acknowledging_from_a_shared_link_returns_to_that_page(client):
    response = client.post("/aviso", data={"next": "/app/fila"}, follow_redirects=False)
    assert response.headers["location"] == "/app/fila"


@pytest.mark.parametrize("target", ["https://evil.test/", "//evil.test/app/x", "/docs"])
def test_the_return_address_cannot_leave_the_demo_pages(client, target):
    response = client.post("/aviso", data={"next": target}, follow_redirects=False)
    assert response.headers["location"] == "/#demos"
    assert "evil" not in client.get(f"/?next={target}").text


@pytest.mark.parametrize("path", ["/", "/app/chat", "/app/fila"])
def test_every_page_carries_the_banner(acknowledged, path):
    page = acknowledged.get(path)
    assert page.status_code == 200
    assert BANNER_TEXT in page.text


def test_the_old_chat_address_redirects_to_the_new_page(client):
    response = client.get("/chat/demo", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == "/app/chat"


def test_the_api_points_the_call_page_at_the_call_server(client, monkeypatch):
    """Only reached in local runs: in production Caddy routes `/app/call` elsewhere."""
    monkeypatch.setattr(settings, "call_public_url", "http://call.local:8100")
    response = client.get("/app/call", follow_redirects=False)
    assert response.headers["location"] == "http://call.local:8100/app/call"


def test_the_api_paths_are_unchanged(client):
    assert client.get("/health").status_code == 200
    assert client.get("/chat/widget.js").status_code == 200
    assert client.post("/chat/sessions", json={"token": None}).status_code in (200, 201)


# ------------------------------------------------------------------ contact queue
def test_the_queue_ranks_the_open_book_with_the_scoring_function(client):
    body = client.get("/queue").json()
    assert body["model"] == "late_rate_stub" and body["threshold"] == 0.6
    entries = body["entries"]
    assert entries and body["scored"] == len(entries)
    assert [e["priority"] for e in entries] == list(range(1, len(entries) + 1))
    probabilities = [e["probability"] for e in entries]
    assert probabilities == sorted(probabilities, reverse=True)
    assert all(e["decision"] == (e["probability"] >= 0.6) for e in entries)
    # The open book of the propensity task: due, still open, inside the 30-day window.
    assert all(1 <= e["days_late"] <= 30 for e in entries)
    assert all(e["invoice_id"].startswith("inv_") for e in entries), "pseudonyms, not raw ids"


def test_the_queue_agrees_with_post_score_on_the_same_receivables(client):
    """One ranking function behind both, so the page cannot drift from the API."""
    queue = client.get("/queue").json()
    _, features = queue_module.demo_book(pd.Timestamp(queue["as_of"]).date())
    rows = features[["invoice_id", *FEATURES]].to_dict(orient="records")
    scored = client.post("/score", json={"receivables": rows}).json()["results"]
    by_invoice = {r["invoice_id"]: (r["priority"], r["probability"]) for r in scored}
    assert {e["invoice_id"]: (e["priority"], e["probability"]) for e in queue["entries"]} == (
        by_invoice
    )


def test_the_queue_says_when_nothing_is_trained(index):
    with _client(index, ModelRegistry(loader=lambda: {})) as http:
        response = http.get("/queue")
    assert response.status_code == 404
    assert "collection train" in response.json()["detail"]


def test_the_queue_says_when_the_models_are_unreachable(index):
    registry = ModelRegistry(loader=lambda: Loaded({}, {"propensity": "OSError: bucket down"}))
    with _client(index, registry) as http:
        response = http.get("/queue")
    assert response.status_code == 503


def test_the_queue_page_handles_every_state(acknowledged):
    """The page renders the queue, and has a message for each way it can be missing."""
    page = acknowledged.get("/app/fila").text
    assert 'fetch("/queue"' in page
    assert "Carregando a fila" in page
    assert "Nenhum modelo treinado ainda" in page
    assert "indisponível no momento" in page
    assert "Tentar de novo" in page
