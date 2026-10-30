"""Create stored_objects: one row per file kept in object storage.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stored_objects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        # Nullable: platform artifacts belong to no tenant.
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("key", sa.String(1024), nullable=False, unique=True),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_stored_objects_tenant_id", "stored_objects", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("stored_objects")
