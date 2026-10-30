"""Where a trained model lives: an object for the weights, a row for what the app asks.

A model is a platform artifact, not a tenant's. `publish` writes the weights and the
metrics as objects under the `platform/` prefix and one `PlatformModel` row that names
them, all through a platform session -- the explicit, auditable exception to the tenant
guard. The version is the next integer for that kind, and the `(kind, version)` constraint
is what stops two simultaneous publishes from sharing one: the second fails instead of
quietly shadowing the first.

The weights are written before the row, so the row never points at nothing. If the row
then fails, an orphan object remains; that is the store's documented trade, and it is
cheaper than the reverse.

The artifact is a pickled bundle (`joblib`), which executes code on load. That is
acceptable here only because the platform prefix is written by the platform's own
training run and never from a tenant's request; a tenant's store cannot reach it. A
bundle from anywhere else must not be loaded this way.

Readers are here too, so the serving path asks the record which model is current and
fetches it from the bucket, rather than looking in a directory.
"""

import io
import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import joblib
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from collection.db.models.platform_model import PlatformModel
from collection.db.session import unit_of_work
from collection.storage.objects import ObjectStore

if TYPE_CHECKING:  # train.py publishes through this module, so it cannot be imported here
    from collection.models.train import FittedTask


def bundle_of(fitted: "FittedTask") -> dict[str, Any]:
    """The serialized contract: what the serving layer validates incoming payloads against."""
    result = fitted.result
    return {
        "pipeline": fitted.pipeline,
        "threshold": result.threshold,
        "target": result.target,
        "task": result.task,
        "source": result.source,
        "champion": result.champion,
        "features": result.feature_columns,
    }


def metrics_of(fitted: "FittedTask") -> dict[str, Any]:
    payload = asdict(fitted.result)
    payload["test"] = asdict(fitted.result.test)
    # Round-tripped so what goes in the JSONB column is exactly what the object holds.
    return json.loads(json.dumps(payload, default=str))


def publish(fitted: "FittedTask", *, session: Session | None = None) -> PlatformModel:
    """Store the model and its metrics and record a new version.

    `session` must be a platform session, to write in the caller's transaction; without
    one the publish opens its own and commits it.
    """
    if session is not None:
        return _publish(fitted, session)
    with unit_of_work(platform=True) as own:
        row = _publish(fitted, own)
        own.flush()
        own.expunge(row)
        return row


def _publish(fitted: "FittedTask", session: Session) -> PlatformModel:
    kind = fitted.result.task
    version = (
        session.scalar(select(func.max(PlatformModel.version)).where(PlatformModel.kind == kind))
        or 0
    ) + 1
    store = ObjectStore(session=session)
    weights = io.BytesIO()
    joblib.dump(bundle_of(fitted), weights)
    artifact = store.put(
        f"{kind}-v{version}.joblib",
        weights.getvalue(),
        kind="models",
        content_type="application/octet-stream",
    )
    metrics = metrics_of(fitted)
    store.put(
        f"{kind}-v{version}.json",
        json.dumps(metrics, indent=2).encode(),
        kind="metrics",
        content_type="application/json",
    )
    row = PlatformModel(kind=kind, version=version, metrics=metrics, artifact_key=artifact.key)
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def latest_model(session: Session, kind: str) -> PlatformModel | None:
    return session.scalars(
        select(PlatformModel)
        .where(PlatformModel.kind == kind)
        .order_by(PlatformModel.version.desc())
        .limit(1)
    ).first()


def read_bundle(row: PlatformModel) -> dict[str, Any]:
    """Fetch and load a model's bundle. See the module docstring on why this is safe."""
    return joblib.load(io.BytesIO(ObjectStore().get(row.artifact_key)))


def load_bundle(session: Session, kind: str) -> dict[str, Any] | None:
    row = latest_model(session, kind)
    return None if row is None else read_bundle(row)


def evaluate_task(task: str, *, session: Session | None = None) -> dict:
    """Read back the metrics of the latest training run."""
    if session is None:
        with unit_of_work(platform=True) as own:
            return evaluate_task(task, session=own)
    row = latest_model(session, task)
    if row is None:
        raise FileNotFoundError(f"No metrics for '{task}'. Run `collection train` first.")
    return row.metrics
