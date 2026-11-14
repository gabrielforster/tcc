"""The command line, exercised as a caller rather than as an import.

Every command imports its dependencies inside its own body, so `--help` and a package-wide
import smoke both pass while a command's first statement is a stale import. That is how
`serve` came to raise `ImportError` on every invocation after the API was rewritten around
the model registry. These tests run the command bodies -- `serve` with uvicorn stubbed, so
nothing binds a port -- which is the cheapest thing that would have caught it.
"""

import pytest
import uvicorn
from alembic import command
from sqlalchemy import make_url
from typer.testing import CliRunner

from collection.api.app import Loaded
from collection.cli import app
from collection.config import settings

runner = CliRunner()


@pytest.fixture
def served(monkeypatch):
    """Record what `serve` would have started, instead of starting it."""
    started: list[dict] = []
    monkeypatch.setattr(
        uvicorn, "run", lambda target, **kwargs: started.append({"target": target, **kwargs})
    )
    return started


def _published(monkeypatch, loaded: Loaded) -> None:
    monkeypatch.setattr("collection.api.app.load_published", lambda: loaded)


def test_serve_runs_its_body_and_starts_the_server(monkeypatch, served):
    _published(monkeypatch, Loaded(bundles={"propensity": {}, "default": {}}))
    monkeypatch.delenv("COLLECTION_HOST", raising=False)

    result = runner.invoke(app, ["serve", "--port", "8099"])

    assert result.exit_code == 0, result.output + str(result.exception)
    assert "propensity" in result.output
    assert served == [
        {"target": "collection.api.app:app", "host": "127.0.0.1", "port": 8099, "reload": False}
    ]


def test_serve_binds_where_the_container_says(monkeypatch, served):
    # The image sets COLLECTION_HOST=0.0.0.0; loopback inside a container is unreachable.
    _published(monkeypatch, Loaded(bundles={"propensity": {}}))
    monkeypatch.setenv("COLLECTION_HOST", "0.0.0.0")

    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0, result.output + str(result.exception)
    assert served[0]["host"] == "0.0.0.0"


def test_serve_reports_an_outage_as_an_outage_not_as_nothing_trained(monkeypatch, served):
    _published(monkeypatch, Loaded(problems={"propensity": "OperationalError: no server"}))

    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0, result.output + str(result.exception)
    assert "503" in result.output
    assert "no trained models" not in result.output
    assert served


def test_serve_reports_an_empty_registry_as_nothing_trained(monkeypatch, served):
    _published(monkeypatch, Loaded())

    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0, result.output + str(result.exception)
    assert "no trained models" in result.output
    assert served


@pytest.fixture
def downgrades(monkeypatch):
    """Record what `db downgrade` would have rolled back, instead of rolling it back."""
    called: list[str] = []
    monkeypatch.setattr(command, "downgrade", lambda config, revision: called.append(revision))
    return called


def test_db_downgrade_does_nothing_without_an_answer(downgrades):
    """The command drops tables on the development database; it must not do that on a typo."""
    result = runner.invoke(app, ["db", "downgrade"], input="n\n")

    assert result.exit_code != 0
    assert downgrades == []


def test_db_downgrade_names_the_database_it_would_drop_from(downgrades):
    result = runner.invoke(app, ["db", "downgrade", "base"], input="n\n")

    assert make_url(settings.database_url).database in result.output
    assert downgrades == []


def test_db_downgrade_runs_when_the_confirmation_is_answered(downgrades):
    result = runner.invoke(app, ["db", "downgrade"], input="y\n")

    assert result.exit_code == 0, result.output + str(result.exception)
    assert downgrades == ["-1"]


def test_db_downgrade_takes_yes_in_advance(downgrades):
    result = runner.invoke(app, ["db", "downgrade", "base", "--yes"])

    assert result.exit_code == 0, result.output + str(result.exception)
    assert downgrades == ["base"]


