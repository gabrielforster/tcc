"""Multi-agent orchestration.

The two properties that matter here are the ones the architecture promises: a customer who
writes in is always served before an outbound campaign, and an opted-out customer cannot be
contacted by any agent. Both are enforced centrally, so both are tested centrally.
"""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from collection.agents.base import StubAgent
from collection.agents.events import Event, EventType, Priority
from collection.agents.orchestrator import MAX_CASCADE_DEPTH, Orchestrator
from collection.agents.queue import EventQueue


def make_event(type: EventType, customer: str = "cus_1", **payload) -> Event:
    return Event(type=type, customer_id=customer, payload=payload)


@pytest.fixture
def responsive():
    return StubAgent(
        "responsive",
        frozenset({EventType.MESSAGE_RECEIVED, EventType.AUDIO_RECEIVED}),
        emits=EventType.REPLY_SENT,
    )


@pytest.fixture
def proactive():
    return StubAgent(
        "proactive",
        frozenset({EventType.OVERDUE_DETECTED, EventType.RISK_DETECTED}),
        emits=EventType.CONTACT_SENT,
    )


# ---------------------------------------------------------------------------- priority
def test_a_waiting_customer_is_served_before_an_outbound_campaign():
    queue = EventQueue()
    # The campaign is queued first and is older; it still loses.
    older = Event(
        type=EventType.OVERDUE_DETECTED,
        customer_id="cus_1",
        created_at=datetime.now(UTC) - timedelta(hours=2),
    )
    queue.push(older)
    queue.push(make_event(EventType.MESSAGE_RECEIVED, "cus_2"))

    assert queue.pop().type == EventType.MESSAGE_RECEIVED
    assert queue.pop().type == EventType.OVERDUE_DETECTED


def test_within_a_priority_the_longest_wait_goes_first():
    queue = EventQueue()
    now = datetime.now(UTC)
    recent = Event(type=EventType.MESSAGE_RECEIVED, customer_id="b", created_at=now)
    older = Event(
        type=EventType.MESSAGE_RECEIVED, customer_id="a", created_at=now - timedelta(minutes=30)
    )
    queue.push(recent)
    queue.push(older)
    assert queue.pop().customer_id == "a"


def test_escalation_outranks_proactive_but_not_a_live_customer():
    assert Priority.RESPONSIVE < Priority.ESCALATION < Priority.PROACTIVE
    assert make_event(EventType.MESSAGE_RECEIVED).priority is Priority.RESPONSIVE
    assert make_event(EventType.ESCALATED_TO_HUMAN).priority is Priority.ESCALATION
    assert make_event(EventType.CONTACT_SCHEDULED).priority is Priority.PROACTIVE


def test_the_queue_reports_what_is_waiting():
    queue = EventQueue()
    queue.push(make_event(EventType.MESSAGE_RECEIVED))
    queue.push(make_event(EventType.OVERDUE_DETECTED))
    queue.push(make_event(EventType.RISK_DETECTED))
    assert queue.counts_by_priority() == {"responsive": 1, "escalation": 0, "proactive": 2}
    assert len(queue) == 3


def test_the_same_event_is_not_queued_twice():
    queue = EventQueue()
    event = make_event(EventType.MESSAGE_RECEIVED)
    assert queue.push(event) is True
    assert queue.push(event) is False
    assert len(queue) == 1


# ----------------------------------------------------------------------------- routing
def test_each_event_reaches_the_agent_that_handles_it(responsive, proactive):
    orchestrator = Orchestrator([responsive, proactive])
    orchestrator.submit(make_event(EventType.MESSAGE_RECEIVED))
    orchestrator.submit(make_event(EventType.OVERDUE_DETECTED))
    trace = orchestrator.run()

    assert [e.type for e in responsive.seen] == [EventType.MESSAGE_RECEIVED]
    assert [e.type for e in proactive.seen] == [EventType.OVERDUE_DETECTED]
    assert trace.counts()["processed"] == 2


def test_an_event_nobody_handles_is_recorded_rather_than_dropped_silently(responsive):
    orchestrator = Orchestrator([responsive])
    orchestrator.submit(make_event(EventType.CONTACT_SENT))
    trace = orchestrator.run()
    assert trace.counts()["unrouted"] == 1
    assert trace.counts()["processed"] == 0


