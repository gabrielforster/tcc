"""The event contract between the orchestrator and the agents.

Every interaction in the system is an event. Fixing the vocabulary here, rather than
letting each agent invent its own payload, is what keeps the three modules replaceable:
the responsive agent can be rewritten around a different LLM, and the orchestrator does
not change.

Events are immutable. An agent never mutates what it received -- it emits a new event --
so the event log is a complete audit trail of why every contact happened, which the LGPD
requires us to be able to reconstruct and the paper needs as evidence.
"""

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any


class EventType(StrEnum):
    # Inbound: the customer started it.
    MESSAGE_RECEIVED = "message_received"
    AUDIO_RECEIVED = "audio_received"
    # Outbound: the system started it.
    OVERDUE_DETECTED = "overdue_detected"
    RISK_DETECTED = "risk_detected"
    CONTACT_SCHEDULED = "contact_scheduled"
    CONTACT_SENT = "contact_sent"
    # Results and terminal states.
    REPLY_SENT = "reply_sent"
    ESCALATED_TO_HUMAN = "escalated_to_human"
    OPT_OUT_REQUESTED = "opt_out_requested"
    SUPPRESSED = "suppressed"


class Priority(int, Enum):
    """Lower sorts first.

    A waiting customer always outranks an outbound campaign: someone who reached out is
    already engaged, and making them wait behind a batch job is both worse service and a
    worse outcome. This is the priority rule the architecture requires, encoded once.
    """

    RESPONSIVE = 0
    ESCALATION = 1
    PROACTIVE = 2


#: Which events are customer-initiated, and therefore jump the queue.
RESPONSIVE_EVENTS = frozenset(
    {EventType.MESSAGE_RECEIVED, EventType.AUDIO_RECEIVED, EventType.OPT_OUT_REQUESTED}
)


@dataclass(frozen=True)
class Event:
    type: EventType
    customer_id: str
    invoice_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    #: Set when this event was produced in response to another, for audit trails.
    caused_by: str | None = None

    @property
    def priority(self) -> Priority:
        if self.type in RESPONSIVE_EVENTS:
            return Priority.RESPONSIVE
        if self.type == EventType.ESCALATED_TO_HUMAN:
            return Priority.ESCALATION
        return Priority.PROACTIVE

    def caused(self, type: EventType, **payload: Any) -> "Event":
        """A new event caused by this one, carrying the causal link."""
        return Event(
            type=type,
            customer_id=self.customer_id,
            invoice_id=self.invoice_id,
            payload=payload,
            caused_by=self.event_id,
        )

    def with_payload(self, **extra: Any) -> "Event":
        return replace(self, payload={**self.payload, **extra})
