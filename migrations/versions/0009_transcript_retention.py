"""Record when a conversation's transcript was erased.

A purged conversation has no turns. So does one that was opened and never spoken in, and
the two are not the same fact: the first is a thread whose contents the controller
deleted, the second a thread that never had any. `purged_at` is what keeps them apart --
without it, erasure would be indistinguishable from absence and an audit could not show
that anything was ever erased.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The purge reads closed threads by age; without this it is a sequential scan of
    # every conversation a tenant has ever had, which is the table that grows fastest.
    op.create_index(
        "ix_conversations_tenant_closed_at", "conversations", ["tenant_id", "closed_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_tenant_closed_at", table_name="conversations")
    op.drop_column("conversations", "purged_at")
