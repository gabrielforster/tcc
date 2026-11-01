"""Score a tenant's customers against the platform's current model.

The tenant never trains: its run imports and indexes its own data, then asks which model
the platform has published and applies it. The model is read through the tenant's own
session because `platform_models` is not a tenant-scoped table, so any session may ask
which version is current; the weights come from the platform prefix, which the tenant's
store could not reach and this does not need it to.

A customer's score is the mean predicted probability over the receivables the feature
table can build for them. The feature table only holds receivables whose outcome is
already observable (that is what keeps the temporal-leakage rule intact), so a customer
whose every receivable is still too recent to label has no score yet, and gets no row
rather than a made-up one. Rescoring against the same model version replaces its rows;
a newer version adds its own and leaves what an earlier decision was based on.
"""

from datetime import UTC, datetime

from collection.data.ingest import load_interim
from collection.db.models.customer import Customer
from collection.db.models.platform_model import CustomerScore
from collection.db.repository import TenantScope
from collection.features.build import build_features
from collection.models.store import latest_model, read_bundle


def score_customers(scope: TenantScope, task: str) -> int:
    """Write one `CustomerScore` per scoreable customer and return how many."""
    model = latest_model(scope.session, task)
    if model is None:
        raise LookupError(f"No model for '{task}'. Run `collection train` first.")
    bundle = read_bundle(model)

    features = build_features(task, load_interim(scope))
    probability = bundle["pipeline"].predict_proba(features[bundle["features"]])[:, 1]
    per_customer = (
        features[["customer_id"]].assign(score=probability).groupby("customer_id")["score"].mean()
    )

    by_pseudonym = {c.pseudonym: c for c in scope.all(Customer)}
    scored = {str(key) for key in per_customer.index}
    # One pseudonym that names no row is ordinary: a customer can be removed between an
    # ingest and a scoring run, and the loop below skips it. None of them matching is a
    # different thing -- the stored frames and the rows were pseudonymized under different
    # keys -- and skipping that quietly writes no scores and reports success.
    if scored and by_pseudonym and not scored & by_pseudonym.keys():
        raise LookupError(
            "None of the scored pseudonyms names one of this tenant's customers: the "
            "stored frames and the rows were anonymized under different keys. Re-run "
            "`collection ingest` to rewrite both."
        )

    for old in scope.all(CustomerScore, CustomerScore.platform_model_id == model.id):
        scope.session.delete(old)
    scope.session.flush()

    scored_at = datetime.now(UTC)
    written = 0
    for pseudonym, score in per_customer.items():
        customer = by_pseudonym.get(str(pseudonym))
        if customer is None:
            continue
        scope.add(
            CustomerScore(
                customer_id=customer.id,
                platform_model_id=model.id,
                score=float(score),
                scored_at=scored_at,
            )
        )
        written += 1
    scope.session.flush()
    return written
