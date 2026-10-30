"""Pseudonymization and data minimization under the LGPD (deliverable 3).

Strategy, aligned with LGPD art. 12:

* direct identifiers (name, tax id, e-mail, phone) are dropped;
* keys (customer_id, invoice_id, ...) become HMAC-SHA256 pseudonyms under a key that is
  derived per tenant, preserving the relationships across tables while blocking
  re-identification by anyone without the salt;
* quasi-identifiers are generalized (city dropped, only the state is kept; signup date
  reduced to the month; document number reduced to its year).

The salt lives in ANONYMIZATION_SALT and is never committed. Changing it makes two
extractions impossible to join, which is the desired behaviour across environments.

The key is derived per tenant rather than taken from the salt directly, so the same
customer document is a different pseudonym in every tenant. Under one global key the
pseudonym is a join key: two tenants that share a debtor -- which is the normal case in
collections, where one person owes several creditors -- carry the same token for them, and
anyone holding both tenants' anonymized tables can line them up without ever learning a
name. Namespacing by tenant is what makes a pseudonym mean "this tenant's customer 7"
rather than "customer 7". It also closes the gap `platform_model.py` records against
pooled training: frames from two tenants can now be stacked without their keys colliding.

Rotating to per-tenant keys is a salt rotation by another name: pseudonyms minted before
it do not match the ones minted after. Both sides of the join are rewritten by a re-run of
`collection ingest` -- the `interim/` objects and the `customers.pseudonym` column -- so
re-ingesting is what carries a tenant across, and `score_customers` refuses to score
rather than quietly matching nothing if only one side has moved.
"""

import hashlib
import hmac
import uuid

import pandas as pd

from collection.crypto import derive_key
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


def _pseudonym_key(tenant: uuid.UUID) -> bytes:
    """One tenant's pseudonymization key. Never stored: derived when it is needed.

    Not to be confused with `Tenant.tenant_key`, which is the public identifier a widget
    embed carries. This one is a secret and never leaves the process.
    """
    return derive_key("pseudonym", str(tenant))


def pseudonym(value: object, prefix: str = "", *, tenant: uuid.UUID) -> str:
    """Truncated HMAC-SHA256. Deterministic for a given salt and tenant.

    `tenant` is keyword-only and has no default on purpose: a pseudonym minted without
    one would be comparable across tenants, and that is exactly what this prevents.
    """
    digest = hmac.new(_pseudonym_key(tenant), str(value).encode(), hashlib.sha256).hexdigest()[:16]
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


def anonymize(dataset: RawDataset, *, tenant: uuid.UUID) -> dict[str, pd.DataFrame]:
    """Apply suppression, pseudonymization and generalization to the four tables."""
    output: dict[str, pd.DataFrame] = {}
    for name, df in dataset.as_dict().items():
        df = df.copy()
        df = df.drop(columns=[c for c in DROPPED if c in df.columns])
        for key in PSEUDONYMIZED_KEYS & set(df.columns):
            df[key] = df[key].map(lambda v, p=KEY_PREFIXES[key]: pseudonym(v, p, tenant=tenant))
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
                treatment = "pseudonymized (HMAC-SHA256, per-tenant key)"
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
