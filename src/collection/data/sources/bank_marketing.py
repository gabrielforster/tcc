"""UCI Bank Marketing as a source of real contact-attempt behavior.

https://archive.ics.uci.edu/dataset/222/bank+marketing -- 41,188 outbound phone contacts
from a Portuguese bank, ordered by date, May 2008 to November 2010 (Moro et al., 2014).
Licensed CC BY 4.0 and downloadable without an account.

Why this dataset is here: it is the only public source we found with *repeated outbound
contact attempts and their outcomes*. It is term-deposit marketing, not collection, so it
says nothing about receivables -- but the question "how do repeated contact attempts,
across channels, affect the chance of a response" is structurally the same one the active
module has to answer, and here it is answered by real human behavior instead of by our
generator.

Two limitations that must survive into the paper:

* It populates `customers` and `collection_events` only. Tasks 1 and 2 do not run on it;
  it feeds contact-strategy analysis.
* **The dataset has no dates.** It carries month and day of week, and the rows are ordered
  chronologically, so the calendar below is *reconstructed*: the month sequence is walked
  in file order, a year boundary is assumed whenever the month goes backwards, and within
  each month rows are laid onto the days matching their recorded weekday. Timestamps are
  therefore good for ordering and for month-level seasonality, and meaningless at
  day-level precision.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from collection.data.sources.base import DataSource, RawDataset
from collection.data.sources.download import download_and_extract_zip

URL = "https://archive.ics.uci.edu/static/public/222/bank+marketing.zip"
FILENAME = "bank-additional-full.csv"
DATASET = "bank-marketing"

# The campaign starts in May 2008 (Moro et al., 2014).
FIRST_MONTH = (2008, 5)

MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4}

# `contact` only distinguishes telephone from cellular; cellular is the closest thing the
# dataset has to the mobile channel our system uses for WhatsApp/SMS.
CHANNEL = {"telephone": "phone", "cellular": "whatsapp"}

# `y` is whether the client subscribed. In collection terms, the contact achieved its goal.
OUTCOME = {"yes": "promised_to_pay", "no": "no_answer"}


class BankMarketingSource(DataSource):
    name = "bank-marketing"
    provides = frozenset({"customers", "collection_events"})

    def __init__(self, path: str | None = None) -> None:
        self.path = path

    def _load(self) -> pd.DataFrame:
        source = self.path or download_and_extract_zip(URL, DATASET, FILENAME)
        return pd.read_csv(source, sep=";", quotechar='"')

    def extract(self) -> RawDataset:
        df = self._load().reset_index(drop=True)
        df["customer_id"] = [f"BM{i:06d}" for i in range(1, len(df) + 1)]
        event_dates = _reconstruct_dates(df["month"], df["day_of_week"])

        customers = pd.DataFrame(
            {
                "customer_id": df["customer_id"],
                "name": "",
                "tax_id": "",
                "email": "",
                "phone": "",
                "city": "",
                "state": "",
                # The dataset has no signup date; age is the only tenure-like signal, so
                # the registry date is left at the first contact.
                "signup_date": event_dates,
                "segment": df["job"],
                "size_tier": df["education"],
                "credit_limit": np.nan,
                "preferred_channel": df["contact"].map(CHANNEL).fillna("phone"),
                # `default` is "has credit in default": the closest thing to a customer who
                # should not be approached the usual way.
                "opt_out": df["default"].eq("yes"),
            }
        )

        events = pd.DataFrame(
            {
                "event_id": [f"BME{i:06d}" for i in range(1, len(df) + 1)],
                # No receivables in this dataset: contacts are not tied to a document.
                "invoice_id": "",
                "customer_id": df["customer_id"],
                "event_time": pd.to_datetime(event_dates),
                "channel": df["contact"].map(CHANNEL).fillna("phone"),
                "kind": np.where(df["previous"] > 0, "negotiation", "reminder"),
                "delinquency_stage": "pre_due",
                "outcome": df["y"].map(OUTCOME).fillna("no_answer"),
                "contact_cost": np.where(df["contact"].eq("telephone"), 1.4, 0.12),
                # Kept beyond the domain schema because they are the whole point of this
                # source: contact pressure and the previous campaign's result.
                "attempt_number": df["campaign"],
                "previous_attempts": df["previous"],
                "days_since_previous_contact": df["pdays"].replace(999, np.nan),
                "previous_outcome": df["poutcome"],
                "call_duration_seconds": df["duration"],
            }
        )

        return RawDataset(
            customers=customers,
            invoices=self.empty_table("invoices"),
            events=events,
            agreements=self.empty_table("agreements"),
            provides=self.provides,
        )


def _reconstruct_dates(months: pd.Series, weekdays: pd.Series) -> list[date]:
    """Lay chronologically ordered rows onto a calendar (see the module docstring)."""
    year, month = FIRST_MONTH
    previous_month = MONTHS[months.iloc[0]]
    slots: list[date] = []
    cursor = 0
    month_days: list[date] = _weekday_slots(year, month)

    for month_name, weekday_name in zip(months, weekdays, strict=True):
        current_month = MONTHS[month_name]
        if current_month != previous_month:
            # Rows are ordered by date, so a month going backwards means a new year.
            if current_month < previous_month:
                year += 1
            month = current_month
            previous_month = current_month
            month_days = _weekday_slots(year, month)
            cursor = 0

        target_weekday = WEEKDAYS.get(weekday_name, 0)
        candidate = _next_matching_day(month_days, cursor, target_weekday)
        cursor = candidate
        slots.append(month_days[candidate])
    return slots


def _weekday_slots(year: int, month: int) -> list[date]:
    """Business days of a month, in order."""
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1)
    days = [start + timedelta(days=i) for i in range((end - start).days)]
    return [d for d in days if d.weekday() < 5]


def _next_matching_day(days: list[date], cursor: int, weekday: int) -> int:
    """First day at or after `cursor` with the given weekday; clamps at the month's end."""
    for i in range(cursor, len(days)):
        if days[i].weekday() == weekday:
            return i
    return len(days) - 1
