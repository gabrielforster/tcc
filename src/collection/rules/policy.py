"""Whether a specific contact attempt is allowed, and why not when it is not.

Every rule here is either a legal constraint or an empirical one, and the difference
matters: a legal limit is not ours to tune, an empirical one is a parameter the experiment
can vary.

**Legal** (CDC art. 42 and the superendividamento amendments: collection must not expose,
threaten or harass; the LGPD right to object):

* contact only inside the permitted hours (see `ContactWindow`);
* opt-out is absolute for outbound contact;
* a frequency ceiling, because repeated contact is the form harassment takes in practice.

**Empirical**, grounded in the Bank Marketing data (`docs/eda-bank-marketing.md`): across
41,188 real outbound attempts, response rate falls from 13.0% on the first attempt to 5.5%
by the sixth — a 58% decline. Two rules follow:

* `max_attempts` defaults to 4. By the fifth attempt the response rate has roughly halved,
  so further attempts mostly generate cost and annoyance rather than recoveries.
* `cooldown_hours` defaults to 48, so attempts are spread rather than stacked.

Both are defaults, not constants: they are exactly the knobs the controlled experiment
should vary, and the number that justifies them is reproducible from the dataset.

A promise to pay pauses contact entirely for `promise_grace_days` — chasing someone who
just committed to a date is the fastest way to lose the commitment.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from collection.rules.window import ContactWindow

#: Cost per contact in BRL, from the channel costs observed in the data.
CHANNEL_COST = {"whatsapp": 0.12, "sms": 0.09, "email": 0.01, "phone": 1.40}


class DenialReason(StrEnum):
    OPTED_OUT = "opted_out"
    OUTSIDE_WINDOW = "outside_window"
    COOLDOWN = "cooldown"
    MAX_ATTEMPTS = "max_attempts"
    WEEKLY_CAP = "weekly_cap"
    PROMISE_GRACE = "promise_grace"
    DISPUTED = "disputed"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: DenialReason | None = None
    detail: str = ""
    #: When the same attempt would become allowed, when that is knowable.
    retry_at: datetime | None = None

    def __bool__(self) -> bool:
        return self.allowed


ALLOWED = Decision(allowed=True)


@dataclass
class ContactHistory:
    """What has already happened to this receivable and customer."""

    attempts: int = 0
    last_contact_at: datetime | None = None
    contacts_this_week: int = 0
    promised_at: datetime | None = None
    disputed: bool = False
    opted_out: bool = False


@dataclass
class ContactPolicy:
    window: ContactWindow = field(default_factory=ContactWindow)
    #: Empirical: response rate has roughly halved by the fifth attempt.
    max_attempts: int = 4
    cooldown_hours: int = 48
    #: Legal: a ceiling on frequency, since repeated contact is how harassment manifests.
    max_contacts_per_week: int = 3
    promise_grace_days: int = 5
    #: A disputed debt goes to a human rather than back into the campaign.
    stop_on_dispute: bool = True

    def evaluate(
        self,
        history: ContactHistory,
        moment: datetime | None = None,
    ) -> Decision:
        """Decide whether an outbound contact may be made now."""
        moment = moment or datetime.now(UTC)

        # Legal blocks first: these are not trade-offs against effectiveness.
        if history.opted_out:
            return Decision(
                allowed=False,
                reason=DenialReason.OPTED_OUT,
                detail="Customer asked not to be contacted.",
            )

        if self.stop_on_dispute and history.disputed:
            return Decision(
                allowed=False,
                reason=DenialReason.DISPUTED,
                detail="Debt is disputed; it belongs with a human, not a campaign.",
            )

        if history.promised_at is not None:
            grace_ends = history.promised_at + timedelta(days=self.promise_grace_days)
            if moment < grace_ends:
                return Decision(
                    allowed=False,
                    reason=DenialReason.PROMISE_GRACE,
                    detail=f"Customer promised to pay; grace period until {grace_ends:%Y-%m-%d}.",
                    retry_at=grace_ends,
                )

        if history.attempts >= self.max_attempts:
            return Decision(
                allowed=False,
                reason=DenialReason.MAX_ATTEMPTS,
                detail=(
                    f"{history.attempts} attempts already made (cap {self.max_attempts}); "
                    "further attempts cost more than they recover."
                ),
            )

        if history.contacts_this_week >= self.max_contacts_per_week:
            return Decision(
                allowed=False,
                reason=DenialReason.WEEKLY_CAP,
                detail=f"Weekly cap of {self.max_contacts_per_week} reached.",
            )

        if history.last_contact_at is not None:
            ready_at = history.last_contact_at + timedelta(hours=self.cooldown_hours)
            if moment < ready_at:
                return Decision(
                    allowed=False,
                    reason=DenialReason.COOLDOWN,
                    detail=f"Last contact was under {self.cooldown_hours}h ago.",
                    retry_at=self.window.next_opening(ready_at),
                )

        if not self.window.allows(moment):
            return Decision(
                allowed=False,
                reason=DenialReason.OUTSIDE_WINDOW,
                detail="Outside the hours collection contact is permitted.",
                retry_at=self.window.next_opening(moment),
            )

        return ALLOWED

    def expected_value(self, attempt_number: int, channel: str, amount: float) -> float:
        """Rough expected return of one more attempt, in BRL.

        Uses the response-rate decay measured in the Bank Marketing data against the
        channel's cost. Not a precise forecast -- it exists so that "is another attempt
        worth it" is answered with a number rather than a hunch, and so the cost side of
        the comparison in the evaluation has a basis.
        """
        response_rate = response_rate_for_attempt(attempt_number)
        cost = CHANNEL_COST.get(channel, 0.12)
        return response_rate * amount - cost


#: Response rate by attempt number, measured over 41,188 real contacts.
#: See docs/eda-bank-marketing.md.
OBSERVED_RESPONSE_RATE = {1: 0.130, 2: 0.115, 3: 0.108, 4: 0.094, 5: 0.075, 6: 0.055}


def response_rate_for_attempt(attempt_number: int) -> float:
    """Observed response rate, extrapolated flat beyond the measured range."""
    if attempt_number <= 0:
        return OBSERVED_RESPONSE_RATE[1]
    if attempt_number in OBSERVED_RESPONSE_RATE:
        return OBSERVED_RESPONSE_RATE[attempt_number]
    return OBSERVED_RESPONSE_RATE[max(OBSERVED_RESPONSE_RATE)]
