"""A conversation, its turns, and the evidence behind each answer.

`TurnRetrieval` is the reason this module exists. Per turn it stores every passage that
was retrieved -- the document's *version*, the chunk, the similarity score and the rank --
written when retrieval happens and never reconstructed. It is what an operator reads when
taking over a conversation and what an analyst reads to judge whether an answer was
faithful to what the agent was actually shown. Once a document has a newer version the
question cannot be answered from the document, which is why versions are immutable and why
the row points at the version that was read rather than at the document.

The chunk is referenced through `(tenant_id, version_id, chunk_id)`, so the database also
refuses a row whose chunk does not belong to the version it claims; a retrieval record
that cites a chunk under the wrong version would be evidence that lies.

A turn with no retrieval rows is a valid and meaningful state: an answer given with
nothing retrieved. Absence of rows must stay distinguishable from absence of a turn.

`provider` and `model` sit beside `responder` because answers are not comparable across
models, and a console that cannot tell them apart cannot be used to compare them.

`created_at` on a turn defaults to `clock_timestamp()` rather than `now()`: `now()` is the
start of the transaction, so turns written in one unit of work would tie and lose their
order. Order is by `created_at` alone, with no sequence column: `clock_timestamp()`
advances per statement and a conversation's turns are written one after another, so a tie
needs a clock adjustment mid-conversation. That was considered, not missed.

Every table carries its own `tenant_id` with composite foreign keys, for the reason
given in `customer.py`.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from collection.db.base import Base, TenantScoped
from collection.domain.intent import Intent


class ConversationState(enum.StrEnum):
    OPEN = "open"
    ESCALATED = "escalated"
    CLOSED = "closed"


class Conversation(TenantScoped, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        # The target of `Turn`'s composite foreign key.
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(index=True)
    channel: Mapped[str] = mapped_column(String(20))
    state: Mapped[ConversationState] = mapped_column(
        SAEnum(
            ConversationState,
            name="conversation_state",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=ConversationState.OPEN,
    )
    #: Who currently answers: the agent, or the operator who took over.
    holder: Mapped[str] = mapped_column(String(100), default="agent")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalation_reason: Mapped[str | None] = mapped_column(Text)


class Turn(TenantScoped, Base):
    __tablename__ = "turns"
    __table_args__ = (
        # The target of `TurnRetrieval`'s composite foreign key.
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"], ["conversations.tenant_id", "conversations.id"]
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(index=True)
    role: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    intent: Mapped[Intent | None] = mapped_column(
        SAEnum(Intent, name="turn_intent", values_callable=lambda e: [m.value for m in e])
    )
    confidence: Mapped[float | None]
    responder: Mapped[str | None] = mapped_column(String(50))
    provider: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(100))
    latency_ms: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class TurnRetrieval(TenantScoped, Base):
    __tablename__ = "turn_retrievals"
    __table_args__ = (
        UniqueConstraint("turn_id", "rank"),
        ForeignKeyConstraint(["tenant_id", "turn_id"], ["turns.tenant_id", "turns.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "version_id", "chunk_id"],
            ["knowledge_chunks.tenant_id", "knowledge_chunks.version_id", "knowledge_chunks.id"],
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    turn_id: Mapped[uuid.UUID] = mapped_column(index=True)
    #: The version that was read, copied from what retrieval returned -- never resolved
    #: from the document afterwards, when it may no longer be the current one.
    version_id: Mapped[uuid.UUID] = mapped_column(index=True)
    chunk_id: Mapped[uuid.UUID]
    score: Mapped[float]
    rank: Mapped[int]
