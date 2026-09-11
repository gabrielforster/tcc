"""A runnable end-to-end trace through the orchestrator.

Exists so the architecture can be reviewed by watching it behave rather than by reading a
diagram: it submits a realistic mix of events -- an inbound message, two overdue
receivables, a risk signal from the predictive module, and one customer who has opted out
-- and reports what happened to each.
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from collection.agents.base import StubAgent
from collection.agents.events import Event, EventType
from collection.agents.orchestrator import Orchestrator
from collection.agents.proactive import ProactiveAgent
from collection.agents.responsive import ResponsiveAgent
from collection.config import settings
from collection.rag.documents import load_directory
from collection.rag.index import VectorIndex


@dataclass
class DemoResult:
    trace: object
    orchestrator: Orchestrator


def build() -> Orchestrator:
    """The three agents of the architecture, still as stubs."""
    # No longer a stub either: it classifies the message and answers from the index.
    index = VectorIndex().add_documents(load_directory(settings.dir_knowledge)).build()
    responsive = ResponsiveAgent(index=index)
    # No longer a stub: the rules engine decides whether each contact may happen.
    # A fixed clock inside business hours, so the demo reads the same whenever it is run.
    proactive = ProactiveAgent(
        clock=lambda: datetime(2026, 9, 10, 10, 30, tzinfo=ZoneInfo("America/Sao_Paulo"))
    )
    predictive = StubAgent("predictive", frozenset({EventType.CONTACT_SCHEDULED}))
    return Orchestrator([responsive, proactive, predictive], opted_out={"cus_optout"})


def run() -> DemoResult:
    orchestrator = build()
    events = [
        Event(
            type=EventType.OVERDUE_DETECTED,
            customer_id="cus_a",
            invoice_id="inv_1",
            payload={"days_late": 12, "amount": 1250.0},
        ),
        Event(
            type=EventType.OVERDUE_DETECTED,
            customer_id="cus_optout",
            invoice_id="inv_2",
            payload={"days_late": 45, "amount": 800.0},
        ),
        Event(
            type=EventType.RISK_DETECTED,
            customer_id="cus_b",
            invoice_id="inv_3",
            payload={"probability": 0.81, "priority": 1, "days_late": 38, "amount": 3400.0},
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
