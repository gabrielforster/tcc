"""The agent interface.

An agent receives an event and returns the events it produced. That is the whole contract.
Keeping it this narrow is what lets the orchestrator be tested against stubs, and lets each
agent be replaced -- an LLM behind the responsive agent, a rules engine behind the active
one -- without touching the coordination logic.
"""

from abc import ABC, abstractmethod

from collection.agents.events import Event, EventType


class Agent(ABC):
    name: str
    #: Event types this agent is willing to handle.
    handles: frozenset[EventType] = frozenset()

    def accepts(self, event: Event) -> bool:
        return event.type in self.handles

    @abstractmethod
    def handle(self, event: Event) -> list[Event]:
        """Process an event and return any events it produced."""


class StubAgent(Agent):
    """Records what it was given and emits a configurable follow-up.

    The architecture is delivered with agents that do nothing useful yet, so that the
    orchestration can be exercised and reviewed before the LLM, the rules engine and the
    telephony integration exist. Each of those replaces one stub.
    """

    def __init__(
        self,
        name: str,
        handles: frozenset[EventType],
        emits: EventType | None = None,
    ) -> None:
        self.name = name
        self.handles = handles
        self.emits = emits
        self.seen: list[Event] = []

    def handle(self, event: Event) -> list[Event]:
        self.seen.append(event)
        if self.emits is None:
            return []
        return [event.caused(self.emits, agent=self.name)]
