"""Collection rules.

Two kinds of rule are tested differently on purpose. The legal ones (permitted hours,
opt-out, frequency ceiling) are asserted as absolutes — they are not ours to trade off
against effectiveness. The empirical ones (attempt cap, cooldown) are asserted as
configurable defaults, because they are exactly what the controlled experiment varies.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from collection.agents.events import Event, EventType
from collection.agents.proactive import ProactiveAgent
from collection.rules.policy import (
    ContactHistory,
    ContactPolicy,
    DenialReason,
    response_rate_for_attempt,
)
from collection.rules.window import ContactWindow

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def local(year=2026, month=9, day=10, hour=10, minute=0) -> datetime:
    """A Thursday at 10:00 local time, comfortably inside the permitted window."""
    return datetime(year, month, day, hour, minute, tzinfo=SAO_PAULO)


# ------------------------------------------------------------------- permitted hours
def test_contact_is_allowed_during_weekday_business_hours():
    assert ContactWindow().allows(local(hour=10))
    assert ContactWindow().allows(local(hour=19, minute=59))


def test_contact_is_refused_before_eight_and_from_eight_in_the_evening():
    window = ContactWindow()
    assert not window.allows(local(hour=7, minute=59))
    assert not window.allows(local(hour=20))
    assert not window.allows(local(hour=23))


def test_permitted_hours_are_wall_clock_local_not_utc():
    """08:00 UTC is 05:00 in Sao Paulo; evaluating in UTC would permit a 5am call."""
    window = ContactWindow()
    assert not window.allows(datetime(2026, 9, 10, 8, 0, tzinfo=UTC))
    assert window.allows(datetime(2026, 9, 10, 13, 0, tzinfo=UTC))


def test_saturday_closes_early_and_sunday_is_closed():
    window = ContactWindow()
    assert window.allows(local(day=12, hour=10))  # Saturday morning
    assert not window.allows(local(day=12, hour=15))  # Saturday afternoon
    assert not window.allows(local(day=13, hour=10))  # Sunday


def test_holidays_are_injected_rather_than_assumed():
    """The relevant holiday set is municipal as well as national."""
    window = ContactWindow(holidays=frozenset({local().date()}))
    assert not window.allows(local(hour=10))


def test_the_next_opening_is_reported_so_the_attempt_can_be_rescheduled():
    window = ContactWindow()
    opening = window.next_opening(local(hour=22))
    assert opening.date() == local(day=11).date()
    assert opening.hour == 8


def test_the_next_opening_skips_a_closed_sunday():
    window = ContactWindow()
    opening = window.next_opening(local(day=13, hour=10))  # Sunday
    assert opening.date() == local(day=14).date()  # Monday
    assert opening.hour == 8


# -------------------------------------------------------------------- legal blocks
def test_an_opted_out_customer_is_never_contacted():
    decision = ContactPolicy().evaluate(ContactHistory(opted_out=True), local())
    assert not decision
    assert decision.reason is DenialReason.OPTED_OUT


def test_a_disputed_debt_goes_to_a_human_not_back_into_the_campaign():
    decision = ContactPolicy().evaluate(ContactHistory(disputed=True), local())
    assert decision.reason is DenialReason.DISPUTED


def test_the_weekly_ceiling_blocks_further_contact():
    policy = ContactPolicy(max_contacts_per_week=3)
    decision = policy.evaluate(ContactHistory(contacts_this_week=3), local())
    assert decision.reason is DenialReason.WEEKLY_CAP


def test_contact_outside_permitted_hours_reports_when_it_becomes_allowed():
    decision = ContactPolicy().evaluate(ContactHistory(), local(hour=21))
    assert decision.reason is DenialReason.OUTSIDE_WINDOW
    assert decision.retry_at is not None
    assert decision.retry_at.hour == 8


# ---------------------------------------------------------------- empirical limits
def test_the_attempt_cap_stops_contact_once_returns_have_collapsed():
    policy = ContactPolicy(max_attempts=4)
    assert policy.evaluate(ContactHistory(attempts=3), local())
    blocked = policy.evaluate(ContactHistory(attempts=4), local())
    assert blocked.reason is DenialReason.MAX_ATTEMPTS


def test_the_attempt_cap_is_configurable_because_the_experiment_varies_it():
    history = ContactHistory(attempts=4)
    assert not ContactPolicy(max_attempts=4).evaluate(history, local())
    assert ContactPolicy(max_attempts=6).evaluate(history, local())


def test_the_cooldown_spreads_attempts_rather_than_stacking_them():
    policy = ContactPolicy(cooldown_hours=48)
    recent = ContactHistory(last_contact_at=local() - timedelta(hours=10))
    decision = policy.evaluate(recent, local())
    assert decision.reason is DenialReason.COOLDOWN
    assert decision.retry_at is not None

    settled = ContactHistory(last_contact_at=local() - timedelta(hours=49))
    assert policy.evaluate(settled, local())


def test_a_promise_to_pay_pauses_contact():
    """Chasing someone who just committed to a date is how the commitment is lost."""
    policy = ContactPolicy(promise_grace_days=5)
    promised = ContactHistory(promised_at=local() - timedelta(days=2))
    decision = policy.evaluate(promised, local())
    assert decision.reason is DenialReason.PROMISE_GRACE
    assert decision.retry_at is not None

    expired = ContactHistory(promised_at=local() - timedelta(days=6))
    assert policy.evaluate(expired, local())


def test_legal_blocks_are_reported_before_empirical_ones():
    """An opted-out customer is refused for being opted out, not for the attempt count."""
    history = ContactHistory(opted_out=True, attempts=99, contacts_this_week=99)
    assert ContactPolicy().evaluate(history, local()).reason is DenialReason.OPTED_OUT


# ------------------------------------------------------------------ expected value
def test_the_response_rate_curve_matches_the_measured_data():
    assert response_rate_for_attempt(1) == pytest.approx(0.130)
    assert response_rate_for_attempt(6) == pytest.approx(0.055)
    # Beyond the measured range the last observed value is carried, not extrapolated down.
    assert response_rate_for_attempt(9) == pytest.approx(0.055)


def test_expected_value_falls_with_each_attempt():
    policy = ContactPolicy()
    first = policy.expected_value(1, "whatsapp", 1000.0)
    sixth = policy.expected_value(6, "whatsapp", 1000.0)
    assert first > sixth


def test_an_expensive_channel_needs_a_larger_debt_to_be_worth_it():
    policy = ContactPolicy()
    assert policy.expected_value(1, "phone", 5.0) < 0
    assert policy.expected_value(1, "whatsapp", 5.0) > 0


# -------------------------------------------------------------------- the agent
def _event(days_late: int = 12, amount: float = 1250.0, customer: str = "cus_1") -> Event:
    return Event(
        type=EventType.OVERDUE_DETECTED,
        customer_id=customer,
        invoice_id="inv_1",
        payload={"days_late": days_late, "amount": amount},
    )


def test_an_allowed_contact_is_scheduled_with_a_channel_for_its_stage():
    agent = ProactiveAgent(clock=local)
    produced = agent.handle(_event(days_late=12))[0]
    assert produced.type is EventType.CONTACT_SCHEDULED
    assert produced.payload["delinquency_stage"] == "8-30"
    assert produced.payload["channel"] == "whatsapp"
    assert produced.payload["attempt_number"] == 1


def test_a_deeply_overdue_receivable_escalates_to_the_phone():
    agent = ProactiveAgent(clock=local)
    produced = agent.handle(_event(days_late=45))[0]
    assert produced.payload["delinquency_stage"] == "31-60"
    assert produced.payload["channel"] == "phone"


def test_a_blocked_contact_produces_a_record_rather_than_silence():
    """A customer never contacted must still have a record explaining why."""
    agent = ProactiveAgent(
        history=lambda customer, invoice: ContactHistory(opted_out=True), clock=local
    )
    produced = agent.handle(_event())[0]
    assert produced.type is EventType.SUPPRESSED
    assert produced.payload["reason"] == DenialReason.OPTED_OUT.value
    assert produced.payload["detail"]


def test_a_suppressed_contact_carries_when_it_could_be_retried():
    agent = ProactiveAgent(clock=lambda: local(hour=22))
    produced = agent.handle(_event())[0]
    assert produced.payload["reason"] == DenialReason.OUTSIDE_WINDOW.value
    assert produced.payload["retry_at"] is not None


def test_the_scheduled_contact_carries_its_expected_value():
    agent = ProactiveAgent(clock=local)
    produced = agent.handle(_event(amount=1000.0))[0]
    assert produced.payload["expected_value"] == pytest.approx(0.130 * 1000 - 0.12, rel=1e-3)


def test_every_produced_event_keeps_the_causal_link():
    agent = ProactiveAgent(clock=local)
    event = _event()
    assert agent.handle(event)[0].caused_by == event.event_id