@pytest.fixture
def swept(db, monkeypatch):
    """`conversations sweep` against the test transaction instead of its own connection.

    The command opens a unit of work of its own, which cannot see rows a rolled-back test
    transaction holds. Handing it this scope is what lets the body -- the window
    arithmetic, the dry run, the table -- run over rows a test can set up.
    """
    from contextlib import contextmanager

    from sqlalchemy.orm import Session

    from collection.db.models import Tenant, TenantState
    from collection.db.repository import TenantScope

    tenant = Tenant(name="swept", slug="swept", state=TenantState.ACTIVE)
    db.add(tenant)
    db.flush()
    scope = TenantScope(
        Session(bind=db.connection(), join_transaction_mode="create_savepoint"), tenant.id
    )

    @contextmanager
    def _scope(slug: str):
        assert slug == "swept"
        yield scope

    monkeypatch.setattr("collection.cli._tenant_scope", _scope)
    return scope


def _abandoned(scope, hours: float):
    """An open thread whose last word was `hours` ago, through its customer."""
    import uuid
    from datetime import UTC, datetime, timedelta

    from collection.db.models import Conversation, Customer

    customer = Customer(external_id=f"cus-{uuid.uuid4().hex[:8]}", pseudonym=uuid.uuid4().hex)
    scope.add(customer)
    scope.session.flush()
    conversation = Conversation(
        customer_id=customer.id,
        channel="chat",
        opened_at=datetime.now(UTC) - timedelta(hours=hours),
    )
    scope.add(conversation)
    scope.session.flush()
    return conversation


@pytest.mark.db
def test_conversations_sweep_closes_an_abandoned_thread(swept):
    from collection.db.models import ConversationState

    conversation = _abandoned(swept, hours=48)

    result = runner.invoke(app, ["conversations", "sweep", "--tenant", "swept"])

    assert result.exit_code == 0, result.output + str(result.exception)
    assert "1" in result.output
    assert conversation.state is ConversationState.CLOSED


@pytest.mark.db
def test_conversations_sweep_dry_run_writes_nothing(swept):
    from collection.db.models import ConversationState

    conversation = _abandoned(swept, hours=48)

    result = runner.invoke(app, ["conversations", "sweep", "--tenant", "swept", "--dry-run"])

    assert result.exit_code == 0, result.output + str(result.exception)
    assert "Would close" in result.output
    assert conversation.state is ConversationState.OPEN


@pytest.mark.db
def test_conversations_sweep_leaves_a_live_thread_alone(swept):
    from collection.db.models import ConversationState

    conversation = _abandoned(swept, hours=1)

    result = runner.invoke(app, ["conversations", "sweep", "--tenant", "swept"])

    assert result.exit_code == 0, result.output + str(result.exception)
    assert "nothing to close" in result.output
    assert conversation.state is ConversationState.OPEN


def _closed(scope, days_ago: float, turns: int = 2):
    """A closed thread with a transcript, closed `days_ago`."""
    import uuid
    from datetime import UTC, datetime, timedelta

    from collection.db.models import Conversation, ConversationState, Customer, Turn

    closed_at = datetime.now(UTC) - timedelta(days=days_ago)
    customer = Customer(external_id=f"cus-{uuid.uuid4().hex[:8]}", pseudonym=uuid.uuid4().hex)
    scope.add(customer)
    scope.session.flush()
    conversation = Conversation(
        customer_id=customer.id,
        channel="chat",
        state=ConversationState.CLOSED,
        opened_at=closed_at - timedelta(hours=1),
        closed_at=closed_at,
    )
    scope.add(conversation)
    scope.session.flush()
    for index in range(turns):
        scope.add(Turn(conversation_id=conversation.id, role="customer", text=f"t{index}"))
    scope.session.flush()
    return conversation


@pytest.mark.db
def test_conversations_purge_erases_a_transcript_past_the_window(swept):
    conversation = _closed(swept, days_ago=400)

    result = runner.invoke(app, ["conversations", "purge", "--tenant", "swept", "--yes"])

    assert result.exit_code == 0, result.output + str(result.exception)
    assert "1" in result.output
    assert conversation.purged_at is not None


@pytest.mark.db
def test_conversations_purge_does_nothing_without_an_answer(swept):
    conversation = _closed(swept, days_ago=400)

    result = runner.invoke(app, ["conversations", "purge", "--tenant", "swept"], input="n\n")

    assert result.exit_code != 0
    assert conversation.purged_at is None


