"""Public dataset sources, exercised against small fixtures rather than the network.

The fixtures reproduce the real files' columns and separators exactly, so a schema change
upstream surfaces here as a failure instead of at extraction time.
"""

import pandas as pd
import pytest

from collection.data.sources.bank_marketing import BankMarketingSource, _reconstruct_dates
from collection.data.sources.base import ALL_TABLES
from collection.data.sources.home_credit import HomeCreditSource
from collection.data.sources.home_credit import REQUIRED_FILES as HOME_CREDIT_FILES

BANK_HEADER = (
    '"age";"job";"marital";"education";"default";"housing";"loan";"contact";"month";'
    '"day_of_week";"duration";"campaign";"pdays";"previous";"poutcome";"emp.var.rate";'
    '"cons.price.idx";"cons.conf.idx";"euribor3m";"nr.employed";"y"'
)
BANK_ROWS = [
    '56;"housemaid";"married";"basic.4y";"no";"no";"no";"telephone";"may";"mon";261;1;999;0;'
    '"nonexistent";1.1;93.994;-36.4;4.857;5191;"no"',
    '37;"services";"married";"high.school";"no";"yes";"no";"telephone";"may";"tue";226;1;999;0;'
    '"nonexistent";1.1;93.994;-36.4;4.857;5191;"yes"',
    '41;"blue-collar";"married";"unknown";"unknown";"no";"no";"cellular";"jun";"wed";151;2;999;1;'
    '"failure";1.1;93.994;-36.4;4.857;5191;"no"',
    '25;"services";"single";"high.school";"no";"yes";"no";"cellular";"mar";"thu";307;3;6;2;'
    '"success";1.1;93.994;-36.4;4.857;5191;"yes"',
]


@pytest.fixture
def bank_csv(tmp_path):
    path = tmp_path / "bank-additional-full.csv"
    path.write_text("\n".join([BANK_HEADER, *BANK_ROWS]) + "\n")
    return str(path)


@pytest.fixture
def home_credit_dir(tmp_path, monkeypatch):
    from collection.data.sources import download, home_credit

    directory = tmp_path / "home-credit"
    directory.mkdir()
    pd.DataFrame(
        {
            "SK_ID_CURR": [1, 2],
            "AMT_CREDIT": [400_000.0, 250_000.0],
            "AMT_INCOME_TOTAL": [90_000.0, 300_000.0],
            "NAME_INCOME_TYPE": ["Working", "Pensioner"],
            "NAME_CONTRACT_TYPE": ["Cash loans", "Revolving loans"],
            "DAYS_REGISTRATION": [-2000.0, -900.0],
        }
    ).to_csv(directory / "application_train.csv", index=False)
    pd.DataFrame(
        {
            "SK_ID_PREV": [10, 10, 11, 11],
            "SK_ID_CURR": [1, 1, 2, 2],
            "NUM_INSTALMENT_NUMBER": [1, 2, 1, 2],
            "DAYS_INSTALMENT": [-500.0, -470.0, -300.0, -270.0],
            # The last installment was never paid: no entry date, no amount.
            "DAYS_ENTRY_PAYMENT": [-505.0, -400.0, -299.0, None],
            "AMT_INSTALMENT": [1000.0, 1000.0, 500.0, 500.0],
            "AMT_PAYMENT": [1000.0, 1000.0, 500.0, None],
        }
    ).to_csv(directory / "installments_payments.csv", index=False)

    monkeypatch.setattr(download.settings, "dir_raw", tmp_path)
    monkeypatch.setattr(home_credit.settings, "dir_raw", tmp_path)
    return directory


# --------------------------------------------------------------------- bank marketing
def test_bank_marketing_maps_onto_customers_and_events(bank_csv):
    dataset = BankMarketingSource(path=bank_csv).extract()
    assert dataset.validate() == []
    assert len(dataset.events) == len(BANK_ROWS)
    assert dataset.invoices.empty and dataset.agreements.empty
    assert dataset.provides == frozenset({"customers", "collection_events"})


