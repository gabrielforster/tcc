"""Create platform_models and customer_scores.

Models belong to the platform, so `platform_models` has no tenant; a score belongs to a
tenant's customer and names the model version that produced it.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_models",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("artifact_key", sa.String(1024), nullable=False),
        sa.UniqueConstraint("kind", "version"),
    )
    op.create_table(
        "customer_scores",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column(
            "platform_model_id", sa.Uuid(), sa.ForeignKey("platform_models.id"), nullable=False
        ),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]
        ),
        sa.UniqueConstraint("tenant_id", "customer_id", "platform_model_id"),
    )
    op.create_index("ix_customer_scores_tenant_id", "customer_scores", ["tenant_id"])
    op.create_index("ix_customer_scores_customer_id", "customer_scores", ["customer_id"])
    op.create_index(
        "ix_customer_scores_platform_model_id", "customer_scores", ["platform_model_id"]
    )


def downgrade() -> None:
    op.drop_table("customer_scores")
    op.drop_table("platform_models")
