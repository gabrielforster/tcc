"""An outbound message and the highest rung of the ladder it has reached.

What each rung means, and what it does not claim, is written in
`collection.channels.delivery`; the row only has to avoid saying more. So each rung
stamps its own column: `sent_at` is the one the server measured, `delivered_at` and
`read_at` are when the *client reported* them, and `replied_at` is when the server saw the
customer's next turn. A single "updated at" would say where the ladder got to and hide
when each claim arrived, which is the difference between a report made a second after the
send and one made a day later. A rung that was skipped stays null, because nobody told us.

`status` and the timestamps are written together by `DatabaseDeliveryLog.advance`, which
is the only writer: nothing in the schema stops a hand-written UPDATE from moving a row
backwards, so the monotonic rule lives in one function rather than in a trigger that
would restate `_RANK` in SQL.

The row carries its own `tenant_id` and composite foreign keys to the conversation and
the turn, for the reason given in `customer.py`. `turn_id` is nullable: a message the
system seeds before the customer has said anything belongs to a conversation and to no
turn yet.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from collection.db.base import Base, TenantScoped
from collection.domain.delivery import DeliveryStatus


class Delivery(TenantScoped, Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"], ["conversations.tenant_id", "conversations.id"]
        ),
        ForeignKeyConstraint(["tenant_id", "turn_id"], ["turns.tenant_id", "turns.id"]),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(index=True)
    turn_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    status: Mapped[DeliveryStatus] = mapped_column(
        SAEnum(
            DeliveryStatus,
            name="delivery_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=DeliveryStatus.ENVIADA,
    )
    #: Opaque: whatever string the recording channel uses to say who may report on this row.
    owner: Mapped[str | None] = mapped_column(String(255))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
