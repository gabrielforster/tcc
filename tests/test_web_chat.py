"""The web chat channel: identity, sessions, limits and the HTTP surface.

The widget is a public endpoint on a page this project does not control, so the tests that
matter most are the ones about what a hostile or careless caller can do: forge a token,
claim someone else's customer id, replay an expired session, flood the endpoint. The happy
path is one test; the boundaries are the rest.

The other theme is that the channel must not become a second way into the agent. Opt-out,
priority and the audit trail live in the orchestrator, and a chat that bypassed it would
pass its own tests while breaking the compliance story. `test_a_message_goes_through_the
_orchestrator` is the guard on that.
"""

import time

import pytest
from fastapi.testclient import TestClient

from collection.agents.responsive import TemplateResponder
from collection.api.app import ModelRegistry, create_app
from collection.api.chat import ChatService
from collection.channels import session as session_module
from collection.channels.session import (
    ANONYMOUS_PREFIX,
    MAX_MESSAGE_CHARS,
    RATE_LIMIT_PER_MINUTE,
    ConversationEnded,
    RateLimited,
    SessionNotFound,
    SessionStore,
    TokenError,
    clean_message,
    issue_token,
    verify_token,
)
from collection.channels.web import GREETING, WebChatChannel
from collection.config import settings
from collection.rag.documents import load_directory
from collection.rag.index import VectorIndex


@pytest.fixture(scope="module")
def index():
    return VectorIndex().add_documents(load_directory(settings.dir_knowledge)).build()


@pytest.fixture
def channel(index):
    return WebChatChannel(index=index, responder=TemplateResponder())


@pytest.fixture
def client(channel):
    app = create_app(registry=ModelRegistry(), chat=ChatService(channel=channel))
    with TestClient(app) as test_client:
        yield test_client


def open_session(client, token=None):
    response = client.post("/chat/sessions", json={"token": token})
    assert response.status_code == 201
    return response.json()


def say(client, session_id, text):
    return client.post(f"/chat/sessions/{session_id}/messages", json={"text": text})


# -------------------------------------------------------------------- identity
def test_a_token_round_trips():
    assert verify_token(issue_token("cus_42")) == "cus_42"


def test_a_tampered_token_is_rejected():
    token = issue_token("cus_42")
    forged = token.replace("cus_42", "cus_99", 1)
    with pytest.raises(TokenError, match="Bad signature"):
        verify_token(forged)


def test_a_token_signed_with_another_secret_is_rejected(monkeypatch):
    token = issue_token("cus_42")
    monkeypatch.setattr(settings, "anonymization_salt", "a-different-secret")
    with pytest.raises(TokenError, match="Bad signature"):
        verify_token(token)


def test_an_expired_token_is_rejected():
    with pytest.raises(TokenError, match="expired"):
        verify_token(issue_token("cus_42", ttl_seconds=-1))


@pytest.mark.parametrize("token", ["", "garbage", "a.b", "a.b.c.d", "cus.notanumber.sig"])
def test_malformed_tokens_do_not_crash(token):
    with pytest.raises(TokenError):
        verify_token(token)


def test_a_session_without_a_token_is_anonymous(channel):
    session, _ = channel.open()
    assert not session.identified
    assert session.customer_id.startswith(ANONYMOUS_PREFIX)


def test_two_anonymous_sessions_do_not_share_a_customer_id(channel):
    first, _ = channel.open()
    second, _ = channel.open()
    assert first.customer_id != second.customer_id


def test_a_valid_token_identifies_the_session(channel):
    session, _ = channel.open(issue_token("cus_7"))
    assert session.identified
    assert session.customer_id == "cus_7"


def test_the_browser_cannot_name_a_customer_without_a_token(client):
    # The only identity input the endpoint accepts is a signed token. A caller sending a
    # customer id has nowhere to put it, and a forged token is refused.
    claimed = client.post("/chat/sessions", json={"customer_id": "cus_7"})
    assert claimed.json()["identified"] is False

    forged = client.post("/chat/sessions", json={"token": "cus_7.99999999999.deadbeef"})
    assert forged.status_code == 401


# -------------------------------------------------------------------- sessions
def test_a_session_opens_with_a_greeting_that_says_it_is_automated(client):
    body = open_session(client)
    assert body["session_id"]
    assert body["greeting"] == GREETING
    assert "automático" in body["greeting"]


def test_an_unknown_session_is_not_found(client):
    assert say(client, "nope", "oi").status_code == 404


def test_an_expired_session_is_not_found(index):
    store = SessionStore()
    channel = WebChatChannel(index=index, store=store, responder=TemplateResponder())
    session, _ = channel.open()
    session.last_seen_at = time.time() - session_module.SESSION_TTL_SECONDS - 1
    with pytest.raises(SessionNotFound, match="expired"):
        channel.send(session.session_id, "oi")


def test_a_closed_session_is_gone(client):
    body = open_session(client)
    assert client.delete(f"/chat/sessions/{body['session_id']}").status_code == 204
    assert say(client, body["session_id"], "oi").status_code == 404


def test_a_long_conversation_is_handed_to_a_person(channel):
    session, _ = channel.open()
    for _ in range(session_module.MAX_TURNS):
        session.record("customer", "oi")
    with pytest.raises(ConversationEnded, match="transferred"):
        channel.send(session.session_id, "mais uma")