@pytest.mark.db
def test_conversations_purge_dry_run_deletes_nothing(swept):
    conversation = _closed(swept, days_ago=400)

    result = runner.invoke(app, ["conversations", "purge", "--tenant", "swept", "--dry-run"])

    assert result.exit_code == 0, result.output + str(result.exception)
    assert "Would erase" in result.output
    assert conversation.purged_at is None


@pytest.mark.db
def test_conversations_purge_leaves_a_recently_closed_thread_alone(swept):
    conversation = _closed(swept, days_ago=2)

    result = runner.invoke(app, ["conversations", "purge", "--tenant", "swept", "--yes"])

    assert result.exit_code == 0, result.output + str(result.exception)
    assert "nothing to erase" in result.output
    assert conversation.purged_at is None


def _escalated(scope, holder: str = "queue", reason: str = "dispute"):
    """A thread the agent handed over, waiting for a person."""
    import uuid
    from datetime import UTC, datetime

    from collection.db.models import Conversation, ConversationState, Customer

    customer = Customer(external_id=f"cus-{uuid.uuid4().hex[:8]}", pseudonym=uuid.uuid4().hex)
    scope.add(customer)
    scope.session.flush()
    conversation = Conversation(
        customer_id=customer.id,
        channel="chat",
        state=ConversationState.ESCALATED,
        holder=holder,
        escalation_reason=reason,
        opened_at=datetime.now(UTC),
    )
    scope.add(conversation)
    scope.session.flush()
    return conversation


def _operator(scope, email: str):
    from collection.db.models import InternalUser

    user = InternalUser(email=email, password_hash="x", role="operator", active=True)
    scope.add(user)
    scope.session.flush()
    return user


@pytest.mark.db
def test_conversations_queue_lists_what_is_waiting(swept):
    conversation = _escalated(swept)

    result = runner.invoke(app, ["conversations", "queue", "--tenant", "swept"])

    assert result.exit_code == 0, result.output + str(result.exception)
    assert "dispute" in result.output
    assert str(conversation.id)[:8] in result.output


@pytest.mark.db
def test_conversations_queue_says_so_when_nothing_is_waiting(swept):
    result = runner.invoke(app, ["conversations", "queue", "--tenant", "swept"])

    assert result.exit_code == 0, result.output + str(result.exception)
    assert "nothing waiting" in result.output


@pytest.mark.db
def test_conversations_claim_puts_the_operator_on_the_thread(swept):
    _operator(swept, "ana@cobranca.example")
    conversation = _escalated(swept)

    result = runner.invoke(
        app,
        [
            "conversations",
            "claim",
            str(conversation.id),
            "--tenant",
            "swept",
            "--user",
            "ana@cobranca.example",
        ],
    )

    assert result.exit_code == 0, result.output + str(result.exception)
    assert conversation.holder == "ana@cobranca.example"


@pytest.mark.db
def test_conversations_claim_reports_an_unknown_operator_without_a_traceback(swept):
    conversation = _escalated(swept)

    result = runner.invoke(
        app,
        [
            "conversations",
            "claim",
            str(conversation.id),
            "--tenant",
            "swept",
            "--user",
            "ninguem@cobranca.example",
        ],
    )

    assert result.exit_code == 1
    assert "No active operator" in result.output
    assert conversation.holder == "queue"


@pytest.mark.db
def test_conversations_resolve_closes_the_thread(swept):
    from collection.db.models import ConversationState

    conversation = _escalated(swept, holder="ana@cobranca.example")

    result = runner.invoke(
        app, ["conversations", "resolve", str(conversation.id), "--tenant", "swept"]
    )

    assert result.exit_code == 0, result.output + str(result.exception)
    assert conversation.state is ConversationState.CLOSED
    assert conversation.holder == "ana@cobranca.example"


@pytest.mark.db
def test_conversations_resolve_refuses_a_thread_that_was_never_escalated(swept):
    conversation = _abandoned(swept, hours=1)

    result = runner.invoke(
        app, ["conversations", "resolve", str(conversation.id), "--tenant", "swept"]
    )

    assert result.exit_code == 1
    assert "not escalated" in result.output
