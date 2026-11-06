"""Closing the conversations nothing is tracking any more.

`DatabaseSessionStore._sweep` closes a thread when the session holding it expires. That
covers the ordinary case -- a visitor who walks away from an open widget -- and nothing
else: the sweep can only see the sessions its own process holds. A process that is
restarted, killed, or that crashes mid-request leaves every thread it was tracking `OPEN`
in the record, with nothing anywhere that will ever close it. Those rows accumulate, and
once they do, `OPEN` stops meaning "someone is talking to us" and starts meaning "someone
was, at some point, maybe". A state that cannot be trusted is not worth querying, and the
reuse in `conversation_for` reads exactly this state.

So this is the sweep that runs over the record rather than over a process, and the two
answer different questions on purpose. The store asks whether a *session* is still alive,
which it knows from `last_seen_at` -- a field that advances on any activity, a transcript
redraw included. From the rows, the most recent thing visible is the last turn, so a
thread that is open but silent looks identical to one that was abandoned. The window here
is therefore a day rather than the session TTL's half hour: wide enough that no live
thread can fall inside it, which is what lets this run against a serving database without
closing a conversation out from under the person having it.

Two states are left alone. `CLOSED` is already done, and rewriting `closed_at` would
move a record of when a thread ended. `ESCALATED` is a person's queue item: it is open
because nobody has answered it yet, and that is a backlog to work, never one to sweep.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from collection.db.models.conversation import Conversation, ConversationState, Turn
from collection.db.repository import TenantScope

#: A thread with nothing said for this long is not one anybody is still having. A day,
#: against a session TTL of half an hour: see the module docstring for why the gap is
#: deliberately that wide.
ABANDONED_AFTER_SECONDS = 24 * 60 * 60


def find_abandoned(
    scope: TenantScope,
    *,
    after_seconds: int = ABANDONED_AFTER_SECONDS,
    now: datetime | None = None,
) -> list[Conversation]:
    """This tenant's open threads that have been silent for longer than the window.

    Silence is measured from the last turn, falling back to `opened_at` for a thread
    that never got one -- a widget opened and abandoned before the greeting was answered
    is still a thread, and the greeting is written as a turn, so the fallback is for the
    rows a seeding path creates rather than for an ordinary chat.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(seconds=after_seconds)
    last_turn = (
        select(Turn.conversation_id, func.max(Turn.created_at).label("last_at"))
        .where(Turn.tenant_id == scope.tenant_id)
        .group_by(Turn.conversation_id)
        .subquery()
    )
    statement = (
        scope.select(Conversation)
        .outerjoin(last_turn, last_turn.c.conversation_id == Conversation.id)
        .where(
            Conversation.state == ConversationState.OPEN,
            func.coalesce(last_turn.c.last_at, Conversation.opened_at) < cutoff,
        )
        .order_by(Conversation.opened_at)
    )
    return list(scope.session.scalars(statement).all())


def close_abandoned(
    scope: TenantScope,
    *,
    after_seconds: int = ABANDONED_AFTER_SECONDS,
    now: datetime | None = None,
) -> list[Conversation]:
    """Close them, and return the ones closed so a caller can say what it did."""
    closed_at = now or datetime.now(UTC)
    abandoned = find_abandoned(scope, after_seconds=after_seconds, now=closed_at)
    for conversation in abandoned:
        conversation.state = ConversationState.CLOSED
        conversation.closed_at = closed_at
    scope.session.flush()
    return abandoned
