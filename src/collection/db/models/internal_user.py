"""A person on the tenant's own staff who will sign in to the operator console.

Schema only. It exists now so the tenant-boundary test covers the table from the first
migration that creates it, rather than a later one adding an unguarded table after the
fact. Nothing reads `password_hash` or `role` yet: authentication is a later stage, and
whatever it decides about hashing and roles will land with it, not be guessed at here.

Email is unique per tenant, not globally: two tenants may each employ someone with the
same address, and a global constraint would let one tenant learn that another has them.
"""

import uuid

from sqlalchemy import String, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from collection.db.base import Base, TenantScoped


class InternalUser(TenantScoped, Base):
    __tablename__ = "internal_users"
    __table_args__ = (UniqueConstraint("tenant_id", "email"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(50))
    active: Mapped[bool] = mapped_column(default=True, server_default=true())