def test_expired_sessions_are_swept(index):
    store = SessionStore()
    stale, _ = WebChatChannel(index=index, store=store).open()
    stale.last_seen_at = time.time() - session_module.SESSION_TTL_SECONDS - 1
    store.open()  # a write sweeps
    assert len(store) == 1


# ---------------------------------------------------------------------- limits
def test_an_empty_message_is_refused(client):
    body = open_session(client)
    assert say(client, body["session_id"], "   ").status_code == 422


def test_an_overlong_message_is_refused(client):
    body = open_session(client)
    assert say(client, body["session_id"], "a" * (MAX_MESSAGE_CHARS + 1)).status_code == 422


def test_whitespace_is_collapsed():
    assert clean_message("  posso   parcelar?\n\n") == "posso parcelar?"


def test_flooding_one_session_is_rate_limited(channel):
    session, _ = channel.open()
    for _ in range(RATE_LIMIT_PER_MINUTE):
        session.check_rate()
    with pytest.raises(RateLimited):
        session.check_rate()


def test_the_rate_limit_window_slides(channel):
    session, _ = channel.open()
    past = time.time() - 61
    for _ in range(RATE_LIMIT_PER_MINUTE):
        session.check_rate(now=past)
    session.check_rate()  # the old hits have aged out


def test_the_rate_limit_is_per_session(channel):
    first, _ = channel.open()
    second, _ = channel.open()
    for _ in range(RATE_LIMIT_PER_MINUTE):
        first.check_rate()
    second.check_rate()


# -------------------------------------------------------------------- answering
def test_a_question_gets_a_grounded_answer(client):
    body = open_session(client)
    reply = say(client, body["session_id"], "quais sao os juros e a multa por atraso?").json()
    assert reply["text"]
    assert reply["citations"]
    assert not reply["escalated"]
    assert reply["intent"]


def test_a_message_goes_through_the_orchestrator(client):
    """The channel must not be a second way into the agent.

    Opt-out, priority and the audit trail live in the orchestrator; a chat that called the
    agent directly would pass every other test here and break all three.
    """
    body = open_session(client)
    reply = say(client, body["session_id"], "quanto eu to devendo?").json()
    assert reply["events"], "no events produced: the orchestrator was bypassed"
    assert "reply_sent" in reply["events"]


def test_a_dispute_is_escalated_and_closes_the_thread(client):
    body = open_session(client)
    reply = say(client, body["session_id"], "esse valor ta errado, nao e isso que eu devo").json()
    assert reply["escalated"]
    assert reply["escalation_reason"] == "dispute"
    assert "escalated_to_human" in reply["events"]


def test_an_opt_out_is_recorded_on_the_channel(channel):
    session, _ = channel.open(issue_token("cus_optout"))
    reply = channel.send(session.session_id, "parem de me mandar mensagem")
    assert reply.opted_out
    assert "cus_optout" in channel.opted_out


def test_an_opted_out_customer_is_still_answered_when_they_write(channel):
    # Opt-out stops outbound collection; it does not refuse service to someone who
    # reached out. The orchestrator encodes this and the channel inherits it.
    session, _ = channel.open(issue_token("cus_optout2"))
    channel.send(session.session_id, "parem de me mandar mensagem")
    again, _ = channel.open(issue_token("cus_optout2"))
    reply = channel.send(again.session_id, "quais sao os juros e a multa por atraso?")
    assert reply.text
    assert not reply.escalated


def test_the_transcript_records_both_sides(client):
    body = open_session(client)
    say(client, body["session_id"], "quanto eu to devendo?")
    transcript = client.get(f"/chat/sessions/{body['session_id']}").json()
    roles = [turn["role"] for turn in transcript["turns"]]
    assert roles == ["agent", "customer", "agent"], "greeting, question, answer"
    assert transcript["identified"] is False


def test_the_unclear_counter_survives_across_messages(channel):
    """One agent across sessions, or the three-strikes handover could never fire."""
    session, _ = channel.open(issue_token("cus_unclear"))
    replies = [channel.send(session.session_id, "asdfgh qwerty zxcvb") for _ in range(3)]
    assert replies[-1].escalated
    assert replies[-1].escalation_reason == "unclear_repeatedly"


# ------------------------------------------------------------------ the widget
def test_the_widget_is_served_as_javascript(client):
    response = client.get("/chat/widget.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_the_widget_renders_replies_as_text_not_html():
    """An injected script tag on the host's page is the failure this prevents."""
    source = (settings.dir_knowledge.parent / "src/collection/web/widget.js").read_text()
    assert "createTextNode" in source
    assert "innerHTML = body" not in source
    assert "attachShadow" in source


def test_the_demo_page_embeds_the_widget(client):
    page = client.get("/chat/demo")
    assert page.status_code == 200
    assert "/chat/widget.js" in page.text


def test_cross_origin_requests_are_allowed(client):
    response = client.post(
        "/chat/sessions", json={"token": None}, headers={"Origin": "https://example.test"}
    )
    assert response.headers.get("access-control-allow-origin")


def test_the_scoring_api_still_works(client):
    assert client.get("/health").status_code == 200
