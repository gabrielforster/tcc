"""The wiring that turns a scheduled contact into a thread waiting for the customer.

The proactive agent decides *whether* to contact someone, on which channel and with which
words; `ChatChannel.seed` puts the result where the widget will find it. Nothing joined
those two halves, so the behaviour existed only in tests. This module is the join, and
`collection seed` is the command that runs it.

It is deliberately thin. Every decision here already belongs to something else: the rules
engine says whether the contact is permitted, the composer writes the message, the
compliance check refuses one it would not send, the channel decides a contact for another
channel is not its to deliver, and both orchestrators -- the one here and the one inside
`seed` -- enforce opt-out. What this module adds is the sequence and a count of what
happened to each contact, because a run that reports only its successes is not evidence.

**The receivables are a fixture, not a query.** There is no job in this repository that
reads overdue invoices out of a dataset: the pipeline writes datasets to `data/`, which is
never committed, so a command that depended on one would not run on a fresh clone. The
sample below is therefore written out in full, in the same spirit as `agents/demo.py`, and
chosen to exercise the outcomes rather than to flatter them -- one contact that is seeded,
one that belongs to the voice channel, one customer who has opted out. Replacing it with a
real query is a change to this list and nothing else.
"""

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from collection.agents.events import Event, EventType
from collection.agents.orchestrator import Orchestrator
from collection.agents.proactive import ProactiveAgent
from collection.channels.web import ChatChannel
from collection.config import settings
from collection.rag.documents import load_directory
from collection.rag.index import VectorIndex

#: Inside business hours and on a weekday, so the rules engine's time window is not what
#: the command happens to be demonstrating. Fixed, so the output reads the same whenever
#: it is run.
DEMO_CLOCK = datetime(2026, 9, 10, 10, 30, tzinfo=ZoneInfo("America/Sao_Paulo"))

#: Customers who asked not to be contacted. One of them is overdue below, on purpose.
OPTED_OUT = frozenset({"cus_optout"})


def overdue() -> list[Event]:
    """The receivables a detection job would hand the proactive agent."""
    return [
        Event(
            type=EventType.OVERDUE_DETECTED,
            customer_id="cus_a",
            invoice_id="inv_1",
            payload={"days_late": 12, "amount": 1250.0, "customer_name": "Marina"},
        ),
        Event(
            type=EventType.OVERDUE_DETECTED,
            customer_id="cus_b",
            invoice_id="inv_2",
            payload={"days_late": 3, "amount": 420.5, "customer_name": "Joaquim"},
        ),
        # Stage 31-60 routes to the voice channel, which the chat channel must decline
        # rather than quietly deliver.
        Event(
            type=EventType.OVERDUE_DETECTED,
            customer_id="cus_c",
            invoice_id="inv_3",
            payload={"days_late": 45, "amount": 3400.0, "customer_name": "Alice"},
        ),
        # Opted out: suppressed before the proactive agent ever runs.
        Event(
            type=EventType.OVERDUE_DETECTED,
            customer_id="cus_optout",
            invoice_id="inv_4",
            payload={"days_late": 20, "amount": 800.0, "customer_name": "Rui"},
        ),
    ]


@dataclass(frozen=True)
class SeedResult:
    """What happened to each detected receivable, counted by outcome.

    The four lists partition the contacts the run considered, so a reader can check that
    nothing fell between them -- `total` exists for exactly that.
    """

    #: `(contact_scheduled event, delivery id)` for each message now waiting in a mailbox.
    seeded: list[tuple[Event, str]] = field(default_factory=list)
    #: Blocked before delivery: by an opt-out, or by the rules engine's own decision.
    suppressed: list[Event] = field(default_factory=list)
    #: Scheduled for a channel this one does not serve.
    other_channel: list[Event] = field(default_factory=list)
    #: The composed message failed its compliance check and went to a person instead.
    escalated: list[Event] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            len(self.seeded) + len(self.suppressed) + len(self.other_channel) + len(self.escalated)
        )


def run(channel: ChatChannel | None = None, events: list[Event] | None = None) -> SeedResult:
    """Run the proactive agent over overdue receivables and seed what it schedules.

    Returns the channel's own view of what it accepted and declined. The channel is
    returned to the caller through the object it was given, so a CLI can open a session on
    the same channel afterwards and see the thread.
    """
    if channel is None:
        index = VectorIndex().add_documents(load_directory(settings.dir_knowledge)).build()
        # The channel and the orchestrator below share one opted-out set, so an opt-out
        # recorded in a chat thread suppresses a contact scheduled in the same process.
        channel = ChatChannel(index=index, opted_out=set(OPTED_OUT))

    agent = ProactiveAgent(clock=lambda: DEMO_CLOCK)
    orchestrator = Orchestrator(agents=[agent], opted_out=channel.opted_out)
    orchestrator.submit_all(events if events is not None else overdue())
    trace = orchestrator.run()

    result = SeedResult(suppressed=list(trace.suppressed))
    before = len(channel.skipped)
    for produced in trace.produced:
        if produced.type is EventType.ESCALATED_TO_HUMAN:
            result.escalated.append(produced)
        elif produced.type is EventType.SUPPRESSED:
            # The rules engine declined: a reason, not a silence.
            result.suppressed.append(produced)
        elif produced.type is EventType.CONTACT_SCHEDULED:
            delivery_id = channel.seed(produced)
            if delivery_id is not None:
                result.seeded.append((produced, delivery_id))

    # Why the channel declined is the channel's record, not a second guess at it here.
    for reason, event in channel.skipped[before:]:
        if reason == "other_channel":
            result.other_channel.append(event)
        else:
            result.suppressed.append(event)
    return result
