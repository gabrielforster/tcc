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

The log is in-memory, like the session store, and shares its limits: it does not survive a
restart. It lives in its own module, not in the web adapter, because it is channel-neutral
and the voice channel reuses it.
"""

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock


class DeliveryStatus(StrEnum):
    """The ladder, lowest rung first. Names are the schedule's vocabulary, in Portuguese."""

    ENVIADA = "enviada"
    ENTREGUE = "entregue"
    LIDA = "lida"
    RESPONDIDA = "respondida"


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
