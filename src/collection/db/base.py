"""Declarative base and the tenant mixin.

Every tenant-owned table inherits `TenantScoped`, so the isolation column is declared once
and cannot be forgotten on a new table: a row without a tenant is a schema error, not a
runtime surprise. The `tenants` table itself arrives with the first model.
"""

import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TenantScoped:
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
