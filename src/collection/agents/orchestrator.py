"""Central orchestrator: routes events to agents and enforces the global rules.

Responsibilities, deliberately few:

1. **Route** each event to the agent that handles it.
2. **Enforce priority** -- responsive work before proactive, via the queue.
3. **Enforce opt-out**, before any agent sees the event. A customer who asked not to be
   contacted must not be reachable by a bug in an agent, so the check lives here rather
   than being repeated in each one (CDC art. 42 and the LGPD right to object).
4. **Record** every event, so the reason for each contact can be reconstructed.

Everything else -- what to say, whom to prioritise, which channel -- belongs to an agent.
The orchestrator stays this small so that the interesting behaviour is testable in
isolation and the coordination itself cannot hide a bug.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from collection.agents.base import Agent
from collection.agents.events import Event, EventType
from collection.agents.queue import EventQueue

#: Guards against an agent loop: A emits an event handled by B, which emits one for A.
MAX_CASCADE_DEPTH = 10


@dataclass
class Trace:
    """What happened during one run, for auditing and for the paper's latency figures."""

    processed: list[Event] = field(default_factory=list)
    produced: list[Event] = field(default_factory=list)
    suppressed: list[Event] = field(default_factory=list)
    unrouted: list[Event] = field(default_factory=list)
    handled_by: dict[str, str] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {
            "processed": len(self.processed),
            "produced": len(self.produced),
            "suppressed": len(self.suppressed),
            "unrouted": len(self.unrouted),
        }

    def by_type(self) -> dict[str, int]:
        return dict(Counter(event.type.value for event in self.processed))


class Orchestrator:
    def __init__(
        self,
        agents: Iterable[Agent],
        opted_out: set[str] | None = None,
        queue: EventQueue | None = None,
    ) -> None:
        self.agents = list(agents)
        self.queue = queue or EventQueue()
        self.opted_out = opted_out or set()
        self.trace = Trace()

    # ------------------------------------------------------------------ routing
    def route(self, event: Event) -> Agent | None:
        for agent in self.agents:
            if agent.accepts(event):
                return agent
        return None

    def submit(self, event: Event) -> bool:
        return self.queue.push(event)

    def submit_all(self, events: Iterable[Event]) -> int:
        return sum(1 for event in events if self.submit(event))

    # ------------------------------------------------------------------ running
    def run(self, max_events: int | None = None) -> Trace:
        """Drain the queue, routing each event to its agent.

        Events produced by an agent are queued, so a reply that triggers an escalation is
        handled in the same run -- but always behind anything more urgent already waiting.
        """
        processed = 0
        while (event := self.queue.pop()) is not None:
            if max_events is not None and processed >= max_events:
                self.queue.push(event)
                break

            if self._suppressed(event):
                continue

            agent = self.route(event)
            if agent is None:
                self.trace.unrouted.append(event)
                continue

            self.trace.processed.append(event)
            self.trace.handled_by[event.event_id] = agent.name
            processed += 1

            for produced in agent.handle(event):
                self.trace.produced.append(produced)
                if self._depth(produced) > MAX_CASCADE_DEPTH:
                    continue
                if produced.type == EventType.OPT_OUT_REQUESTED:
                    self.opted_out.add(produced.customer_id)
                self.queue.push(produced)

        return self.trace

    # ------------------------------------------------------------------- guards
    def _suppressed(self, event: Event) -> bool:
        """Opt-out is checked before any agent runs, for every outbound event."""
        if event.customer_id not in self.opted_out:
            return False
        if event.priority.name == "RESPONSIVE":
            # An opted-out customer who writes to us is still answered: opt-out stops
            # outbound collection, it does not refuse service.
            return False
        self.trace.suppressed.append(event)
        return True

    def _depth(self, event: Event) -> int:
        depth = 0
        seen = {e.event_id: e for e in self.trace.processed + self.trace.produced}
        current = event
        while current.caused_by and current.caused_by in seen and depth <= MAX_CASCADE_DEPTH:
            current = seen[current.caused_by]
            depth += 1
        return depth
