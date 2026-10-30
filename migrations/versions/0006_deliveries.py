"""Create deliveries: one row per outbound message, stamped at each rung.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen here: a migration must not change when the enum later gains a member.
STATUSES = ("enviada", "entregue", "lida", "respondida")


def upgrade() -> None:
    op.create_table(
        "deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.Enum(*STATUSES, name="delivery_status"), nullable=False),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"], ["conversations.tenant_id", "conversations.id"]
        ),
        sa.ForeignKeyConstraint(["tenant_id", "turn_id"], ["turns.tenant_id", "turns.id"]),
    )
    op.create_index("ix_deliveries_tenant_id", "deliveries", ["tenant_id"])
    op.create_index("ix_deliveries_conversation_id", "deliveries", ["conversation_id"])
    op.create_index("ix_deliveries_turn_id", "deliveries", ["turn_id"])


def downgrade() -> None:
    op.drop_table("deliveries")
    op.execute("DROP TYPE delivery_status")
