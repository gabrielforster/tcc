"""Erasing a transcript at the end of its retention window.

What a purge must do is delete the words. What it must not do is lose the fact that a
conversation happened, lose the fact that the words were erased, or reach a thread that
is still live or still a person's to answer.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from collection.channels.retention import find_expired, purge_transcripts
from collection.db.models import (
    Conversation,
    ConversationState,
    Customer,
    KnowledgeChunk,
    Tenant,
    TenantState,
    Turn,
    TurnRetrieval,
)
from collection.db.repository import TenantScope
from collection.rag.store import publish_document

pytestmark = pytest.mark.db

RETENTION_DAYS = 180
WINDOW = timedelta(days=RETENTION_DAYS)


def _scope(db: Session, slug: str = "t") -> TenantScope:
    tenant = Tenant(name=slug, slug=slug, state=TenantState.ACTIVE)
    db.add(tenant)
    db.flush()
    return TenantScope(
        Session(bind=db.connection(), join_transaction_mode="create_savepoint"), tenant.id
    )


def _thread(
    scope: TenantScope,
    *,
    state: ConversationState = ConversationState.CLOSED,
    closed_at: datetime | None,
    turns: int = 2,
) -> Conversation:
    customer = Customer(external_id=f"cus-{uuid.uuid4().hex[:8]}", pseudonym=uuid.uuid4().hex)
    scope.add(customer)
    scope.session.flush()
    conversation = Conversation(
        customer_id=customer.id,
        channel="chat",
        state=state,
        opened_at=(closed_at or datetime.now(UTC)) - timedelta(hours=1),
        closed_at=closed_at,
    )
    scope.add(conversation)
    scope.session.flush()
    for index in range(turns):
        turn = Turn(
            conversation_id=conversation.id,
            role="customer" if index % 2 == 0 else "agent",
            text=f"turn {index}",
        )
        scope.add(turn)
    scope.session.flush()
    return conversation


def _turns(scope: TenantScope, conversation: Conversation) -> int:
    return scope.session.scalar(
        select(func.count())
        .select_from(Turn)
        .where(Turn.tenant_id == scope.tenant_id, Turn.conversation_id == conversation.id)
    )


def test_a_closed_thread_past_the_window_loses_its_turns(db):
    scope = _scope(db)
    now = datetime.now(UTC)
    old = _thread(scope, closed_at=now - WINDOW - timedelta(days=1))

    purged = purge_transcripts(scope, older_than_days=RETENTION_DAYS, now=now)

    assert [c.id for c in purged] == [old.id]
    assert _turns(scope, old) == 0


def test_the_conversation_itself_survives_the_purge(db):
    """The words go; the fact that a conversation happened is what reporting counts."""
    scope = _scope(db)
    now = datetime.now(UTC)
    old = _thread(scope, closed_at=now - WINDOW - timedelta(days=1))

    purge_transcripts(scope, older_than_days=RETENTION_DAYS, now=now)

    assert scope.get(Conversation, old.id) is not None
    assert old.channel == "chat"
    assert old.state is ConversationState.CLOSED
    assert old.closed_at is not None


def test_a_purged_thread_is_distinguishable_from_one_never_spoken_in(db):
    scope = _scope(db)
    now = datetime.now(UTC)
    old = _thread(scope, closed_at=now - WINDOW - timedelta(days=1))
    silent = _thread(scope, closed_at=now - WINDOW - timedelta(days=1), turns=0)

    purge_transcripts(scope, older_than_days=RETENTION_DAYS, now=now)

    assert _turns(scope, old) == _turns(scope, silent) == 0
    assert old.purged_at is not None
    assert silent.purged_at is None


def test_a_closed_thread_inside_the_window_keeps_its_transcript(db):
    scope = _scope(db)
    now = datetime.now(UTC)
    recent = _thread(scope, closed_at=now - timedelta(days=1))

    assert purge_transcripts(scope, older_than_days=RETENTION_DAYS, now=now) == []
    assert _turns(scope, recent) == 2


def test_an_open_thread_is_never_purged_however_old_it_is(db):
    """Age is measured from closing. A thread with no `closed_at` has not started ageing."""
    scope = _scope(db)
    now = datetime.now(UTC)
    live = _thread(scope, state=ConversationState.OPEN, closed_at=None)
    live.opened_at = now - WINDOW - timedelta(days=400)
    scope.session.flush()

    assert purge_transcripts(scope, older_than_days=RETENTION_DAYS, now=now) == []
    assert _turns(scope, live) == 2


def test_an_escalated_thread_is_never_purged(db):
    scope = _scope(db)
    now = datetime.now(UTC)
    escalated = _thread(
        scope,
        state=ConversationState.ESCALATED,
        closed_at=now - WINDOW - timedelta(days=1),
    )

    assert purge_transcripts(scope, older_than_days=RETENTION_DAYS, now=now) == []
    assert _turns(scope, escalated) == 2


def test_the_retrieval_evidence_goes_with_the_turns_it_belongs_to(db):
    """A retrieval row cites a turn. Leaving it behind orphans evidence of a deleted answer."""
    scope = _scope(db)
    now = datetime.now(UTC)
    old = _thread(scope, closed_at=now - WINDOW - timedelta(days=1))
    turn = scope.all(Turn, Turn.conversation_id == old.id)[0]
    version = publish_document(scope, title="T", path="faq/t.md", body="Posso parcelar.")
    chunk = scope.all(KnowledgeChunk, KnowledgeChunk.version_id == version.id)[0]
    scope.add(
        TurnRetrieval(
            turn_id=turn.id,
            version_id=chunk.version_id,
            chunk_id=chunk.id,
            score=0.5,
            rank=1,
        )
    )
    scope.session.flush()

    purge_transcripts(scope, older_than_days=RETENTION_DAYS, now=now)

    remaining = scope.session.scalar(
        select(func.count())
        .select_from(TurnRetrieval)
        .where(TurnRetrieval.tenant_id == scope.tenant_id)
    )
    assert remaining == 0


def test_purging_twice_changes_nothing_the_second_time(db):
    scope = _scope(db)
    now = datetime.now(UTC)
    old = _thread(scope, closed_at=now - WINDOW - timedelta(days=1))

    purge_transcripts(scope, older_than_days=RETENTION_DAYS, now=now)
    first = old.purged_at

    assert purge_transcripts(scope, older_than_days=RETENTION_DAYS, now=now) == []
    assert old.purged_at == first


def test_the_purge_does_not_reach_another_tenants_transcript(db):
    now = datetime.now(UTC)
    ours, theirs = _scope(db, "ours"), _scope(db, "theirs")
    mine = _thread(ours, closed_at=now - WINDOW - timedelta(days=1))
    yours = _thread(theirs, closed_at=now - WINDOW - timedelta(days=1))

    assert [c.id for c in purge_transcripts(ours, older_than_days=RETENTION_DAYS, now=now)] == [
        mine.id
    ]
    assert _turns(theirs, yours) == 2


def test_find_expired_reports_without_deleting(db):
    scope = _scope(db)
    now = datetime.now(UTC)
    old = _thread(scope, closed_at=now - WINDOW - timedelta(days=1))

    found = find_expired(scope, older_than_days=RETENTION_DAYS, now=now)

    assert [c.id for c in found] == [old.id]
    assert _turns(scope, old) == 2
    assert old.purged_at is None
