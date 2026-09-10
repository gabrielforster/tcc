"""Synthetic collection dataset generator.

This is the fallback foreseen in the schedule while ERP access is pending. Rather than a
public credit-scoring dataset, which has neither receivables nor collection events, the
generator reproduces the real domain schema. That way the whole pipeline (EDA, features,
temporal split, models, API) is written against the final format and swapping in the real
data stays confined to `ERPSource`.

The generating process embeds the signals the predictive module is supposed to learn:
latent customer propensity, delinquency history, amount relative to the credit limit,
seasonality and the effect of collection contacts. None of those latent variables reach
the tables -- the model only sees what the ERP would also expose.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from collection.config import settings
from collection.data.sources.base import DataSource, RawDataset
from collection.domain.schema import DEFAULT_THRESHOLD_DAYS

SEGMENTS = ["retail", "wholesale", "services"]
SIZE_TIERS = ["small", "medium", "large"]
CHANNELS = ["whatsapp", "phone", "email", "sms"]
PAYMENT_METHODS = ["boleto", "pix", "card", "transfer"]
STATES = ["SC", "PR", "RS", "SP", "MG"]
CITIES = {
    "SC": ["Jaragua do Sul", "Joinville", "Blumenau", "Florianopolis"],
    "PR": ["Curitiba", "Londrina", "Maringa"],
    "RS": ["Porto Alegre", "Caxias do Sul"],
    "SP": ["Sao Paulo", "Campinas", "Santos"],
    "MG": ["Belo Horizonte", "Uberlandia"],
}
FIRST_NAMES = [
    "Ana",
    "Bruno",
    "Carla",
    "Diego",
    "Elisa",
    "Fabio",
    "Gisele",
    "Heitor",
    "Iara",
    "Joao",
    "Karina",
    "Lucas",
    "Marina",
    "Nelson",
    "Olivia",
    "Paulo",
]
LAST_NAMES = [
    "Silva",
    "Souza",
    "Oliveira",
    "Pereira",
    "Costa",
    "Rodrigues",
    "Almeida",
    "Nascimento",
    "Lima",
    "Araujo",
    "Fernandes",
    "Ribeiro",
]

# Days late at which the current process fires a contact attempt.
CONTACT_TRIGGERS = [3, 10, 20, 35, 65, 95]

CONTACT_COST = {"whatsapp": 0.12, "sms": 0.09, "email": 0.01, "phone": 1.4}


class SyntheticSource(DataSource):
    name = "synthetic"

    def __init__(
        self,
        n_customers: int | None = None,
        n_invoices: int | None = None,
        seed: int | None = None,
        as_of: date | None = None,
        horizon_months: int = 30,
    ) -> None:
        self.n_customers = n_customers or settings.synthetic_n_customers
        self.n_invoices = n_invoices or settings.synthetic_n_invoices
        self.seed = settings.synthetic_seed if seed is None else seed
        self.as_of = as_of or date.today()
        self.start = self.as_of - timedelta(days=30 * horizon_months)
        self.rng = np.random.default_rng(self.seed)

    # ----------------------------------------------------------------- customers
    def _make_customers(self) -> tuple[pd.DataFrame, np.ndarray]:
        rng = self.rng
        n = self.n_customers
        segment = rng.choice(SEGMENTS, n, p=[0.55, 0.2, 0.25])
        size_tier = rng.choice(SIZE_TIERS, n, p=[0.6, 0.3, 0.1])

        # Latent propensity to pay: never reaches the tables, only drives the process.
        base = rng.beta(2.2, 1.6, n)
        bonus = np.where(segment == "wholesale", 0.06, 0.0) + np.where(
            size_tier == "large", 0.08, 0.0
        )
        propensity = np.clip(base + bonus - np.where(segment == "retail", 0.05, 0.0), 0.02, 0.98)

        base_limit = {"small": 8_000, "medium": 35_000, "large": 120_000}
        limit = np.array([base_limit[s] for s in size_tier]) * rng.lognormal(0, 0.35, n)

        states = rng.choice(STATES, n, p=[0.45, 0.2, 0.12, 0.15, 0.08])
        cities = [rng.choice(CITIES[uf]) for uf in states]
        days_since_signup = rng.integers(180, 3600, n)

        customers = pd.DataFrame(
            {
                "customer_id": [f"C{i:06d}" for i in range(1, n + 1)],
                "name": [
                    f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)} {rng.choice(LAST_NAMES)}"
                    for _ in range(n)
                ],
                "tax_id": [self._tax_id(rng) for _ in range(n)],
                "email": "",
                "phone": [
                    f"+55{rng.integers(41, 55)}9{rng.integers(10**7, 10**8 - 1)}" for _ in range(n)
                ],
                "city": cities,
                "state": states,
                "signup_date": [self.as_of - timedelta(days=int(d)) for d in days_since_signup],
                "segment": segment,
                "size_tier": size_tier,
                "credit_limit": np.round(limit, 2),
                "preferred_channel": rng.choice(CHANNELS, n, p=[0.6, 0.15, 0.15, 0.1]),
                "opt_out": rng.random(n) < 0.03,
            }
        )
        customers["email"] = [
            f"{name.split()[0].lower()}.{cid.lower()}@example.com.br"
            for name, cid in zip(customers["name"], customers["customer_id"], strict=True)
        ]
        return customers, propensity

    @staticmethod
    def _tax_id(rng: np.random.Generator) -> str:
        d = rng.integers(0, 10, 11)
        return f"{d[0]}{d[1]}{d[2]}.{d[3]}{d[4]}{d[5]}.{d[6]}{d[7]}{d[8]}-{d[9]}{d[10]}"

    # ------------------------------------------------------------------ invoices
    def _make_invoices(self, customers: pd.DataFrame, propensity: np.ndarray) -> pd.DataFrame:
        rng = self.rng
        n_inv = self.n_invoices
        n_cus = len(customers)

        # Some customers buy far more often than others (long tail).
        weight = rng.lognormal(0, 0.8, n_cus)
        idx = rng.choice(n_cus, n_inv, p=weight / weight.sum())

        period_days = (self.as_of - self.start).days
        offset = rng.integers(0, period_days, n_inv)
        issue = np.array([self.start + timedelta(days=int(d)) for d in offset])
        term = rng.choice([15, 28, 30, 45, 60], n_inv, p=[0.1, 0.15, 0.45, 0.2, 0.1])
        due = np.array([i + timedelta(days=int(t)) for i, t in zip(issue, term, strict=True)])

        limit = customers["credit_limit"].to_numpy()[idx]
        amount = np.round(np.clip(limit * rng.beta(1.6, 6.0, n_inv), 45, None), 2)
        relative_amount = amount / limit

        prop = propensity[idx]
        month = np.array([d.month for d in due])
        # Seasonality: January/February (post-holidays) and July squeeze cash flow.
        pressure = np.where(np.isin(month, [1, 2]), 0.12, 0.0) + np.where(month == 7, 0.06, 0.0)

        p_on_time = np.clip(
            prop - 0.9 * relative_amount - pressure + rng.normal(0, 0.06, n_inv), 0.02, 0.97
        )
        on_time = rng.random(n_inv) < p_on_time

        # For late payers, the size of the delay shrinks as propensity grows.
        scale = 6 + 55 * (1 - prop)
        latent_delay = np.ceil(rng.gamma(1.5, scale / 1.5)).astype(int)
        # The share that never pays grows sharply in the low-propensity tail.
        never_pays = rng.random(n_inv) < np.clip(0.55 * (1 - prop) ** 2, 0.01, 0.5)

        # On-time payers settle up to 6 days early; late payers follow the gamma delay.
        early = rng.integers(0, 7, n_inv)
        shift = np.where(on_time, -early, latent_delay)
        unpaid = never_pays & ~on_time
        payment: list[date | None] = [
            None if skip else d + timedelta(days=int(s))
            for d, s, skip in zip(due, shift, unpaid, strict=True)
        ]

        invoices = pd.DataFrame(
            {
                "invoice_id": [f"D{i:07d}" for i in range(1, n_inv + 1)],
                "customer_id": customers["customer_id"].to_numpy()[idx],
                "document_number": [f"NF-{d.year}-{i:06d}" for i, d in enumerate(issue, start=1)],
                "amount": amount,
                "issue_date": issue,
                "due_date": due,
                "payment_date": payment,
                "payment_method": rng.choice(PAYMENT_METHODS, n_inv, p=[0.4, 0.35, 0.15, 0.1]),
            }
        )
        # A payment in the future has not happened yet: the ERP only knows the past.
        future = invoices["payment_date"].apply(lambda d: d is not None and d > self.as_of)
        invoices.loc[future, "payment_date"] = None

        invoices["days_late"] = [
            (pay - d).days if pay is not None else (self.as_of - d).days
            for pay, d in zip(invoices["payment_date"], invoices["due_date"], strict=True)
        ]
        invoices["days_late"] = invoices["days_late"].clip(lower=0)
        invoices["status"] = [
            self._status(pay, d, late)
            for pay, d, late in zip(
                invoices["payment_date"], invoices["due_date"], invoices["days_late"], strict=True
            )
        ]
        return invoices.sort_values("due_date").reset_index(drop=True)

    def _status(self, payment: date | None, due: date, days_late: int) -> str:
        if payment is not None:
            return "paid" if days_late <= 0 else "paid_late"
        if due > self.as_of:
            return "open"
        return "defaulted" if days_late > DEFAULT_THRESHOLD_DAYS else "open"

    # ------------------------------------------------------- events and agreements
    def _make_events(
        self, invoices: pd.DataFrame, customers: pd.DataFrame, propensity: np.ndarray
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        rng = self.rng
        propensity_by_customer = dict(zip(customers["customer_id"], propensity, strict=True))
        opt_out = dict(zip(customers["customer_id"], customers["opt_out"], strict=True))
        preferred = dict(zip(customers["customer_id"], customers["preferred_channel"], strict=True))

        events: list[dict] = []
        agreements: list[dict] = []
        for inv in invoices.itertuples(index=False):
            if inv.days_late <= 0 or opt_out[inv.customer_id]:
                continue
            prop = propensity_by_customer[inv.customer_id]
            stop = False
            for trigger in CONTACT_TRIGGERS:
                if trigger > inv.days_late or stop:
                    break
                event_day = inv.due_date + timedelta(days=trigger)
                if event_day > self.as_of:
                    break
                channel = (
                    preferred[inv.customer_id]
                    if trigger <= 20
                    else rng.choice(CHANNELS, p=[0.4, 0.4, 0.1, 0.1])
                )
                p_reply = np.clip(0.25 + 0.5 * prop, 0.05, 0.9)
                if rng.random() > p_reply:
                    outcome = "no_answer"
                else:
                    outcome = str(
                        rng.choice(
                            ["replied", "promised_to_pay", "disputed", "opt_out"],
                            p=[0.35, 0.45, 0.15, 0.05],
                        )
                    )
                    stop = outcome == "opt_out"
                events.append(
                    {
                        "event_id": f"E{len(events) + 1:08d}",
                        "invoice_id": inv.invoice_id,
                        "customer_id": inv.customer_id,
                        "event_time": pd.Timestamp(event_day)
                        + pd.Timedelta(
                            hours=int(rng.integers(9, 18)), minutes=int(rng.integers(0, 60))
                        ),
                        "channel": channel,
                        "kind": self._contact_kind(trigger),
                        "delinquency_stage": delinquency_stage(trigger),
                        "outcome": outcome,
                        "contact_cost": CONTACT_COST[channel],
                    }
                )
                if outcome == "promised_to_pay" and trigger >= 20 and rng.random() < 0.35:
                    settled = inv.payment_date is not None
                    agreements.append(
                        {
                            "agreement_id": f"A{len(agreements) + 1:07d}",
                            "customer_id": inv.customer_id,
                            "invoice_id": inv.invoice_id,
                            "agreement_date": event_day,
                            "agreement_amount": round(
                                float(inv.amount) * float(rng.uniform(1.0, 1.18)), 2
                            ),
                            "installments": int(
                                rng.choice([1, 2, 3, 6], p=[0.35, 0.25, 0.25, 0.15])
                            ),
                            "status": "fulfilled"
                            if settled
                            else str(rng.choice(["current", "broken"], p=[0.35, 0.65])),
                        }
                    )
                    stop = True

        event_columns = [
            "event_id",
            "invoice_id",
            "customer_id",
            "event_time",
            "channel",
            "kind",
            "delinquency_stage",
            "outcome",
            "contact_cost",
        ]
        agreement_columns = [
            "agreement_id",
            "customer_id",
            "invoice_id",
            "agreement_date",
            "agreement_amount",
            "installments",
            "status",
        ]
        return pd.DataFrame(events, columns=event_columns), pd.DataFrame(
            agreements, columns=agreement_columns
        )

    @staticmethod
    def _contact_kind(days: int) -> str:
        if days <= 7:
            return "reminder"
        if days <= 30:
            return "collection"
        if days <= 60:
            return "negotiation"
        return "notice"

    # ----------------------------------------------------------------------- api
    def extract(self) -> RawDataset:
        customers, propensity = self._make_customers()
        invoices = self._make_invoices(customers, propensity)
        events, agreements = self._make_events(invoices, customers, propensity)
        return RawDataset(
            customers=customers, invoices=invoices, events=events, agreements=agreements
        )


def delinquency_stage(days: int) -> str:
    """Delinquency bucket used by the outbound templates (deliverable 14)."""
    if days <= 0:
        return "pre_due"
    if days <= 7:
        return "1-7"
    if days <= 30:
        return "8-30"
    if days <= 60:
        return "31-60"
    return "60+"
