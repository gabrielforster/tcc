"""What happened to an outbound message, as far as this system can honestly say.

The collection schedule tracks every outbound message as `enviada / entregue / lida /
respondida`. The original plan was to take those states from the WhatsApp Business API,
whose webhooks carry real carrier receipts. That integration was dropped, so the states
have to be modelled here -- and the point of this docstring is to say precisely what each
one means, because a status column is easy to read as a stronger claim than it is.

* `ENVIADA` -- the system handed the message to the channel and recorded it. Measured, and
  the only state that needs no one's cooperation. It does not mean anyone saw it.
* `ENTREGUE` and `LIDA` -- **reported by the client**, not observed by the server. The
  widget reports that it rendered the message (`ENTREGUE`) and that it was on screen
  (`LIDA`), through the delivery endpoint in `api/chat.py`, which accepts a report only
  from the session that owns the record.
  These are not carrier receipts: nothing here has independent evidence the message
  reached a person, and a client can lie, be scripted, or simply never report -- a closed
  tab sends nothing. So `ENVIADA` is not "delivery failed" and a missing `LIDA` is not
  "unread"; both are "we were not told". Do not cite these two states as proof of notice.
  Nothing here guesses them on a timer either: a simulated receipt would be a number that
  looks measured and is not.
* `RESPONDIDA` -- set by the server when the customer's next turn arrives in the same
  thread. This is the one transition the server can observe first-hand, and it is the
  strongest signal in the ladder: a reply is evidence the message was seen even if the
  client never reported it. Precisely, it means a turn arrived after the message was
  delivered to the session -- not that the message was displayed, read, understood or
  agreed with. A client that failed to draw the message still lets the next turn advance
  it.

The ladder is monotonic. A status never moves backwards, and trying to raises
`BackwardsStatus` (a `ValueError` that carries the current and attempted status, so a
caller can tell a late report from a nonsense one): a late `ENTREGUE` report arriving
after `LIDA` is a client bug or a replay, and silently accepting it would let the record
contradict itself. Repeating the current status is a harmless no-op, since clients
retry. Rungs may be skipped -- a client that reports only `LIDA`, or a customer who
replies before any report arrives, is normal.

An unknown delivery id raises `KeyError` rather than creating a record: a status update
for a message nobody sent is a bug in the caller, and conjuring the record would turn it
into a fabricated history.

`DeliveryLog` is in-memory, like the session store, and shares its limits: it does not
survive a restart. It stays because the tests and the demo run without a database.
`DatabaseDeliveryLog` is the same contract on rows, so a status survives the process.
Both compare the one `_RANK`: the monotonic rule is defined once, in Python, and the
database version does not restate it in SQL, where the two could drift apart.
`ConversationDeliveries` is the adapter that lets a channel written against the in-memory
log drive the rows without knowing it, by resolving the owner of a message to the
conversation it belongs to.

Two reports can race for one row -- a client retrying `LIDA` while the customer's reply
sets `RESPONDIDA` -- which the dictionary never had to face. `DatabaseDeliveryLog.advance`
therefore reads the row `FOR UPDATE` before comparing ranks, so the second writer waits for
the first and then judges against what it committed. A late report that loses the race is
refused with `BackwardsStatus`, exactly as it would be had it arrived afterwards. The lock
lasts until the caller's transaction ends, so a caller should commit promptly.

The module lives apart from the web adapter because it is channel-neutral and the voice
channel reuses it.
"""

import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import TYPE_CHECKING

from collection.domain.delivery import DeliveryStatus

if TYPE_CHECKING:
    from collection.channels.session import DatabaseSessionStore
    from collection.db.models.conversation import Conversation, Turn
    from collection.db.models.delivery import Delivery
    from collection.db.repository import TenantScope


class BackwardsStatus(ValueError):
    """An attempt to move a message down the ladder.

    Carries both statuses. A late `ENTREGUE` after the customer already replied is a normal
    race and the caller may treat it as benign; a report that contradicts the record in
    any other way is not. Telling them apart is the caller's decision, so the facts are here.
    """

    def __init__(self, delivery_id: str, current: "DeliveryStatus", attempted: "DeliveryStatus"):
        super().__init__(
            f"{delivery_id} is already {current.value}; it cannot go back to {attempted.value}"
        )
        self.delivery_id = delivery_id
        self.current = current
        self.attempted = attempted


_RANK = {status: rank for rank, status in enumerate(DeliveryStatus)}


