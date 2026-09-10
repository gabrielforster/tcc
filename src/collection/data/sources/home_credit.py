"""Home Credit Default Risk as a source of real payment behavior.

https://www.kaggle.com/competitions/home-credit-default-risk

`installments_payments.csv` is the closest public analogue to our receivables table: one
row per scheduled installment, with the scheduled date and amount alongside what was
actually paid and when, and many installments per client. That is exactly the shape tasks
1 and 2 need, produced by real borrowers instead of by our generator.

Access: the competition data is behind a Kaggle account and its rules must be accepted,
so the files cannot be downloaded automatically or committed here. Put them in
`data/raw/home-credit/` (see the error message this source raises) and check the
competition's licence terms before publishing anything derived from it.

Limitations that must survive into the paper:

* It populates `customers` and `invoices` only -- there are no collection contacts, so the
  active module cannot be evaluated on it.
* **There is no absolute calendar.** Every `DAYS_*` column is an offset relative to that
  client's own application date, and the application dates themselves are not in the data.
  All offsets are therefore anchored to one shared reference date, which means
  `reference_date` measures time relative to application, not wall-clock time. The
  chronological split stays leak-free in that relative ordering, but two consequences
  follow: seasonality analysis is meaningless on this source, and the split does not
  reproduce the "train on the past, score the future" calendar that the ERP data would.
* Consumer lending, not B2B receivables, and a different population from Bank Marketing --
  the two public sources cannot be joined.
"""

import numpy as np
import pandas as pd

from collection.config import settings
from collection.data.sources.base import DataSource, RawDataset
from collection.data.sources.download import require_local_files
from collection.domain.schema import DEFAULT_THRESHOLD_DAYS

DATASET = "home-credit"
REQUIRED_FILES = ["application_train.csv", "installments_payments.csv"]

INSTRUCTIONS = """Home Credit data is behind a Kaggle account, so it cannot be fetched
automatically.

  1. Accept the competition rules at
     https://www.kaggle.com/competitions/home-credit-default-risk/rules
  2. Download and unzip, then copy these files into data/raw/home-credit/:
       application_train.csv
       installments_payments.csv

  With the Kaggle CLI (pip install kaggle, credentials in ~/.kaggle/kaggle.json):
     kaggle competitions download -c home-credit-default-risk -f application_train.csv \\
       -p data/raw/home-credit/
     kaggle competitions download -c home-credit-default-risk -f installments_payments.csv \\
       -p data/raw/home-credit/"""

# Day 0 of the shared timeline. Arbitrary but fixed, so runs are reproducible.
ANCHOR = pd.Timestamp("2018-06-01")

# The application's stated income bracket stands in for customer size.
SIZE_BOUNDS = [0, 112_500, 225_000, np.inf]
SIZE_LABELS = ["small", "medium", "large"]

# Installments have no issue date; the term is assumed to be the standard monthly cycle.
ASSUMED_TERM_DAYS = 30


class HomeCreditSource(DataSource):
    name = "home-credit"
    provides = frozenset({"customers", "invoices"})

    def __init__(self, n_customers: int | None = 5_000, seed: int = 42) -> None:
        #: The full dataset is 13.6M installment rows; sampling clients keeps a run
        #: workable. None loads everything.
        self.n_customers = n_customers
        self.seed = seed

    def extract(self) -> RawDataset:
        directory = require_local_files(DATASET, REQUIRED_FILES, INSTRUCTIONS)

        applications = pd.read_csv(
            directory / "application_train.csv",
            usecols=[
                "SK_ID_CURR",
                "AMT_CREDIT",
                "AMT_INCOME_TOTAL",
                "NAME_INCOME_TYPE",
                "NAME_CONTRACT_TYPE",
                "DAYS_REGISTRATION",
            ],
        )
        if self.n_customers is not None and self.n_customers < len(applications):
            applications = applications.sample(self.n_customers, random_state=self.seed)
        keep = set(applications["SK_ID_CURR"])

        installments = pd.read_csv(
            directory / "installments_payments.csv",
            usecols=[
                "SK_ID_PREV",
                "SK_ID_CURR",
                "NUM_INSTALMENT_NUMBER",
                "DAYS_INSTALMENT",
                "DAYS_ENTRY_PAYMENT",
                "AMT_INSTALMENT",
                "AMT_PAYMENT",
            ],
        )
        installments = installments[installments["SK_ID_CURR"].isin(keep)]

        customers = self._customers(applications)
        invoices = self._invoices(installments)
        # Drop clients whose installments were filtered out, so no receivable is orphaned.
        customers = customers[customers["customer_id"].isin(set(invoices["customer_id"]))]

        return RawDataset(
            customers=customers,
            invoices=invoices,
            events=self.empty_table("collection_events"),
            agreements=self.empty_table("agreements"),
            provides=self.provides,
        )

    def _customers(self, applications: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "customer_id": applications["SK_ID_CURR"].astype(str),
                "name": "",
                "tax_id": "",
                "email": "",
                "phone": "",
                "city": "",
                "state": "",
                "signup_date": ANCHOR
                + pd.to_timedelta(applications["DAYS_REGISTRATION"], unit="D"),
                "segment": applications["NAME_INCOME_TYPE"],
                "size_tier": pd.cut(
                    applications["AMT_INCOME_TOTAL"], bins=SIZE_BOUNDS, labels=SIZE_LABELS
                ).astype(str),
                "credit_limit": applications["AMT_CREDIT"],
                "preferred_channel": "unknown",
                "opt_out": False,
            }
        )

    def _invoices(self, installments: pd.DataFrame) -> pd.DataFrame:
        due = ANCHOR + pd.to_timedelta(installments["DAYS_INSTALMENT"], unit="D")
        paid = ANCHOR + pd.to_timedelta(installments["DAYS_ENTRY_PAYMENT"], unit="D")
        # A missing entry date is a scheduled installment that was never paid.
        paid = paid.where(installments["AMT_PAYMENT"].notna())

        invoices = pd.DataFrame(
            {
                "invoice_id": installments["SK_ID_PREV"].astype(str)
                + "-"
                + installments["NUM_INSTALMENT_NUMBER"].astype(int).astype(str),
                "customer_id": installments["SK_ID_CURR"].astype(str),
                "document_number": installments["SK_ID_PREV"].astype(str),
                "amount": installments["AMT_INSTALMENT"],
                "issue_date": due - pd.Timedelta(days=ASSUMED_TERM_DAYS),
                "due_date": due,
                "payment_date": paid,
                "payment_method": "unknown",
            }
        )
        invoices = invoices[invoices["amount"] > 0].drop_duplicates(subset="invoice_id")

        as_of = pd.Series(invoices["payment_date"].max(), index=invoices.index)
        reference = invoices["payment_date"].fillna(as_of)
        invoices["days_late"] = (reference - invoices["due_date"]).dt.days.clip(lower=0)
        invoices["status"] = np.select(
            [
                invoices["payment_date"].notna() & (invoices["days_late"] <= 0),
                invoices["payment_date"].notna(),
                invoices["days_late"] > DEFAULT_THRESHOLD_DAYS,
            ],
            ["paid", "paid_late", "defaulted"],
            default="open",
        )
        return invoices.sort_values("due_date").reset_index(drop=True)


def sample_is_available() -> bool:
    """Whether the Kaggle files are already in place."""
    directory = settings.dir_raw / DATASET
    return all((directory / f).exists() for f in REQUIRED_FILES)
