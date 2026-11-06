"""An escalated thread has an owner and an exit.

The agent hands a conversation over by escalating it. Until an operator takes it, it
belongs to the queue; once one does, it belongs to them by name; and resolving it is what
finally closes it. These pin that chain, and the refusals that keep `holder` honest.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from collection.channels.handover import (
    AGENT,
    QUEUE,
    AlreadyHeld,
    NotEscalated,
    UnknownOperator,
    claim,
    queued,
    resolve,
)
from collection.db.models import (
    Conversation,
    ConversationState,
    Customer,
    InternalUser,
    Tenant,
    TenantState,
)
from collection.db.repository import TenantScope

pytestmark = pytest.mark.db

ANA = "ana@cobranca.example"
BRUNO = "bruno@cobranca.example"


def _scope(db: Session, slug: str = "t") -> TenantScope:
    tenant = Tenant(name=slug, slug=slug, state=TenantState.ACTIVE)
    db.add(tenant)
    db.flush()
    return TenantScope(
        Session(bind=db.connection(), join_transaction_mode="create_savepoint"), tenant.id
    )


def _operator(scope: TenantScope, email: str, active: bool = True) -> InternalUser:
    user = InternalUser(email=email, password_hash="x", role="operator", active=active)
    scope.add(user)
    scope.session.flush()
    return user


def _thread(
    scope: TenantScope,
    *,
    state: ConversationState = ConversationState.ESCALATED,
    holder: str = QUEUE,
    opened_at: datetime | None = None,
) -> Conversation:
    customer = Customer(external_id=f"cus-{uuid.uuid4().hex[:8]}", pseudonym=uuid.uuid4().hex)
    scope.add(customer)
    scope.session.flush()
    conversation = Conversation(
        customer_id=customer.id,
        channel="chat",
        state=state,
        holder=holder,
        opened_at=opened_at or datetime.now(UTC),
    )
    scope.add(conversation)
    scope.session.flush()
    return conversation


def test_the_queue_is_the_escalated_threads_nobody_has_taken(db):
    scope = _scope(db)
    waiting = _thread(scope)
    _thread(scope, state=ConversationState.OPEN, holder=AGENT)
    _thread(scope, state=ConversationState.CLOSED, holder=AGENT)

    assert [c.id for c in queued(scope)] == [waiting.id]


def test_the_queue_is_oldest_first(db):
    """It is a queue. Whoever has waited longest is answered first."""
    scope = _scope(db)
    now = datetime.now(UTC)
    newer = _thread(scope, opened_at=now - timedelta(minutes=5))
    older = _thread(scope, opened_at=now - timedelta(hours=3))

    assert [c.id for c in queued(scope)] == [older.id, newer.id]


def test_a_thread_someone_already_holds_is_not_in_the_queue(db):
    scope = _scope(db)
    _operator(scope, ANA)
    held = _thread(scope)
    claim(scope, held.id, email=ANA)

    assert queued(scope) == []


def test_claiming_puts_the_operators_name_on_the_thread(db):
    scope = _scope(db)
    _operator(scope, ANA)
    thread = _thread(scope)

    claim(scope, thread.id, email=ANA)

    assert thread.holder == ANA
    assert thread.state is ConversationState.ESCALATED


def test_claiming_a_thread_someone_else_holds_is_refused_and_names_them(db):
    scope = _scope(db)
    _operator(scope, ANA)
    _operator(scope, BRUNO)
    thread = _thread(scope)
    claim(scope, thread.id, email=ANA)

    with pytest.raises(AlreadyHeld) as error:
        claim(scope, thread.id, email=BRUNO)

    assert ANA in str(error.value)
    assert thread.holder == ANA


def test_reclaiming_a_thread_you_already_hold_is_not_an_error(db):
    """Running the command twice is not a conflict with yourself."""
    scope = _scope(db)
    _operator(scope, ANA)
    thread = _thread(scope)
    claim(scope, thread.id, email=ANA)

    claim(scope, thread.id, email=ANA)

    assert thread.holder == ANA


def test_an_open_thread_cannot_be_claimed(db):
    """The agent is answering it. Taking it over is what escalation is for."""
    scope = _scope(db)
    _operator(scope, ANA)
    thread = _thread(scope, state=ConversationState.OPEN, holder=AGENT)

    with pytest.raises(NotEscalated):
        claim(scope, thread.id, email=ANA)

    assert thread.holder == AGENT


def test_an_unknown_operator_cannot_claim(db):
    scope = _scope(db)
    thread = _thread(scope)

    with pytest.raises(UnknownOperator):
        claim(scope, thread.id, email=ANA)

    assert thread.holder == QUEUE


def test_a_deactivated_operator_cannot_claim(db):
    """Someone who has left the company is not someone a case may be assigned to."""
    scope = _scope(db)
    _operator(scope, ANA, active=False)
    thread = _thread(scope)

    with pytest.raises(UnknownOperator):
        claim(scope, thread.id, email=ANA)

    assert thread.holder == QUEUE


def test_another_tenants_operator_cannot_claim(db):
    ours, theirs = _scope(db, "ours"), _scope(db, "theirs")
    _operator(theirs, ANA)
    thread = _thread(ours)

    with pytest.raises(UnknownOperator):
        claim(ours, thread.id, email=ANA)

    assert thread.holder == QUEUE


def test_another_tenants_thread_cannot_be_claimed(db):
    ours, theirs = _scope(db, "ours"), _scope(db, "theirs")
    _operator(ours, ANA)
    theirs_thread = _thread(theirs)

    with pytest.raises(LookupError):
        claim(ours, theirs_thread.id, email=ANA)

    assert theirs_thread.holder == QUEUE


def test_resolving_closes_the_thread_and_keeps_who_handled_it(db):
    scope = _scope(db)
    _operator(scope, ANA)
    thread = _thread(scope)
    claim(scope, thread.id, email=ANA)

    resolve(scope, thread.id)

    assert thread.state is ConversationState.CLOSED
    assert thread.closed_at is not None
    # The record of who handled it survives the closing: it is the answer to "who dealt
    # with this customer", which a closed thread is exactly what you ask it of.
    assert thread.holder == ANA


def test_a_thread_still_in_the_queue_can_be_resolved(db):
    """Not every escalation needs a claim first -- some are answered off-channel."""
    scope = _scope(db)
    thread = _thread(scope)

    resolve(scope, thread.id)

    assert thread.state is ConversationState.CLOSED


def test_resolving_an_open_thread_is_refused(db):
    scope = _scope(db)
    thread = _thread(scope, state=ConversationState.OPEN, holder=AGENT)

    with pytest.raises(NotEscalated):
        resolve(scope, thread.id)

    assert thread.state is ConversationState.OPEN