@dataclass(frozen=True)
class DeliveryRecord:
    """One outbound message and the highest rung it has reached.

    `at` is when it reached that rung (the send time for `ENVIADA`), not when it was
    created, so the record reads as "became `lida` at ...".
    """

    id: str
    status: DeliveryStatus
    at: float
    #: Opaque: whatever string the recording channel uses to say who may report on this record.
    owner: str | None = None


class DeliveryLog:
    """Outbound messages and their status, advanced one monotonic step at a time."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._records: dict[str, DeliveryRecord] = {}
        self._lock = Lock()

    def record(self, text: str, owner: str | None = None) -> DeliveryRecord:
        """Register a message as sent. The text is not kept: the log tracks status only."""
        record = DeliveryRecord(
            id=f"dlv_{secrets.token_hex(8)}",
            status=DeliveryStatus.ENVIADA,
            at=self._clock(),
            owner=owner,
        )
        with self._lock:
            self._records[record.id] = record
        return record

    def advance(self, delivery_id: str, status: DeliveryStatus) -> DeliveryRecord:
        """Move a message up the ladder; raise `BackwardsStatus` (a `ValueError`) on a step back.

        Catch `BackwardsStatus` for the late-report case; it carries both statuses.
        """
        with self._lock:
            current = self._records[delivery_id]
            if _RANK[status] < _RANK[current.status]:
                raise BackwardsStatus(delivery_id, current.status, status)
            if status is current.status:
                return current
            updated = DeliveryRecord(
                id=delivery_id, status=status, at=self._clock(), owner=current.owner
            )
            self._records[delivery_id] = updated
            return updated

    def reassign(self, delivery_id: str, owner: str | None) -> DeliveryRecord:
        """Hand a record to a new owner; raise `KeyError` for an id this log never issued."""
        with self._lock:
            current = self._records[delivery_id]
            updated = DeliveryRecord(
                id=delivery_id, status=current.status, at=current.at, owner=owner
            )
            self._records[delivery_id] = updated
            return updated

    def of(self, delivery_id: str) -> DeliveryRecord:
        """The current record; raise `KeyError` for an id this log never issued."""
        with self._lock:
            return self._records[delivery_id]


#: The column each rung stamps. `ENVIADA` is stamped by `record`, never by `advance`.
_STAMP = {
    DeliveryStatus.ENVIADA: "sent_at",
    DeliveryStatus.ENTREGUE: "delivered_at",
    DeliveryStatus.LIDA: "read_at",
    DeliveryStatus.RESPONDIDA: "replied_at",
}


class DatabaseDeliveryLog:
    """`DeliveryLog`'s contract on rows, within one tenant.

    The model imports are inside the methods so that importing this module, for the
    in-memory log, does not load the database layer.

    The caller owns the transaction, as with every other write through a `TenantScope`:
    nothing here commits.
    """

    def __init__(self, scope: "TenantScope", clock: Callable[[], float] = time.time) -> None:
        self._scope = scope
        self._clock = clock

    def record(
        self,
        text: str,
        owner: str | None = None,
        *,
        conversation: "Conversation",
        turn: "Turn | None" = None,
    ) -> DeliveryRecord:
        """Register a message as sent. The text is not kept: the log tracks status only.

        A row has to belong to a conversation, which the in-memory log never needed, so
        the conversation is required and the turn it is tied to is optional.
        """
        from collection.db.models.delivery import Delivery

        row = Delivery(
            conversation_id=conversation.id,
            turn_id=turn.id if turn is not None else None,
            status=DeliveryStatus.ENVIADA,
            owner=owner,
            sent_at=self._now(),
        )
        self._scope.add(row)
        self._scope.session.flush()
        return self._record(row)

    def advance(self, delivery_id: str, status: DeliveryStatus) -> DeliveryRecord:
        """Move a message up the ladder; raise `BackwardsStatus` (a `ValueError`) on a step back."""
        row = self._lock(delivery_id)
        if _RANK[status] < _RANK[row.status]:
            raise BackwardsStatus(delivery_id, row.status, status)
        if status is row.status:
            return self._record(row)
        row.status = status
        setattr(row, _STAMP[status], self._now())
        self._scope.session.flush()
        return self._record(row)

    def get(self, delivery_id: str) -> DeliveryRecord:
        """The current record; raise `KeyError` for an id this tenant never issued."""
        return self._record(self._find(delivery_id))

    def reassign(
        self, delivery_id: str, owner: str | None, *, conversation: "Conversation | None" = None
    ) -> DeliveryRecord:
        """Hand a record to a new owner; raise `KeyError` for an id this tenant never issued.

        A row belongs to a conversation as well as an owner, and the two move together: a
        message seeded for a customer is re-owned by the session that finally shows it, and
        it was shown in that session's thread, not in the one it was recorded against. The
        thread it leaves is closed if the message was the only thing in it, so a thread
        opened to hold a contact nobody has read yet does not linger open once it has.
        """
        row = self._find(delivery_id)
        vacated = row.conversation_id
        row.owner = owner
        if conversation is not None:
            row.conversation_id = conversation.id
        self._scope.session.flush()
        if conversation is not None and vacated != conversation.id:
            self._close_if_empty(vacated)
        return self._record(row)

    def _close_if_empty(self, conversation_id: uuid.UUID) -> None:
        """Close a thread that holds no turn and no delivery; leave any other alone."""
        from collection.db.models.conversation import Conversation, ConversationState
        from collection.db.models.conversation import Turn as TurnRow
        from collection.db.models.delivery import Delivery

        conversation = self._scope.get(Conversation, conversation_id)
        if conversation is None or conversation.state is not ConversationState.OPEN:
            return
        if self._scope.all(TurnRow, TurnRow.conversation_id == conversation_id):
            return
        if self._scope.all(Delivery, Delivery.conversation_id == conversation_id):
            return
        conversation.state = ConversationState.CLOSED
        conversation.closed_at = datetime.now(UTC)
        self._scope.session.flush()

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), UTC)

    def _find(self, delivery_id: str, *, lock: bool = False) -> "Delivery":
        from collection.db.models.delivery import Delivery

        try:
            key = uuid.UUID(delivery_id)
        except ValueError:
            raise KeyError(delivery_id) from None
        query = self._scope.select(Delivery).where(Delivery.id == key)
        # `populate_existing` because a waiter that was blocked on the lock must see what
        # the winner committed, not the row its session loaded before it waited.
        query = query.with_for_update().execution_options(populate_existing=True) if lock else query
        row = self._scope.session.scalars(query).first()
        if row is None:
            raise KeyError(delivery_id)
        return row

    def _lock(self, delivery_id: str) -> "Delivery":
        return self._find(delivery_id, lock=True)

    @staticmethod
    def _record(row: "Delivery") -> DeliveryRecord:
        stamp = getattr(row, _STAMP[row.status])
        return DeliveryRecord(
            id=str(row.id), status=row.status, at=stamp.timestamp(), owner=row.owner
        )


class ConversationDeliveries:
    """`DeliveryLog`'s contract for a channel, backed by `DatabaseDeliveryLog`'s rows.

    `ChatChannel` was written against the in-memory log: an opaque owner string, and
    `record`, `advance`, `reassign` and `of`. A row needs one thing the channel never had
    to supply -- the conversation the message belongs to -- and only the session store can
    turn an owner into one. So the translation lives here: the channel must not learn that
    there is a database, and `DatabaseDeliveryLog` must not learn that there are sessions.

    Each call is one short unit of work on the store's scope, because `advance` holds a
    `FOR UPDATE` lock on the row until the transaction ends. See `DatabaseSessionStore`.
    """

    def __init__(self, log: DatabaseDeliveryLog, store: "DatabaseSessionStore") -> None:
        self._log = log
        self._store = store

    def record(self, text: str, owner: str | None = None) -> DeliveryRecord:
        """Register a message as sent, in the conversation its owner is talking in.

        The owner is required here, unlike in memory: a row with no owner has no
        conversation either, and a delivery belonging to no thread is not a record of
        anything.
        """
        if not owner:
            raise ValueError("a recorded delivery needs an owner: a row belongs to a conversation")
        with self._store.unit():
            return self._log.record(
                text, owner=owner, conversation=self._store.conversation_of(owner)
            )

    def advance(self, delivery_id: str, status: DeliveryStatus) -> DeliveryRecord:
        with self._store.unit():
            return self._log.advance(delivery_id, status)

    def reassign(self, delivery_id: str, owner: str | None) -> DeliveryRecord:
        with self._store.unit():
            conversation = self._store.conversation_of(owner) if owner else None
            return self._log.reassign(delivery_id, owner, conversation=conversation)

    def of(self, delivery_id: str) -> DeliveryRecord:
        with self._store.unit():
            return self._log.get(delivery_id)
