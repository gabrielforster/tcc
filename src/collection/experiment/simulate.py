"""Run one arm over one population, a day at a time, and record every contact.

The three arms differ in **whom they reach and through what**, never in which rules they
obey. Every arm's contacts go through the same `ContactPolicy`, the one the proactive agent
uses: the legal limits are not a feature of the system under test, a human team is held to
them too, and crediting the system with avoiding violations the baseline was allowed to
commit would measure the rules twice. Compliance is checked afterwards by `audit`, which
re-derives each rule from the contact log without calling the policy -- so it is a check on
the implementation, and it must read zero in every arm.

The arms (docs/experiment-protocol.md §2):

* **manual** -- a collector working the overdue book oldest-first, by phone, on weekdays,
  up to a daily capacity. What a small collection desk does today.
* **semi** -- the rules engine and the stage templates without prediction: every overdue
  receivable is contacted whenever the rules allow, oldest first, on the stage's channel.
* **full** -- the system: the same rules and templates, plus the predictive module. It
  ranks the overdue book by expected value (the response curve x amount x risk, less the
  channel's cost) and sends a preventive reminder before the due date to the receivables
  the default model flags.

How a contact is answered (§3) is the same function for every arm:

* it **converts** -- the customer pays -- with the attempt's response rate measured in the
  Bank Marketing data, scaled by how the customer's propensity compares to the book's;
* or the customer disputes, or opts out, at the rates the synthetic generator uses;
* or nothing happens.

A conversion brings the payment forward to the contact day plus a short lag, never later
than the customer would have paid anyway. A contact on someone who was going to pay
tomorrow therefore buys nothing and still costs its price: that is the waste prediction is
supposed to avoid.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from collection.agents.proactive import CHANNEL_BY_STAGE
from collection.data.sources.synthetic import delinquency_stage
from collection.experiment.population import Population, Visible
from collection.rules.policy import (
    CHANNEL_COST,
    ContactHistory,
    ContactPolicy,
    response_rate_for_attempt,
)

ARMS = ("none", "manual", "semi", "full")

#: When the day's contacts are made, local time: inside every permitted window, Saturdays
#: included, so a refusal is about the day or the history and never about the hour.
CONTACT_TIME = time(10, 0)
#: A person's time per call, priced: about six minutes at R$ 25/h loaded (§3.4).
MANUAL_LABOUR_COST = 2.50
#: The preventive reminder goes this many days before the due date.
PREVENTIVE_LEAD_DAYS = 3
#: Share of the cohort the default model flags for a preventive reminder: the top quintile.
PREVENTIVE_SHARE = 0.20
#: A conversion rate cannot exceed this, however far above average the customer is.
MAX_CONVERSION = 0.60
#: Outcome mix of an answered contact in the synthetic generator (`_make_events`).
DISPUTE_SHARE = 0.15
OPT_OUT_SHARE = 0.05


@dataclass(frozen=True)
class Response:
    """How a contact is answered. Defaults are the main analysis; the fields are the
    sensitivity knobs."""

    #: Scale the conversion by propensity / mean propensity. Off means everyone converts at
    #: the population's average rate, so ranking can only help through amount and risk.
    heterogeneous: bool = True
    #: Multiplier on conversion for written channels. Bank Marketing measured phone calls.
    chat_factor: float = 1.0


#: The main analysis' response model.
MAIN = Response()


@dataclass(frozen=True)
class Contact:
    day: date
    moment: datetime
    index: int
    invoice_id: str
    customer_id: str
    attempt: int
    channel: str
    cost: float
    preventive: bool
    outcome: str


@dataclass
class Run:
    arm: str
    seed: int
    capacity: int | None
    contacts: list[Contact] = field(default_factory=list)
    #: The day each receivable was finally paid, or None.
    paid: list[date | None] = field(default_factory=list)
    denials: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    #: The state the auditor needs: when each customer opted out or disputed, when each
    #: receivable's grace began.
    opted_out_at: dict[str, datetime] = field(default_factory=dict)
    disputed_at: dict[int, datetime] = field(default_factory=dict)
    promised_at: dict[int, datetime] = field(default_factory=dict)


@dataclass
class _State:
    attempts: list[int]
    paid: list[date | None]
    promised_at: list[datetime | None]
    disputed: list[bool]
    opted_out: set[str]
    last_contact: dict[str, datetime] = field(default_factory=dict)
    week_contacts: dict[tuple[str, int, int], int] = field(default_factory=lambda: defaultdict(int))


def _channel(arm: str, days_late: int) -> str:
    if arm == "manual":
        return "voice"
    return CHANNEL_BY_STAGE.get(delinquency_stage(days_late), "chat")


def _cost(arm: str, channel: str) -> float:
    cost = CHANNEL_COST[channel]
    return cost + MANUAL_LABOUR_COST if arm == "manual" else cost


def _days(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _preventive_threshold(population: Population) -> float:
    scores = sorted((v.risk_default for v in population.visible), reverse=True)
    if not scores:
        return 1.0
    cut = max(int(len(scores) * PREVENTIVE_SHARE), 1)
    return scores[cut - 1]


def _candidates(
    arm: str, population: Population, state: _State, day: date, threshold: float
) -> list[tuple[Visible, bool]]:
    """The arm's ordered worklist for the day, as (receivable, preventive).

    Reads `Visible` and the system's own record (attempts, paid, disputed) and nothing
    else: no arm can see a latent propensity or an untreated payment date.
    """
    open_items = [
        v
        for v in population.visible
        if not _is_paid(state, v.index, day) and not state.disputed[v.index]
    ]
    overdue = [v for v in open_items if day > v.due]
    if arm in ("manual", "semi"):
        # Oldest debt first: the order a desk without a model works in.
        return [(v, False) for v in sorted(overdue, key=lambda v: (v.due, v.invoice_id))]

    # full: expected value of the next attempt, as the policy's own estimate prices it.
    def value(v: Visible, risk: float, channel: str) -> float:
        attempt = state.attempts[v.index] + 1
        return response_rate_for_attempt(attempt) * v.amount * risk - CHANNEL_COST[channel]

    ranked: list[tuple[float, Visible, bool]] = []
    for v in overdue:
        channel = _channel(arm, (day - v.due).days)
        ranked.append((value(v, v.risk_overdue, channel), v, False))
    for v in open_items:
        lead = (v.due - day).days
        if (
            0 < lead <= PREVENTIVE_LEAD_DAYS
            and state.attempts[v.index] == 0
            and v.risk_default >= threshold
        ):
            ranked.append((value(v, v.risk_default, "chat"), v, True))
    ranked.sort(key=lambda item: (-item[0], item[1].invoice_id))
    return [(v, preventive) for score, v, preventive in ranked if score > 0]


def _is_paid(state: _State, index: int, day: date) -> bool:
    paid = state.paid[index]
    return paid is not None and paid <= day


def simulate(
    population: Population,
    arm: str,
    *,
    capacity: int | None = None,
    policy: ContactPolicy | None = None,
    response: Response = MAIN,
) -> Run:
    """Play `arm` over the population's horizon. `capacity` caps contacts per day."""
    if arm not in ARMS:
        raise ValueError(f"arm must be one of {ARMS}")
    if arm == "manual" and capacity is None:
        raise ValueError("the manual arm needs a capacity: a desk is never unlimited")
    policy = policy or ContactPolicy()
    n = len(population.visible)
    state = _State(
        attempts=[0] * n,
        paid=[latent.untreated_payment for latent in population.latent],
        promised_at=[None] * n,
        disputed=[False] * n,
        opted_out={v.customer_id for v in population.visible if v.opted_out},
    )
    run = Run(arm=arm, seed=population.seed, capacity=capacity)
    # Opted out before the horizon began: on the record from the first minute.
    opened = policy.window.localise(datetime.combine(population.start, time(0, 0)))
    for customer_id in state.opted_out:
        run.opted_out_at[customer_id] = opened
    threshold = _preventive_threshold(population)

    if arm != "none":
        for day in _days(population.start, population.end):
            if arm == "manual" and day.weekday() >= 5:
                continue  # the desk works Monday to Friday
            moment = policy.window.localise(datetime.combine(day, CONTACT_TIME))
            made = 0
            for visible, preventive in _candidates(arm, population, state, day, threshold):
                if capacity is not None and made >= capacity:
                    break
                week = moment.isocalendar()
                week_key = (visible.customer_id, week.year, week.week)
                history = ContactHistory(
                    attempts=state.attempts[visible.index],
                    last_contact_at=state.last_contact.get(visible.customer_id),
                    contacts_this_week=state.week_contacts[week_key],
                    promised_at=state.promised_at[visible.index],
                    disputed=state.disputed[visible.index],
                    opted_out=visible.customer_id in state.opted_out,
                )
                decision = policy.evaluate(history, moment=moment)
                if not decision.allowed:
                    run.denials[decision.reason.value if decision.reason else "unknown"] += 1
                    continue
                made += 1
                run.contacts.append(
                    _contact(
                        arm, population, state, run, visible, preventive, day, moment, response
                    )
                )
                state.last_contact[visible.customer_id] = moment
                state.week_contacts[week_key] += 1

    run.paid = list(state.paid)
    return run


