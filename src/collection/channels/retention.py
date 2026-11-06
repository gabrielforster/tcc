"""Erasing a transcript at the end of its retention window.

LGPD art. 15 and 16: processing ends when its purpose is met, and the data is then
eliminated. A conversation transcript has no exemption that makes it an exception -- it
is personal data, often the most revealing kind this system holds, because a debtor
explaining why they have not paid says things no invoice row contains. Keeping it forever
is a decision, and it is the wrong one; `settings.transcript_retention_days` makes it a
window instead.

Six months is the default because that is roughly how long a complaint about the way a
collection was conducted stays live, and for that complaint the transcript is the
evidence. Past it, what reporting needs from a thread is its shape -- when it happened,
on which channel, whether it escalated, how long it ran -- and the words are a liability
holding no remaining purpose.

So the purge deletes turns and the retrieval rows that cite them, and leaves the
conversation. The row that survives carries `purged_at`, and that column is the point:
a thread whose transcript was erased and a thread nobody ever spoke in both have no
turns, and a controller who cannot tell the two apart cannot show a regulator that
anything was ever erased. Erasure has to leave a mark, or it is indistinguishable from
never having held the data.

Two states are refused. An `OPEN` thread has not started ageing -- age is measured from
`closed_at`, so a conversation still being had can never be caught by a window, however
long it has been running. An `ESCALATED` one is a person's queue item, unanswered by
definition, and deleting the transcript would hand them a case with its contents missing.
`collection conversations sweep` is what moves an abandoned thread to `CLOSED` and starts
its clock.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from collection.db.models.conversation import Conversation, ConversationState, Turn, TurnRetrieval
from collection.db.repository import TenantScope


def find_expired(
    scope: TenantScope,
    *,
    older_than_days: int,
    now: datetime | None = None,
) -> list[Conversation]:
    """This tenant's closed threads past the window that still have a transcript."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=older_than_days)
    statement = (
        scope.select(Conversation)
        .where(
            Conversation.state == ConversationState.CLOSED,
            Conversation.closed_at.is_not(None),
            Conversation.closed_at < cutoff,
            Conversation.purged_at.is_(None),
            # A thread nobody spoke in has nothing to erase, and marking it purged would
            # claim an erasure that never happened.
            scope.select(Turn).where(Turn.conversation_id == Conversation.id).exists(),
        )
        .order_by(Conversation.closed_at)
    )
    return list(scope.session.scalars(statement).all())


def purge_transcripts(
    scope: TenantScope,
    *,
    older_than_days: int,
    now: datetime | None = None,
) -> list[Conversation]:
    """Erase them, and return the conversations whose transcripts are now gone."""
    purged_at = now or datetime.now(UTC)
    expired = find_expired(scope, older_than_days=older_than_days, now=purged_at)
    if not expired:
        return []

    ids = [conversation.id for conversation in expired]
    turns = scope.select(Turn).where(Turn.conversation_id.in_(ids)).with_only_columns(Turn.id)
    # The citations go first: a retrieval row points at a turn, and leaving one behind
    # would be evidence for an answer whose text no longer exists.
    scope.session.execute(
        delete(TurnRetrieval).where(
            TurnRetrieval.tenant_id == scope.tenant_id,
            TurnRetrieval.turn_id.in_(select(turns.subquery().c.id)),
        )
    )
    scope.session.execute(
        delete(Turn).where(Turn.tenant_id == scope.tenant_id, Turn.conversation_id.in_(ids))
    )
    for conversation in expired:
        conversation.purged_at = purged_at
    scope.session.flush()
    return expired
