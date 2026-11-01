"""Closing the threads nothing is tracking any more.

The store's own sweep runs over the sessions one process holds, so it can only close what
that process opened. These pin the sweep that runs over the record instead: what it
closes, what it refuses to touch, and that it stays inside one tenant.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from collection.channels.sweep import ABANDONED_AFTER_SECONDS, close_abandoned, find_abandoned
from collection.db.models import (
    Conversation,
    ConversationState,
    Customer,
    Tenant,
    TenantState,
    Turn,
)
from collection.db.repository import TenantScope

pytestmark = pytest.mark.db

WINDOW = timedelta(seconds=ABANDONED_AFTER_SECONDS)


def _scope(db: Session, slug: str = "t") -> TenantScope:
    tenant = Tenant(name=slug, slug=slug, state=TenantState.ACTIVE)
    db.add(tenant)
    db.flush()
    return TenantScope(
        Session(bind=db.connection(), join_transaction_mode="create_savepoint"), tenant.id
    )


def _customer(scope: TenantScope) -> Customer:
    customer = Customer(external_id=f"cus-{uuid.uuid4().hex[:8]}", pseudonym=uuid.uuid4().hex)
    scope.add(customer)
    scope.session.flush()
    return customer


def _conversation(
    scope: TenantScope,
    *,
    opened_at: datetime,
    state: ConversationState = ConversationState.OPEN,
    spoke_at: datetime | None = None,
) -> Conversation:
    conversation = Conversation(
        customer_id=_customer(scope).id,
        channel="chat",
        state=state,
        opened_at=opened_at,
    )
    scope.add(conversation)
    scope.session.flush()
    if spoke_at is not None:
        turn = Turn(
            conversation_id=conversation.id,
            role="customer",
            text="ola",
            created_at=spoke_at,
        )
        scope.add(turn)
        scope.session.flush()
    return conversation


def test_an_open_thread_with_nothing_said_inside_the_window_is_closed(db):
    scope = _scope(db)
    now = datetime.now(UTC)
    abandoned = _conversation(scope, opened_at=now - WINDOW - timedelta(minutes=1))

    closed = close_abandoned(scope, now=now)

    assert [c.id for c in closed] == [abandoned.id]
    assert abandoned.state is ConversationState.CLOSED
    assert abandoned.closed_at is not None


def test_a_thread_that_spoke_inside_the_window_is_left_open(db):
    scope = _scope(db)
    now = datetime.now(UTC)
    # Opened long ago, so only the turn can save it.
    live = _conversation(
        scope,
        opened_at=now - WINDOW - timedelta(hours=5),
        spoke_at=now - timedelta(minutes=1),
    )

    assert close_abandoned(scope, now=now) == []
    assert live.state is ConversationState.OPEN


def test_a_thread_whose_last_turn_is_older_than_the_window_is_closed(db):
    scope = _scope(db)
    now = datetime.now(UTC)
    # Opened recently in wall-clock terms, but silent since: the last turn decides, not
    # the opening, or a long thread would be swept while it was still being answered.
    quiet = _conversation(
        scope,
        opened_at=now - WINDOW - timedelta(hours=2),
        spoke_at=now - WINDOW - timedelta(minutes=1),
    )

    assert [c.id for c in close_abandoned(scope, now=now)] == [quiet.id]
    assert quiet.state is ConversationState.CLOSED


def test_an_escalated_thread_is_never_closed_by_the_sweep(db):
    """A person owns it. Closing it would retire a queue item nobody has answered."""
    scope = _scope(db)
    now = datetime.now(UTC)
    escalated = _conversation(
        scope,
        opened_at=now - WINDOW - timedelta(days=30),
        state=ConversationState.ESCALATED,
    )

    assert close_abandoned(scope, now=now) == []
    assert escalated.state is ConversationState.ESCALATED


def test_an_already_closed_thread_keeps_the_time_it_was_closed(db):
    scope = _scope(db)
    now = datetime.now(UTC)
    closed_at = now - timedelta(days=9)
    done = _conversation(
        scope,
        opened_at=now - WINDOW - timedelta(days=10),
        state=ConversationState.CLOSED,
    )
    done.closed_at = closed_at
    scope.session.flush()

    assert close_abandoned(scope, now=now) == []
    assert done.closed_at == closed_at


def test_the_sweep_does_not_reach_another_tenants_abandoned_thread(db):
    now = datetime.now(UTC)
    ours, theirs = _scope(db, "ours"), _scope(db, "theirs")
    mine = _conversation(ours, opened_at=now - WINDOW - timedelta(minutes=1))
    yours = _conversation(theirs, opened_at=now - WINDOW - timedelta(minutes=1))

    assert [c.id for c in close_abandoned(ours, now=now)] == [mine.id]
    assert yours.state is ConversationState.OPEN


def test_find_abandoned_reports_without_closing(db):
    """The dry run an operator gets before letting the sweep write."""
    scope = _scope(db)
    now = datetime.now(UTC)
    abandoned = _conversation(scope, opened_at=now - WINDOW - timedelta(minutes=1))

    found = find_abandoned(scope, now=now)

    assert [c.id for c in found] == [abandoned.id]
    assert abandoned.state is ConversationState.OPEN


def test_the_window_is_the_callers_to_widen(db):
    scope = _scope(db)
    now = datetime.now(UTC)
    recent = _conversation(scope, opened_at=now - timedelta(minutes=10))

    assert close_abandoned(scope, now=now) == []
    assert [c.id for c in close_abandoned(scope, after_seconds=60, now=now)] == [recent.id]
