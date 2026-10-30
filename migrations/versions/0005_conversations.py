"""Create conversations, turns and the per-turn retrieval record.

Adds a unique constraint on knowledge_chunks so a retrieval row can cite a chunk together
with its version and have the database check they belong together.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATES = ("open", "escalated", "closed")
# Frozen here: a migration must not change when the enum later gains a member.
INTENTS = (
    "debt_inquiry",
    "second_copy",
    "negotiation",
    "dispute",
    "payment_claim",
    "opt_out",
    "small_talk",
    "unknown",
)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_knowledge_chunks_tenant_id_version_id_id",
        "knowledge_chunks",
        ["tenant_id", "version_id", "id"],
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("state", sa.Enum(*STATES, name="conversation_state"), nullable=False),
        sa.Column("holder", sa.String(100), nullable=False),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalation_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]
        ),
    )
    op.create_index("ix_conversations_tenant_id", "conversations", ["tenant_id"])
    op.create_index("ix_conversations_customer_id", "conversations", ["customer_id"])

    op.create_table(
        "turns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("intent", sa.Enum(*INTENTS, name="turn_intent"), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("responder", sa.String(50), nullable=True),
        sa.Column("provider", sa.String(50), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.clock_timestamp(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"], ["conversations.tenant_id", "conversations.id"]
        ),
    )
    op.create_index("ix_turns_tenant_id", "turns", ["tenant_id"])
    op.create_index("ix_turns_conversation_id", "turns", ["conversation_id"])

    op.create_table(
        "turn_retrievals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.UniqueConstraint("turn_id", "rank"),
        sa.ForeignKeyConstraint(["tenant_id", "turn_id"], ["turns.tenant_id", "turns.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id", "chunk_id"],
            ["knowledge_chunks.tenant_id", "knowledge_chunks.version_id", "knowledge_chunks.id"],
        ),
    )
    op.create_index("ix_turn_retrievals_tenant_id", "turn_retrievals", ["tenant_id"])
    op.create_index("ix_turn_retrievals_turn_id", "turn_retrievals", ["turn_id"])
    op.create_index("ix_turn_retrievals_version_id", "turn_retrievals", ["version_id"])


def downgrade() -> None:
    op.drop_table("turn_retrievals")
    op.drop_table("turns")
    op.drop_table("conversations")
    op.execute("DROP TYPE turn_intent")
    op.execute("DROP TYPE conversation_state")
    op.drop_constraint(
        "uq_knowledge_chunks_tenant_id_version_id_id", "knowledge_chunks", type_="unique"
    )