def test_events_produced_by_an_agent_are_handled_in_the_same_run(responsive):
    """A reply that triggers an escalation must not wait for the next cycle."""
    escalation = StubAgent("human_handoff", frozenset({EventType.REPLY_SENT}))
    orchestrator = Orchestrator([responsive, escalation])
    orchestrator.submit(make_event(EventType.MESSAGE_RECEIVED))
    orchestrator.run()
    assert [e.type for e in escalation.seen] == [EventType.REPLY_SENT]


def test_produced_events_carry_the_causal_link_for_the_audit_trail(responsive):
    orchestrator = Orchestrator([responsive])
    original = make_event(EventType.MESSAGE_RECEIVED)
    orchestrator.submit(original)
    trace = orchestrator.run()
    assert trace.produced[0].caused_by == original.event_id
    assert trace.handled_by[original.event_id] == "responsive"


# ---------------------------------------------------------------------------- opt-out
def test_an_opted_out_customer_receives_no_outbound_contact(proactive):
    orchestrator = Orchestrator([proactive], opted_out={"cus_1"})
    orchestrator.submit(make_event(EventType.OVERDUE_DETECTED, "cus_1"))
    orchestrator.submit(make_event(EventType.OVERDUE_DETECTED, "cus_2"))
    trace = orchestrator.run()

    assert [e.customer_id for e in proactive.seen] == ["cus_2"]
    assert trace.counts()["suppressed"] == 1


def test_opt_out_is_enforced_before_any_agent_runs(proactive):
    """The guard must not depend on an agent remembering to check."""
    orchestrator = Orchestrator([proactive], opted_out={"cus_1"})
    orchestrator.submit(make_event(EventType.OVERDUE_DETECTED, "cus_1"))
    orchestrator.run()
    assert proactive.seen == []


def test_an_opted_out_customer_who_writes_in_is_still_answered(responsive, proactive):
    """Opt-out stops outbound collection; it is not a refusal of service."""
    orchestrator = Orchestrator([responsive, proactive], opted_out={"cus_1"})
    orchestrator.submit(make_event(EventType.MESSAGE_RECEIVED, "cus_1"))
    orchestrator.run()
    assert len(responsive.seen) == 1


def test_an_opt_out_request_suppresses_later_outbound_events_in_the_same_run():
    opt_out_agent = StubAgent(
        "responsive",
        frozenset({EventType.MESSAGE_RECEIVED}),
        emits=EventType.OPT_OUT_REQUESTED,
    )
    proactive = StubAgent("proactive", frozenset({EventType.OVERDUE_DETECTED}))
    orchestrator = Orchestrator([opt_out_agent, proactive])
    orchestrator.submit(make_event(EventType.MESSAGE_RECEIVED, "cus_1"))
    orchestrator.submit(make_event(EventType.OVERDUE_DETECTED, "cus_1"))
    orchestrator.run()
    # The message is handled first (responsive), registering the opt-out before the
    # outbound event is reached.
    assert proactive.seen == []
    assert "cus_1" in orchestrator.opted_out


# ------------------------------------------------------------------------- safeguards
def test_a_loop_between_two_agents_terminates():
    ping = StubAgent("ping", frozenset({EventType.CONTACT_SENT}), emits=EventType.REPLY_SENT)
    pong = StubAgent("pong", frozenset({EventType.REPLY_SENT}), emits=EventType.CONTACT_SENT)
    orchestrator = Orchestrator([ping, pong])
    orchestrator.submit(make_event(EventType.CONTACT_SENT))
    trace = orchestrator.run()
    assert len(trace.processed) <= MAX_CASCADE_DEPTH + 2


def test_a_run_can_be_capped_leaving_the_rest_queued():
    # An agent that emits nothing, so the queue holds only what was not yet processed.
    silent = StubAgent("proactive", frozenset({EventType.OVERDUE_DETECTED}))
    orchestrator = Orchestrator([silent])
    for i in range(5):
        orchestrator.submit(make_event(EventType.OVERDUE_DETECTED, f"cus_{i}"))
    trace = orchestrator.run(max_events=2)
    assert trace.counts()["processed"] == 2
    assert len(orchestrator.queue) == 3


def test_events_are_immutable_so_the_log_cannot_be_rewritten():
    event = make_event(EventType.MESSAGE_RECEIVED)
    with pytest.raises(FrozenInstanceError):
        event.customer_id = "someone_else"
    assert event.with_payload(note="x").payload["note"] == "x"
    assert "note" not in event.payload
