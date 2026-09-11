"""Priority queue for events awaiting an agent.

In-memory and synchronous on purpose: the queue's job in this work is to make the
scheduling policy explicit and testable, not to be production infrastructure. Swapping it
for Redis or SQS means implementing `push`/`pop` -- the policy lives here, not in the
transport.

Ordering is (priority, created_at): responsive work first, and within a priority the
oldest waits least. Ties break on insertion order so behaviour stays deterministic under
test.
"""

import heapq
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import count

from collection.agents.events import Event, Priority


@dataclass(order=True)
class _Entry:
    priority: int
    timestamp: float
    sequence: int
    event: Event = field(compare=False)


class EventQueue:
    def __init__(self) -> None:
        self._heap: list[_Entry] = []
        self._counter = count()
        self._seen: set[str] = set()

    def push(self, event: Event) -> bool:
        """Enqueue an event. Returns False if this exact event is already queued."""
        if event.event_id in self._seen:
            return False
        self._seen.add(event.event_id)
        heapq.heappush(
            self._heap,
            _Entry(
                priority=int(event.priority),
                timestamp=event.created_at.timestamp(),
                sequence=next(self._counter),
                event=event,
            ),
        )
        return True

    def pop(self) -> Event | None:
        if not self._heap:
            return None
        entry = heapq.heappop(self._heap)
        self._seen.discard(entry.event.event_id)
        return entry.event

    def drain(self) -> Iterator[Event]:
        while (event := self.pop()) is not None:
            yield event

    def peek(self) -> Event | None:
        return self._heap[0].event if self._heap else None

    def __len__(self) -> int:
        return len(self._heap)

    def counts_by_priority(self) -> dict[str, int]:
        counts = {p.name.lower(): 0 for p in Priority}
        for entry in self._heap:
            counts[Priority(entry.priority).name.lower()] += 1
        return counts