def test_bank_marketing_translates_channels_and_outcomes(bank_csv):
    events = BankMarketingSource(path=bank_csv).extract().events
    assert set(events["channel"]) <= {"phone", "whatsapp"}
    assert set(events["outcome"]) <= {"no_answer", "promised_to_pay"}
    # Row 2 subscribed, row 1 did not.
    assert events["outcome"].tolist()[:2] == ["no_answer", "promised_to_pay"]


def test_bank_marketing_keeps_the_contact_pressure_columns(bank_csv):
    events = BankMarketingSource(path=bank_csv).extract().events
    for column in ("attempt_number", "previous_attempts", "days_since_previous_contact"):
        assert column in events.columns
    # 999 in pdays means "never contacted before" and must not be read as 999 days.
    assert events["days_since_previous_contact"].tolist() == pytest.approx(
        [float("nan"), float("nan"), float("nan"), 6.0], nan_ok=True
    )


def test_reconstructed_dates_are_ordered_and_start_at_the_campaign_start(bank_csv):
    events = BankMarketingSource(path=bank_csv).extract().events
    dates = events["event_time"]
    assert dates.is_monotonic_increasing
    assert dates.iloc[0].year == 2008 and dates.iloc[0].month == 5


def test_a_month_going_backwards_is_read_as_a_new_year():
    months = pd.Series(["may", "jun", "nov", "mar"])
    weekdays = pd.Series(["mon", "mon", "mon", "mon"])
    dates = _reconstruct_dates(months, weekdays)
    assert [d.year for d in dates] == [2008, 2008, 2008, 2009]
    assert dates == sorted(dates)


def test_reconstructed_days_match_the_recorded_weekday():
    months = pd.Series(["may", "may", "may"])
    weekdays = pd.Series(["mon", "wed", "fri"])
    assert [d.weekday() for d in _reconstruct_dates(months, weekdays)] == [0, 2, 4]


# ------------------------------------------------------------------------ home credit
def test_home_credit_maps_installments_onto_receivables(home_credit_dir):
    dataset = HomeCreditSource(n_customers=None).extract()
    assert dataset.validate() == []
    assert len(dataset.invoices) == 4
    assert dataset.events.empty and dataset.agreements.empty
    assert dataset.provides == frozenset({"customers", "invoices"})


def test_home_credit_derives_lateness_from_scheduled_versus_actual_payment(home_credit_dir):
    invoices = HomeCreditSource(n_customers=None).extract().invoices.set_index("invoice_id")
    # Paid 5 days early, then 70 days late, then 1 day early, then never paid.
    assert invoices.loc["10-1", "days_late"] == 0
    assert invoices.loc["10-1", "status"] == "paid"
    assert invoices.loc["10-2", "days_late"] == 70
    assert invoices.loc["10-2", "status"] == "paid_late"
    assert pd.isna(invoices.loc["11-2", "payment_date"])


def test_home_credit_drops_customers_left_without_receivables(home_credit_dir):
    dataset = HomeCreditSource(n_customers=None).extract()
    assert set(dataset.customers["customer_id"]) == set(dataset.invoices["customer_id"])


def test_home_credit_explains_how_to_obtain_the_files_when_they_are_missing(tmp_path, monkeypatch):
    from collection.data.sources import download, home_credit

    monkeypatch.setattr(download.settings, "dir_raw", tmp_path)
    monkeypatch.setattr(home_credit.settings, "dir_raw", tmp_path)
    with pytest.raises(FileNotFoundError) as error:
        HomeCreditSource().extract()
    message = str(error.value)
    assert all(name in message for name in HOME_CREDIT_FILES)
    assert "kaggle" in message.lower()


# ------------------------------------------------------------------------------ shared
def test_a_source_declaring_fewer_tables_still_matches_the_schema(bank_csv):
    """Tables a source does not cover must exist with the right columns, just empty."""
    dataset = BankMarketingSource(path=bank_csv).extract()
    from collection.domain.schema import TABLES_BY_NAME

    for name in ALL_TABLES - dataset.provides:
        df = dataset.as_dict()[name]
        assert list(df.columns) == TABLES_BY_NAME[name].columns


def test_rows_in_an_undeclared_table_are_reported(bank_csv):
    dataset = BankMarketingSource(path=bank_csv).extract()
    dataset.provides = frozenset({"customers"})
    assert any("not declared in provides" in p for p in dataset.validate())
