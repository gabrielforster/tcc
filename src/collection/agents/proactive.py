"""The active (proactive) agent: decides whether and how to contact, and records why.

It replaces the stub of the same role. Its job is narrow on purpose:

1. take an overdue or risk event;
2. ask the rules engine whether contact is permitted right now;
3. if it is, choose a channel for the delinquency stage and emit `contact_scheduled`;
4. if it is not, emit `suppressed` carrying the reason and, where known, when the attempt
   would become allowed.

A blocked attempt produces an event rather than silence, so a customer never contacted
still has a record explaining why -- which is what makes the rules auditable rather than
merely present.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from collection.agents.base import Agent
from collection.agents.events import Event, EventType
from collection.data.sources.synthetic import delinquency_stage
from collection.rules.policy import ContactHistory, ContactPolicy

#: Channel by delinquency stage. Cheap and unobtrusive first, costly and direct last.
CHANNEL_BY_STAGE = {
    "pre_due": "whatsapp",
    "1-7": "whatsapp",
    "8-30": "whatsapp",
    "31-60": "phone",
    "60+": "phone",
}

HistoryLookup = Callable[[str, str | None], ContactHistory]


def _no_history(customer_id: str, invoice_id: str | None) -> ContactHistory:
    return ContactHistory()


class ProactiveAgent(Agent):
    name = "proactive"
    handles = frozenset({EventType.OVERDUE_DETECTED, EventType.RISK_DETECTED})

    def __init__(
        self,
        policy: ContactPolicy | None = None,
        history: HistoryLookup = _no_history,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.policy = policy or ContactPolicy()
        self.history = history
        self.clock = clock

    def handle(self, event: Event) -> list[Event]:
        now = self.clock()
        history = self.history(event.customer_id, event.invoice_id)
        decision = self.policy.evaluate(history, moment=now)

        if not decision.allowed:
            return [
                event.caused(
                    EventType.SUPPRESSED,
                    reason=decision.reason.value if decision.reason else "unknown",
                    detail=decision.detail,
                    retry_at=decision.retry_at.isoformat() if decision.retry_at else None,
                )
            ]

        days_late = int(event.payload.get("days_late", 0))
        stage = delinquency_stage(days_late)
        channel = CHANNEL_BY_STAGE.get(stage, "whatsapp")
        attempt = history.attempts + 1

        return [
            event.caused(
                EventType.CONTACT_SCHEDULED,
                channel=channel,
                delinquency_stage=stage,
                attempt_number=attempt,
                scheduled_for=now.isoformat(),
                expected_value=round(
                    self.policy.expected_value(
                        attempt, channel, float(event.payload.get("amount", 0.0))
                    ),
                    2,
                ),
            )
        ]
