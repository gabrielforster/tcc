"""A tenant's debtors and what they owe.

Names follow `collection.domain.schema`: `external_id` is that vocabulary's `customer_id`
(the identifier in the tenant's ERP), renamed because `customer_id` here would collide
with the foreign key to this table's own primary key. `Receivable` is that vocabulary's
`invoice`, and its statuses are the ones listed there. The overdue counter is called
`days_overdue` rather than the schema's `days_late`: the schema's counts "to payment or
today", this one is a fact about the day of ingestion and says nothing of payment.

`Receivable` carries its own `tenant_id` although its customer already has one. A join is
not a boundary: the guard filters the table a statement names, so a receivable reachable
only through its customer would be filtered by nothing the moment someone queried it
directly. The composite foreign key then makes the two columns agree, so a receivable
cannot be filed under one tenant while pointing at another tenant's customer -- the
database refuses what the guard, which reads one row at a time, would not notice.

`pseudonym` is the stable token that stands in for the customer wherever the real
identity must not travel (prompts, logs, analytics), so the mapping back lives in one
table and nowhere else.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKeyConstraint,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from collection.db.base import Base, TenantScoped


class ReceivableStatus(enum.StrEnum):
    OPEN = "open"
    PAID = "paid"
    PAID_LATE = "paid_late"
    DEFAULTED = "defaulted"
    AGREEMENT = "agreement"


def days_overdue(due_date: date, as_of: date) -> int:
    """Whole days past due on `as_of`; a receivable not yet due is zero, never negative."""
    return max(0, (as_of - due_date).days)


class Customer(TenantScoped, Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_id"),
        # The target of `Receivable`'s composite foreign key.
        UniqueConstraint("tenant_id", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str] = mapped_column(String(100))
    pseudonym: Mapped[str] = mapped_column(String(64))
    preferred_channel: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Receivable(TenantScoped, Base):
    __tablename__ = "receivables"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    due_date: Mapped[date] = mapped_column(Date)
    days_overdue: Mapped[int]
    status: Mapped[ReceivableStatus] = mapped_column(
        SAEnum(
            ReceivableStatus,
            name="receivable_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=ReceivableStatus.OPEN,
    )
