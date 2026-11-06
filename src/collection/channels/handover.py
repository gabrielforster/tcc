"""An escalated thread's owner, and its exit.

`Conversation.holder` has been a column with a docstring and no writer: "who currently
answers: the agent, or the operator who took over", defaulted to `agent` and never
changed by anything. A column nothing writes is worse than a missing one, because a
reader believes it. The escalation path set `state` and `escalation_reason` and left
`holder` saying the agent was still answering a thread the agent had just handed over.

That mattered more once the sweep and the purge landed. Both refuse to touch an
`ESCALATED` thread, on the stated grounds that a person owns it -- so escalated threads
are the one kind nothing retires, and until now there was no way for a person to own one
or to finish with it. This is the other half of that argument.

`holder` takes three kinds of value, and they are told apart without ambiguity because an
address contains an `@` and neither constant does:

- `agent` -- the agent is answering, which is every open thread
- `queue` -- escalated and not yet taken; the backlog
- an operator's email -- that person has it

`claim` requires an active `InternalUser` of this tenant. That is identity, not
authentication: `internal_users` is still schema-only by design, and whatever decides
about password hashing and roles will land with the console. Naming a holder who is not
on the tenant's staff, or who has left it, is the part worth refusing now.

`resolve` is the exit, and it is the only path that closes an escalated thread --
`SessionStore._close` moves `OPEN` to `CLOSED` and deliberately leaves `ESCALATED` alone.
Closing is what starts the retention clock, so a resolved thread is one the transcript
purge can eventually reach; an unresolved one is kept, which is the right way round.
"""

import uuid
from datetime import UTC, datetime

from collection.db.models.conversation import Conversation, ConversationState
from collection.db.models.internal_user import InternalUser
from collection.db.repository import TenantScope

#: The agent is answering. Every open thread, and the column's default.
AGENT = "agent"
#: Escalated and not yet taken by anyone.
QUEUE = "queue"


class HandoverError(RuntimeError):
    """A takeover that the record will not allow."""


class NotEscalated(HandoverError):
    """The thread is not a person's to take or to finish."""


class AlreadyHeld(HandoverError):
    """Someone else has it."""


class UnknownOperator(HandoverError):
    """Nobody active on this tenant's staff answers to that address."""


def queued(scope: TenantScope) -> list[Conversation]:
    """The escalated threads nobody has taken, oldest first.

    Oldest first because it is a queue: the customer who has waited longest is the one
    who has waited longest. `opened_at` rather than the escalation's time, which the
    record does not keep separately -- near enough, since a thread escalates in the
    minutes after it opens or not at all.
    """
    statement = (
        scope.select(Conversation)
        .where(
            Conversation.state == ConversationState.ESCALATED,
            Conversation.holder == QUEUE,
        )
        .order_by(Conversation.opened_at)
    )
    return list(scope.session.scalars(statement).all())


def claim(scope: TenantScope, conversation_id: uuid.UUID, *, email: str) -> Conversation:
    """Put an operator's name on an escalated thread."""
    conversation = _escalated(scope, conversation_id)
    if conversation.holder not in (QUEUE, email):
        raise AlreadyHeld(f"{conversation_id} is held by {conversation.holder}.")
    _operator(scope, email)
    conversation.holder = email
    scope.session.flush()
    return conversation


def resolve(scope: TenantScope, conversation_id: uuid.UUID) -> Conversation:
    """Close an escalated thread. The holder stays on it: it answers who handled this."""
    conversation = _escalated(scope, conversation_id)
    conversation.state = ConversationState.CLOSED
    conversation.closed_at = datetime.now(UTC)
    scope.session.flush()
    return conversation


def _escalated(scope: TenantScope, conversation_id: uuid.UUID) -> Conversation:
    conversation = scope.get(Conversation, conversation_id)
    if conversation is None:
        raise LookupError(f"No conversation {conversation_id} for this tenant.")
    if conversation.state is not ConversationState.ESCALATED:
        raise NotEscalated(
            f"{conversation_id} is {conversation.state.value}, not escalated. "
            "Only a thread the agent handed over is a person's to take or to finish."
        )
    return conversation


def _operator(scope: TenantScope, email: str) -> InternalUser:
    matches = scope.all(InternalUser, InternalUser.email == email, InternalUser.active.is_(True))
    if not matches:
        raise UnknownOperator(f"No active operator {email!r} on this tenant's staff.")
    return matches[0]
