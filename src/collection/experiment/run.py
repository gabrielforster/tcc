"""Replicate the experiment: every arm, every capacity, every response scenario, per seed.

A replication builds one population and runs every configuration on it. That is the
pairing the statistics rely on: within a seed, two arms differ only in the arm, never in the
customers, the model's scores or the random draws that decide how a contact is answered.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from collection.experiment.metrics import Outcome, outcome
from collection.experiment.population import Population, build_population
from collection.experiment.simulate import Response, simulate

#: Contacts per day. Unlimited (None) is the automated arms' natural mode; a desk is not.
CAPACITIES: tuple[int | None, ...] = (2, 4, 8, 16, None)
#: The main analysis and the two sensitivity scenarios (protocol §6).
SCENARIOS: dict[str, Response] = {
    "main": Response(),
    "homogeneous": Response(heterogeneous=False),
    "chat_half": Response(chat_factor=0.5),
}
DEFAULT_REPLICATIONS = 30
FIRST_SEED = 1


@dataclass
class PopulationSummary:
    seed: int
    receivables: int
    at_risk: int
    at_risk_amount: float
    opted_out: int
    auc_overdue: float
    auc_default: float


@dataclass
class Results:
    replications: int
    first_seed: int
    #: (scenario, arm, capacity) -> one outcome per seed, in seed order.
    outcomes: dict[tuple[str, str, int | None], list[Outcome]] = field(default_factory=dict)
    populations: list[PopulationSummary] = field(default_factory=list)

    def series(self, scenario: str, arm: str, capacity: int | None) -> list[Outcome]:
        return self.outcomes[(scenario, arm, capacity)]


def _summary(population: Population) -> PopulationSummary:
    at_risk = population.at_risk()
    return PopulationSummary(
        seed=population.seed,
        receivables=len(population.visible),
        at_risk=len(at_risk),
        at_risk_amount=sum(population.visible[i].amount for i in at_risk),
        opted_out=sum(v.opted_out for v in population.visible),
        auc_overdue=population.auc_overdue,
        auc_default=population.auc_default,
    )


def run_replication(population: Population, results: Results) -> None:
    for scenario, response in SCENARIOS.items():
        results.outcomes.setdefault((scenario, "none", None), []).append(
            outcome(population, simulate(population, "none", response=response))
        )
        for capacity in CAPACITIES:
            for arm in ("manual", "semi", "full"):
                if arm == "manual" and capacity is None:
                    continue
                run = simulate(population, arm, capacity=capacity, response=response)
                results.outcomes.setdefault((scenario, arm, capacity), []).append(
                    outcome(population, run)
                )


def run_experiment(
    replications: int = DEFAULT_REPLICATIONS,
    first_seed: int = FIRST_SEED,
    *,
    n_customers: int = 1200,
    n_invoices: int = 12000,
    progress: Callable[[int], None] | None = None,
) -> Results:
    results = Results(replications=replications, first_seed=first_seed)
    for seed in range(first_seed, first_seed + replications):
        population = build_population(seed, n_customers=n_customers, n_invoices=n_invoices)
        results.populations.append(_summary(population))
        run_replication(population, results)
        if progress:
            progress(seed)
    return results