def _contact(
    arm: str,
    population: Population,
    state: _State,
    run: Run,
    visible: Visible,
    preventive: bool,
    day: date,
    moment: datetime,
    response: Response,
) -> Contact:
    i = visible.index
    state.attempts[i] += 1
    attempt = state.attempts[i]
    days_late = max((day - visible.due).days, 0)
    channel = "chat" if preventive else _channel(arm, days_late)

    latent = population.latent[i]
    rate = response_rate_for_attempt(attempt)
    if response.heterogeneous:
        rate *= latent.propensity / population.mean_propensity
    if channel != "voice":
        rate *= response.chat_factor
    convert = min(rate, MAX_CONVERSION)
    # An answered contact, as the synthetic generator answers one, splits into dispute and
    # opt-out shares; the conversion itself comes from Bank Marketing instead.
    answered = min(max(0.25 + 0.5 * latent.propensity, 0.05), 0.9)
    dispute = answered * DISPUTE_SHARE
    opt_out = answered * OPT_OUT_SHARE

    draw = population.draws[i, min(attempt, population.draws.shape[1]) - 1]
    if draw < convert:
        outcome = "converted"
        lag = int(population.lags[i, min(attempt, population.lags.shape[1]) - 1])
        promised = day + timedelta(days=lag)
        if preventive:
            promised = min(promised, visible.due)
        current = state.paid[i]
        if current is None or promised < current:
            state.paid[i] = promised
        state.promised_at[i] = moment
        run.promised_at[i] = moment
    elif draw < convert + dispute:
        outcome = "disputed"
        state.disputed[i] = True
        run.disputed_at[i] = moment
    elif draw < convert + dispute + opt_out:
        outcome = "opted_out"
        state.opted_out.add(visible.customer_id)
        run.opted_out_at.setdefault(visible.customer_id, moment)
    else:
        outcome = "no_effect"

    return Contact(
        day=day,
        moment=moment,
        index=i,
        invoice_id=visible.invoice_id,
        customer_id=visible.customer_id,
        attempt=attempt,
        channel=channel,
        cost=_cost(arm, channel),
        preventive=preventive,
        outcome=outcome,
    )
