"""Pseudonymization and data minimization under the LGPD (deliverable 3).

Strategy, aligned with LGPD art. 12:

* direct identifiers (name, tax id, e-mail, phone) are dropped;
* keys (customer_id, invoice_id, ...) become HMAC-SHA256 pseudonyms under a secret salt,
  preserving the relationships across tables while blocking re-identification by anyone
  without the salt;
* quasi-identifiers are generalized (city dropped, only the state is kept; signup date
  reduced to the month; document number reduced to its year).

The salt lives in ANONYMIZATION_SALT and is never committed. Changing it makes two
extractions impossible to join, which is the desired behaviour across environments.
"""

import hashlib
import hmac

import pandas as pd

from collection.config import settings
from collection.data.sources.base import RawDataset
from collection.domain.schema import TABLES

PSEUDONYMIZED_KEYS = {"customer_id", "invoice_id", "event_id", "agreement_id"}
DROPPED = {"name", "tax_id", "email", "phone"}
KEY_PREFIXES = {
    "customer_id": "cus_",
    "invoice_id": "inv_",
    "event_id": "evt_",
    "agreement_id": "agr_",
}


def pseudonym(value: object, prefix: str = "") -> str:
    """Truncated HMAC-SHA256. Deterministic for a given salt."""
    digest = hmac.new(
        settings.anonymization_salt.encode(),
        str(value).encode(),
        hashlib.sha256,
    ).hexdigest()[:16]
    return f"{prefix}{digest}"


def _generalize(table_name: str, df: pd.DataFrame) -> pd.DataFrame:
    if table_name == "customers" and "signup_date" in df:
        df["signup_date"] = pd.to_datetime(df["signup_date"]).dt.to_period("M").astype(str)
        df = df.drop(columns=["city"], errors="ignore")
    if table_name == "invoices" and "document_number" in df:
        df["document_number"] = (
            df["document_number"].astype(str).str.extract(r"(\d{4})", expand=False)
        )
        df = df.rename(columns={"document_number": "document_year"})
    return df


def anonymize(dataset: RawDataset) -> dict[str, pd.DataFrame]:
    """Apply suppression, pseudonymization and generalization to the four tables."""
    output: dict[str, pd.DataFrame] = {}
    for name, df in dataset.as_dict().items():
        df = df.copy()
        df = df.drop(columns=[c for c in DROPPED if c in df.columns])
        for key in PSEUDONYMIZED_KEYS & set(df.columns):
            df[key] = df[key].map(lambda v, p=KEY_PREFIXES[key]: pseudonym(v, p))
        output[name] = _generalize(name, df)
    return output


def lgpd_report() -> pd.DataFrame:
    """Per-field treatment table, ready to be attached to the paper."""
    rows = []
    for table in TABLES:
        for field in table.fields:
            if field.name in DROPPED:
                treatment = "dropped"
            elif field.name in PSEUDONYMIZED_KEYS:
                treatment = "pseudonymized (HMAC-SHA256 + salt)"
            elif field.sensitivity == "quasi_id":
                treatment = "generalized"
            else:
                treatment = "kept"
            rows.append(
                {
                    "table": table.name,
                    "field": field.name,
                    "sensitivity": field.sensitivity,
                    "treatment": treatment,
                }
            )
    return pd.DataFrame(rows)
