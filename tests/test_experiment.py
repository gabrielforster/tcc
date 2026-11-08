"""The controlled experiment: a function of its seed, blind to what it simulates, lawful.

The tests that matter are the invariants a result would silently depend on. A replication
must be reproducible from its seed, or a reported interval describes nothing anyone can
re-run. No arm may contact anyone the rules forbid -- checked by the auditor, which does not
share code with the policy. And a contact may only ever bring a payment forward: if one
could delay it, the arms would be measured against a counterfactual they can damage.
"""

from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from collection.data.sources.synthetic import SyntheticSource
from collection.experiment import load, report
from collection.experiment.audit import audit
from collection.experiment.metrics import bootstrap, outcome, paired
from collection.experiment.population import build_population, freeze
from collection.experiment.run import run_experiment
from collection.experiment.simulate import ARMS, MAIN, Contact, Response, Run, simulate
from collection.features.build import build_features

SMALL = {"n_customers": 150, "n_invoices": 1800}
ZONE = ZoneInfo("America/Sao_Paulo")


@pytest.fixture(scope="module")
def population():
    return build_population(3, **SMALL)


def _runs(population, response=MAIN):
    yield simulate(population, "none", response=response)
    for capacity in (2, 8, None):
        for arm in ("manual", "semi", "ranked", "full"):
            if arm == "manual" and capacity is None:
                continue
            yield simulate(population, arm, capacity=capacity, response=response)


def test_a_replication_is_a_function_of_its_seed(population):
    again = build_population(3, **SMALL)
    assert again.visible == population.visible
    assert again.latent == population.latent
    assert np.array_equal(again.draws, population.draws)
    first = simulate(population, "full", capacity=4)
    second = simulate(again, "full", capacity=4)
    assert first.contacts == second.contacts
    assert first.paid == second.paid


def test_another_seed_is_another_world(population):
    other = build_population(4, **SMALL)
    assert [v.invoice_id for v in other.visible] != [v.invoice_id for v in population.visible] or (
        other.latent != population.latent
    )


@pytest.mark.parametrize(
    "response", [Response(), Response(heterogeneous=False), Response(chat_factor=0.5)]
)
def test_no_arm_breaks_a_rule(population, response):
    for run in _runs(population, response):
        assert audit(run) == {}, (run.arm, run.capacity)


def test_capacity_is_a_ceiling_and_the_desk_keeps_weekdays(population):
    for capacity in (2, 8):
        for arm in ("manual", "semi", "full"):
            run = simulate(population, arm, capacity=capacity)
            per_day = Counter(c.day for c in run.contacts)
            assert max(per_day.values(), default=0) <= capacity
    manual = simulate(population, "manual", capacity=8)
    assert manual.contacts
    assert all(c.day.weekday() < 5 for c in manual.contacts)
    assert {c.channel for c in manual.contacts} == {"voice"}


def test_doing_nothing_leaves_every_untreated_outcome(population):
    run = simulate(population, "none")
    assert run.contacts == []
    assert run.paid == [latent.untreated_payment for latent in population.latent]


def test_a_contact_only_ever_brings_a_payment_forward(population):
    for run in _runs(population):
        for paid, latent in zip(run.paid, population.latent, strict=True):
            if latent.untreated_payment is not None:
                assert paid is not None and paid <= latent.untreated_payment


def test_only_the_system_sends_preventive_reminders(population):
    assert not any(c.preventive for c in simulate(population, "semi").contacts)
    assert not any(c.preventive for c in simulate(population, "ranked").contacts)
    full = simulate(population, "full")
    preventive = [c for c in full.contacts if c.preventive]
    assert preventive
    for contact in preventive:
        due = population.visible[contact.index].due
        assert 0 < (due - contact.day).days <= 3


def test_manual_needs_a_capacity(population):
    with pytest.raises(ValueError):
        simulate(population, "manual")
    with pytest.raises(ValueError):
        simulate(population, "nobody")
    assert set(ARMS) == {"none", "manual", "semi", "ranked", "full"}


def test_contacting_recovers_at_least_what_doing_nothing_does(population):
    nothing = outcome(population, simulate(population, "none"))
    for arm in ("semi", "full"):
        result = outcome(population, simulate(population, arm))
        assert result.recovery_rate_30 >= nothing.recovery_rate_30
        assert result.days_outstanding <= nothing.days_outstanding
        assert result.violations == 0


