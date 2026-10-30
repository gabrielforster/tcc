"""The row that says a file exists in object storage, and whose it is.

The bytes live in the bucket; this row is what the application can query -- which tenant
owns the object, what kind of thing it is, how big -- without listing a bucket. Object
storage answers "give me this key", never "which objects belong to this tenant".

`tenant_id` is nullable because a shared model and its metrics belong to no tenant. The
`TenantScoped` mixin declares the column non-null, so it is overridden here rather than
dropped: keeping the mixin is what makes the guard's injected predicate
(`with_loader_criteria(TenantScoped, ...)`) apply to this table, so a tenant's session
sees only its own rows and never the platform's. The same nullable column means the guard
refuses a session with no tenant bound, so platform objects are read and written through
`platform=True` and nowhere else.

`key` is unique on its own: it embeds the tenant id (or `platform/`), so two owners can
never collide, and a nullable tenant would make a composite unique constraint useless
(NULLs are distinct).
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from collection.db.base import Base, TenantScoped


class StoredObject(TenantScoped, Base):
    __tablename__ = "stored_objects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(  # type: ignore[assignment]
        ForeignKey("tenants.id"), index=True, nullable=True
    )
    kind: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(1024), unique=True)
    content_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
