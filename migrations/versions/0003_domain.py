"""Create customers, receivables and internal users.

The first tenant-scoped tables. Each carries `tenant_id` itself, receivables included,
and the receivable's composite foreign key makes its tenant and its customer's agree.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("open", "paid", "paid_late", "defaulted", "agreement")


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("pseudonym", sa.String(64), nullable=False),
        sa.Column("preferred_channel", sa.String(20), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "external_id"),
        sa.UniqueConstraint("tenant_id", "id"),
    )
    op.create_index("ix_customers_tenant_id", "customers", ["tenant_id"])

    op.create_table(
        "receivables",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("days_overdue", sa.Integer(), nullable=False),
        sa.Column("status", sa.Enum(*STATUSES, name="receivable_status"), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]
        ),
    )
    op.create_index("ix_receivables_tenant_id", "receivables", ["tenant_id"])
    op.create_index("ix_receivables_customer_id", "receivables", ["customer_id"])

    op.create_table(
        "internal_users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.UniqueConstraint("tenant_id", "email"),
    )
    op.create_index("ix_internal_users_tenant_id", "internal_users", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("internal_users")
    op.drop_table("receivables")
    op.drop_table("customers")
    op.execute("DROP TYPE receivable_status")
