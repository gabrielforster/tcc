"""The active (proactive) agent: decides whether and how to contact, and records why.

It replaces the stub of the same role. Its job is narrow on purpose:

1. take an overdue or risk event;
2. ask the rules engine whether contact is permitted right now;
3. if it is, choose a channel for the delinquency stage, **write the message**, check it,
   and emit `contact_scheduled` carrying the text that would be sent;
4. if it is not, emit `suppressed` carrying the reason and, where known, when the attempt
   would become allowed.

A blocked attempt produces an event rather than silence, so a customer never contacted
still has a record explaining why -- which is what makes the rules auditable rather than
merely present.

Step 3 has a gate of its own. A composed message is checked against its brief
(`outreach/compliance.py`) *before* it is scheduled, and a message that fails goes to a
person as `escalated_to_human` instead of going out. Putting the check here rather than in
the composer is deliberate: the composer is the thing under test, and a component that
grades its own output cannot be compared against an alternative.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from collection.agents.base import Agent
from collection.agents.events import Event, EventType
from collection.data.sources.synthetic import delinquency_stage
from collection.outreach.brief import Brief
from collection.outreach.compliance import check
from collection.outreach.compose import Composer, TemplateComposer
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
        composer: Composer | None = None,
    ) -> None:
        self.policy = policy or ContactPolicy()
        self.history = history
        self.clock = clock
        # The template composer by default: the arm that needs no API key, so the demo,
        # the tests and CI all exercise the whole path without one.
        self.composer = composer or TemplateComposer()

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
        amount = float(event.payload.get("amount", 0.0))

        brief = Brief(
            customer_id=event.customer_id,
            invoice_id=event.invoice_id,
            amount=amount,
            days_late=days_late,
            customer_name=str(event.payload.get("customer_name", "")),
            attempt=attempt,
            channel=channel,
        )
        message = self.composer.compose(brief)
        compliance = check(message, brief)

        if not compliance.sendable:
            return [
                event.caused(
                    EventType.ESCALATED_TO_HUMAN,
                    reason="message_not_compliant",
                    detail="; ".join(compliance.violations),
                    composer=message.composer,
                    delinquency_stage=stage,
                    channel=channel,
                )
            ]

        return [
            event.caused(
                EventType.CONTACT_SCHEDULED,
                channel=channel,
                delinquency_stage=stage,
                attempt_number=attempt,
                scheduled_for=now.isoformat(),
                message=message.text,
                composer=message.composer,
                message_chars=compliance.length,
                expected_value=round(
                    self.policy.expected_value(attempt, channel, amount),
                    2,
                ),
            )
        ]
