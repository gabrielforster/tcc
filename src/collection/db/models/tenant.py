"""A tenant, and the lifecycle that decides whether it may be served at all.

A tenant with no ingested data has nothing to answer from, and serving a degraded system
is worse than refusing: the customer gets a confident-sounding reply with no grounding.
So a tenant is servable only in `ACTIVE`, and `ACTIVE` is reachable only by climbing the
ladder one rung at a time (`PROVISIONED -> INGESTING -> READY -> ACTIVE`). Skipping a rung
raises instead of being tolerated, because a skipped rung means a step that was assumed
rather than done.

`SUSPENDED` is the exit from any state. It returns to `READY`, never straight to
`ACTIVE`: whoever suspended the tenant must decide again that it is fit to serve.

`tenant_key` is the public key a widget embed carries, so an anonymous customer request
can be resolved to a tenant. It is an identifier, not a secret: it grants nothing beyond
reaching that tenant's own public channel, and it is random so it cannot be guessed from
the slug.
"""

import enum
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from collection.db.base import Base


class TenantState(enum.StrEnum):
    PROVISIONED = "provisioned"
    INGESTING = "ingesting"
    READY = "ready"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class InvalidTransition(ValueError):
    """A move that is not on the ladder; carries both ends so the message can say which."""

    def __init__(self, current: TenantState, attempted: TenantState) -> None:
        super().__init__(f"a {current.value} tenant cannot become {attempted.value}")
        self.current = current
        self.attempted = attempted


_ALLOWED: dict[TenantState, frozenset[TenantState]] = {
    TenantState.PROVISIONED: frozenset({TenantState.INGESTING, TenantState.SUSPENDED}),
    TenantState.INGESTING: frozenset({TenantState.READY, TenantState.SUSPENDED}),
    TenantState.READY: frozenset({TenantState.ACTIVE, TenantState.SUSPENDED}),
    TenantState.ACTIVE: frozenset({TenantState.SUSPENDED}),
    TenantState.SUSPENDED: frozenset({TenantState.READY}),
}


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    # No column default: `__init__` below mints the key, because an unsaved tenant has to
    # carry it already. A second default here would only be a second thing to keep in step.
    tenant_key: Mapped[str] = mapped_column(String(64), unique=True)
    state: Mapped[TenantState] = mapped_column(
        SAEnum(TenantState, name="tenant_state", values_callable=lambda e: [m.value for m in e]),
        default=TenantState.PROVISIONED,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __init__(self, **kwargs: object) -> None:
        # Column defaults only fire on flush; a tenant that has not been saved yet must
        # already answer `servable` and carry its key.
        kwargs.setdefault("id", uuid.uuid4())
        kwargs.setdefault("state", TenantState.PROVISIONED)
        kwargs.setdefault("tenant_key", secrets.token_urlsafe(16))
        super().__init__(**kwargs)

    @property
    def servable(self) -> bool:
        return self.state is TenantState.ACTIVE


def advance(tenant: Tenant, to: TenantState) -> None:
    if to not in _ALLOWED[tenant.state]:
        raise InvalidTransition(tenant.state, to)
    tenant.state = to
    # `activated_at` answers "when did this tenant finish onboarding and become servable",
    # a fact about onboarding, not about the latest resume. A suspension is an
    # interruption, not a new onboarding, so only the first activation is recorded.
    if to is TenantState.ACTIVE and tenant.activated_at is None:
        tenant.activated_at = datetime.now(UTC)
