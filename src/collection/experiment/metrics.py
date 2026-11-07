"""What a run achieved, and the statistics that compare two arms over replications.

The outcome is read over the receivables that are **at risk** -- not paid by their due date
had nobody done anything. That denominator is the same for every arm in a replication, so
a difference in recovery is a difference the arm made, not a difference in what it was
asked to recover. Self-cure counts towards every arm alike; `incremental` is what is left
after subtracting it, which is the number an operation actually buys.
"""

from dataclasses import dataclass

import numpy as np

from collection.domain.schema import PROPENSITY_WINDOW_DAYS
from collection.experiment.audit import violations
from collection.experiment.population import Population
from collection.experiment.simulate import Run

BOOTSTRAP_RESAMPLES = 10_000


@dataclass(frozen=True)
class Outcome:
    arm: str
    seed: int
    capacity: int | None
    at_risk_amount: float
    recovered_amount: float
    recovered_30_amount: float
    #: Recovered at-risk amount over at-risk amount, by the end of the horizon.
    recovery_rate: float
    #: The same, counting only payments within 30 days of the due date -- the propensity
    #: task's window, and the one where a contact's timing shows.
    recovery_rate_30: float
    #: Days from due date to payment over the at-risk book, unpaid counted as the horizon.
    days_outstanding: float
    contacts: int
    preventive_contacts: int
    cost: float
    violations: int

    @property
    def cost_per_contact(self) -> float:
        return self.cost / self.contacts if self.contacts else 0.0


def outcome(population: Population, run: Run) -> Outcome:
    at_risk = population.at_risk()
    amount = sum(population.visible[i].amount for i in at_risk)
    recovered = recovered_30 = 0.0
    outstanding = []
    for i in at_risk:
        due = population.visible[i].due
        paid = run.paid[i]
        settled = paid is not None and paid <= population.end
        if settled:
            recovered += population.visible[i].amount
            if (paid - due).days <= PROPENSITY_WINDOW_DAYS:
                recovered_30 += population.visible[i].amount
        stop = paid if settled else population.end
        outstanding.append(max((stop - due).days, 0))
    return Outcome(
        arm=run.arm,
        seed=run.seed,
        capacity=run.capacity,
        at_risk_amount=amount,
        recovered_amount=recovered,
        recovered_30_amount=recovered_30,
        recovery_rate=recovered / amount if amount else 0.0,
        recovery_rate_30=recovered_30 / amount if amount else 0.0,
        days_outstanding=float(np.mean(outstanding)) if outstanding else 0.0,
        contacts=len(run.contacts),
        preventive_contacts=sum(c.preventive for c in run.contacts),
        cost=sum(c.cost for c in run.contacts),
        violations=violations(run),
    )


@dataclass(frozen=True)
class Estimate:
    mean: float
    low: float
    high: float
    #: Paired standardized effect (Cohen's d_z): mean difference over its standard deviation.
    effect: float | None = None

    def format(self, digits: int = 3, scale: float = 1.0, signed: bool = False) -> str:
        sign = "+" if signed else ""
        m, lo, hi = self.mean * scale, self.low * scale, self.high * scale
        return f"{m:{sign}.{digits}f} [{lo:{sign}.{digits}f}, {hi:{sign}.{digits}f}]"


def bootstrap(values: np.ndarray, seed: int = 0) -> Estimate:
    """Mean and 95% percentile bootstrap interval over replications."""
    values = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(values), (BOOTSTRAP_RESAMPLES, len(values)))
    means = values[picks].mean(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    return Estimate(mean=float(values.mean()), low=float(low), high=float(high))


def paired(a: np.ndarray, b: np.ndarray, seed: int = 0) -> Estimate:
    """a - b, paired by replication: both arms ran on the same world with the same draws."""
    diff = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
    estimate = bootstrap(diff, seed=seed)
    sd = diff.std(ddof=1) if len(diff) > 1 else 0.0
    effect = float(diff.mean() / sd) if sd > 0 else None
    return Estimate(estimate.mean, estimate.low, estimate.high, effect)