# -------------------------------------------------------------------------- the freeze
def test_the_frozen_book_holds_nothing_from_the_decision_day_on():
    raw = SyntheticSource(
        seed=5, n_customers=100, n_invoices=1200, as_of=date(2026, 6, 1)
    ).extract()
    tables = {
        "customers": raw.customers,
        "invoices": raw.invoices,
        "collection_events": raw.events,
        "agreements": raw.agreements,
    }
    day = date(2026, 2, 1)
    frozen = freeze(tables, day)
    invoices = frozen["invoices"]
    assert (invoices["issue_date"] < day).all()
    assert all(p is None or p < day for p in invoices["payment_date"])
    assert (pd.to_datetime(frozen["collection_events"]["event_time"]) < pd.Timestamp(day)).all()
    unpaid_open = invoices[invoices["payment_date"].isna() & (invoices["due_date"] < day)]
    assert (unpaid_open["days_late"] == [(day - d).days for d in unpaid_open["due_date"]]).all()


def test_unlabelled_features_keep_the_open_book_with_no_target(dataset):
    tables = {
        "customers": dataset.customers,
        "invoices": dataset.invoices,
        "collection_events": dataset.events,
        "agreements": dataset.agreements,
    }
    labelled = build_features("propensity", tables)
    unlabelled = build_features("propensity", tables, labelled=False)
    assert len(unlabelled) == len(dataset.invoices) > len(labelled)
    assert unlabelled["target_payment_propensity"].isna().all()
    # The same receivable gets the same features either way: only the population differs.
    shared = labelled["invoice_id"].iloc[0]
    a = labelled[labelled["invoice_id"] == shared].drop(columns=["target_payment_propensity"])
    b = unlabelled[unlabelled["invoice_id"] == shared].drop(columns=["target_payment_propensity"])
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))


def test_the_rankers_have_no_view_of_the_future(population):
    # An AUC near 1 is the signature of a leak: the scores knew the outcome.
    assert 0.5 < population.auc_overdue < 0.95
    assert 0.5 < population.auc_default < 0.95


# --------------------------------------------------------------------------- the audit
def _contact(moment: datetime, attempt: int = 1, index: int = 0, customer: str = "C1") -> Contact:
    return Contact(
        day=moment.date(),
        moment=moment,
        index=index,
        invoice_id=f"D{index}",
        customer_id=customer,
        attempt=attempt,
        channel="chat",
        cost=0.12,
        preventive=False,
        outcome="no_effect",
    )


def test_the_auditor_catches_what_the_policy_should_have_refused():
    monday = datetime(2026, 3, 2, 10, tzinfo=ZONE)
    run = Run(arm="semi", seed=0, capacity=None)
    run.contacts = [
        _contact(monday - timedelta(days=1)),  # Sunday
        _contact(monday.replace(hour=21)),  # after hours, and 35h after Sunday's
        _contact(monday + timedelta(days=1), attempt=5, index=1, customer="C2"),
        _contact(monday + timedelta(days=2)),  # 37h after the 21:00 contact
        _contact(monday + timedelta(days=8), index=2, customer="C3"),
    ]
    run.opted_out_at["C3"] = monday
    found = audit(run)
    assert found["sunday"] == 1
    assert found["outside_hours"] == 1
    assert found["over_max_attempts"] == 1
    assert found["inside_cooldown"] == 2
    assert found["after_opt_out"] == 1


# ----------------------------------------------------------------- statistics and report
def test_the_paired_interval_brackets_the_mean_and_signs_the_effect():
    a = np.array([0.60, 0.62, 0.65, 0.61, 0.63])
    b = a - 0.02
    estimate = paired(a, b)
    assert estimate.low <= estimate.mean <= estimate.high
    assert estimate.mean == pytest.approx(0.02)
    single = bootstrap(np.array([1.0, 2.0, 3.0]))
    assert single.low <= 2.0 <= single.high


def test_rewriting_the_report_keeps_the_reading_around_the_tables(tmp_path):
    results = run_experiment(2, 11, **SMALL)
    path = tmp_path / "experiment-results.md"
    path.write_text(
        f"# Title\n\nHand-written reading.\n\n{report.START}\nold tables\n{report.END}\n\nLimits.\n"
    )
    report.write(results, path, tmp_path / "images")
    text = path.read_text()
    assert "Hand-written reading." in text and "Limits." in text
    assert "old tables" not in text
    assert "system − semi-automated" in text
    assert "ranking only − semi-automated" in text
    assert "0 violations" in text
    assert (tmp_path / "images" / "experiment-capacity.png").exists()


# ------------------------------------------------------------------------ concurrency
def test_the_concurrency_check_holds_conversations_against_a_served_app(tmp_path):
    levels = load.run_load((3,), turns=2)
    level = levels[0]
    assert level.requests == 3 * 3
    assert level.errors == {}
    path = tmp_path / "experiment-results.md"
    path.write_text("# Results\n\nReading.\n")
    load.write(levels, path)
    text = path.read_text()
    assert "Reading." in text and load.START in text and "| 3 | 9 | 0 |" in text
