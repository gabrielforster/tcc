"""The models the platform trains, and what they say about a tenant's customers.

Models are app-level: there is one propensity model and one default-risk model, versioned
as platform artifacts. They are meant to be trained on pooled data; today a run fits one
tenant's frames (recorded in `metrics["trained_on"]`). Pooling is no longer blocked on the
ids -- pseudonyms are keyed per tenant, so two tenants' frames stack without their keys
colliding -- and what remains is the question of whose data may be fitted together, which
is a contractual answer rather than a technical one. A tenant's pipeline run imports and
indexes its own data and scores its receivables against the latest version; it never
trains one. That is why `PlatformModel` has no `tenant_id` -- and, because it has none,
no `TenantScoped` mixin and no guard: any session may read which model is current, and
only a platform session writes one, by the same convention that keeps `tenants` out of a
tenant's reach. The weights themselves are an object under the `platform/` prefix; the
row is what the application can query (which version, how it scored, where the bytes
are).

`CustomerScore` is the opposite case. It is a fact about one tenant's customer, so it
carries its own `tenant_id` and the composite foreign key that makes it agree with the
customer's, as `Receivable` does. It names the `PlatformModel` row that produced it, so
a score is never a bare number: it is a number from a version, and rescoring against a
newer version adds rows instead of overwriting what an earlier decision was based on.
`(tenant_id, customer_id, platform_model_id)` is unique so that rescoring against the
same version replaces, never doubles.

`version` counts per `kind` and is unique with it: two publishers racing for the same
number cannot both win, the loser fails on the constraint instead of silently shadowing.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from collection.db.base import Base, TenantScoped


def _now() -> datetime:
    return datetime.now(UTC)


class PlatformModel(Base):
    __tablename__ = "platform_models"
    __table_args__ = (UniqueConstraint("kind", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(64))
    version: Mapped[int]
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    artifact_key: Mapped[str] = mapped_column(String(1024))


class CustomerScore(TenantScoped, Base):
    __tablename__ = "customer_scores"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]),
        UniqueConstraint("tenant_id", "customer_id", "platform_model_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(index=True)
    platform_model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform_models.id"), index=True
    )
    score: Mapped[float] = mapped_column(Float)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
