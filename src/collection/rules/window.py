"""When a customer may legally and reasonably be contacted.

Brazilian consumer-protection practice (CDC art. 42, the state PROCON guidance most
commonly applied, and the 2021 superendividamento amendments) constrains collection to
reasonable hours and forbids conduct that exposes or harasses the debtor. This module fixes
those hours in one place so that no agent can accidentally widen them.

The defaults implemented here are the conservative reading in common use:

* weekdays 08:00-20:00;
* Saturdays 08:00-14:00;
* no contact on Sundays or public holidays.

Holidays are injected rather than hard-coded: the relevant set is municipal as well as
national, and pretending otherwise in code would be a false precision.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

#: Permitted hours are a wall-clock rule where the customer is, so every comparison
#: happens in this zone. Evaluating in UTC would allow contact at 05:00 local.
DEFAULT_TIMEZONE = "America/Sao_Paulo"

WEEKDAY_WINDOW = (time(8, 0), time(20, 0))
SATURDAY_WINDOW = (time(8, 0), time(14, 0))
SUNDAY = 6
SATURDAY = 5


@dataclass(frozen=True)
class ContactWindow:
    weekday: tuple[time, time] = WEEKDAY_WINDOW
    saturday: tuple[time, time] = SATURDAY_WINDOW
    contact_on_sunday: bool = False
    holidays: frozenset[date] = field(default_factory=frozenset)
    timezone: str = DEFAULT_TIMEZONE

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def localise(self, moment: datetime) -> datetime:
        """Express an instant in the customer's local wall clock."""
        if moment.tzinfo is None:
            return moment.replace(tzinfo=self.zone)
        return moment.astimezone(self.zone)

    def window_for(self, day: date) -> tuple[time, time] | None:
        """Allowed interval on a given day, or None if contact is forbidden."""
        if day in self.holidays:
            return None
        if day.weekday() == SUNDAY:
            return self.weekday if self.contact_on_sunday else None
        if day.weekday() == SATURDAY:
            return self.saturday
        return self.weekday

    def allows(self, moment: datetime) -> bool:
        local = self.localise(moment)
        window = self.window_for(local.date())
        if window is None:
            return False
        start, end = window
        return start <= local.time() < end

    def next_opening(self, moment: datetime, horizon_days: int = 30) -> datetime | None:
        """The first instant at or after `moment` when contact is allowed."""
        if self.allows(moment):
            return moment
        candidate = self.localise(moment)
        for _ in range(horizon_days):
            window = self.window_for(candidate.date())
            if window is not None:
                start, end = window
                if candidate.time() < start:
                    return datetime.combine(candidate.date(), start, tzinfo=self.zone)
                if candidate.time() < end:  # pragma: no cover - covered by allows()
                    return candidate
            candidate = datetime.combine(
                candidate.date() + timedelta(days=1), time(0, 0), tzinfo=self.zone
            )
        return None
