"""Re-check a run's contact log against the rules, without asking the rules engine.

The simulator obeys `ContactPolicy`; this module does not import it. Each rule is derived
again from the log and the numbers in docs/collection-rules.md, so a bug in the policy --
or in how the simulator feeds it history -- shows up here as a violation instead of being
agreed with. A violation count that is not zero in any arm fails the experiment, which is
why the tests assert it and the report prints it.
"""

from collections import defaultdict
from datetime import timedelta
from zoneinfo import ZoneInfo

from collection.experiment.simulate import Run

ZONE = ZoneInfo("America/Sao_Paulo")
MAX_ATTEMPTS = 4
COOLDOWN = timedelta(hours=48)
MAX_PER_WEEK = 3
PROMISE_GRACE = timedelta(days=5)


def audit(run: Run) -> dict[str, int]:
    """Violations by rule. Every value must be zero."""
    found: dict[str, int] = defaultdict(int)
    last: dict[str, object] = {}
    per_week: dict[tuple, int] = defaultdict(int)

    for contact in sorted(run.contacts, key=lambda c: (c.moment, c.invoice_id)):
        local = contact.moment.astimezone(ZONE)
        weekday = local.weekday()
        hour = local.hour + local.minute / 60
        opening, closing = (8, 14) if weekday == 5 else (8, 20)
        if weekday == 6:
            found["sunday"] += 1
        elif not opening <= hour < closing:
            found["outside_hours"] += 1

        opted = run.opted_out_at.get(contact.customer_id)
        if opted is not None and contact.moment > opted:
            found["after_opt_out"] += 1
        disputed = run.disputed_at.get(contact.index)
        if disputed is not None and contact.moment > disputed:
            found["after_dispute"] += 1
        promised = run.promised_at.get(contact.index)
        if promised is not None and promised < contact.moment < promised + PROMISE_GRACE:
            found["inside_promise_grace"] += 1
        if contact.attempt > MAX_ATTEMPTS:
            found["over_max_attempts"] += 1

        previous = last.get(contact.customer_id)
        if previous is not None and contact.moment - previous < COOLDOWN:
            found["inside_cooldown"] += 1
        last[contact.customer_id] = contact.moment

        week = local.isocalendar()
        key = (contact.customer_id, week.year, week.week)
        per_week[key] += 1
        if per_week[key] > MAX_PER_WEEK:
            found["over_weekly_cap"] += 1

    return dict(found)


def violations(run: Run) -> int:
    return sum(audit(run).values())
