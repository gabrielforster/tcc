"""A runnable end-to-end trace through the orchestrator.

Exists so the architecture can be reviewed by watching it behave rather than by reading a
diagram: it submits a realistic mix of events -- an inbound message, two overdue
receivables, a risk signal from the predictive module, and one customer who has opted out
-- and reports what happened to each.
"""

from dataclasses import dataclass

from collection.agents.base import StubAgent
from collection.agents.events import Event, EventType
from collection.agents.orchestrator import Orchestrator


@dataclass
class DemoResult:
    trace: object
    orchestrator: Orchestrator


def build() -> Orchestrator:
    """The three agents of the architecture, still as stubs."""
    responsive = StubAgent(
        "responsive",
        frozenset({EventType.MESSAGE_RECEIVED, EventType.AUDIO_RECEIVED}),
        emits=EventType.REPLY_SENT,
    )
    proactive = StubAgent(
        "proactive",
        frozenset({EventType.OVERDUE_DETECTED, EventType.RISK_DETECTED}),
        emits=EventType.CONTACT_SENT,
    )
    predictive = StubAgent("predictive", frozenset({EventType.CONTACT_SCHEDULED}))
    return Orchestrator([responsive, proactive, predictive], opted_out={"cus_optout"})


def run() -> DemoResult:
    orchestrator = build()
    events = [
        Event(type=EventType.OVERDUE_DETECTED, customer_id="cus_a", invoice_id="inv_1"),
        Event(type=EventType.OVERDUE_DETECTED, customer_id="cus_optout", invoice_id="inv_2"),
        Event(
            type=EventType.RISK_DETECTED,
            customer_id="cus_b",
            invoice_id="inv_3",
            payload={"probability": 0.81, "priority": 1},
        ),
        # Arrives last, handled first.
        Event(
            type=EventType.MESSAGE_RECEIVED,
            customer_id="cus_c",
            payload={"text": "posso parcelar?"},
        ),
    ]
    orchestrator.submit_all(events)
    return DemoResult(trace=orchestrator.run(), orchestrator=orchestrator)
