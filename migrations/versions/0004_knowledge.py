"""Create versioned knowledge: documents, immutable versions and chunk vectors.

The vector dimension is the platform embedding model's, fixed here as 384; changing the
model is a migration of its own that re-embeds every chunk.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUDIENCES = ("customer", "internal")


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("audience", sa.Enum(*AUDIENCES, name="knowledge_audience"), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.UniqueConstraint("tenant_id", "path"),
        sa.UniqueConstraint("tenant_id", "id"),
    )
    op.create_index("ix_knowledge_documents_tenant_id", "knowledge_documents", ["tenant_id"])

    op.create_table(
        "knowledge_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("document_id", "number"),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["knowledge_documents.tenant_id", "knowledge_documents.id"],
        ),
    )
    op.create_index("ix_knowledge_versions_tenant_id", "knowledge_versions", ["tenant_id"])
    op.create_index("ix_knowledge_versions_document_id", "knowledge_versions", ["document_id"])

    op.create_foreign_key(
        "fk_knowledge_documents_current_version",
        "knowledge_documents",
        "knowledge_versions",
        ["tenant_id", "current_version_id"],
        ["tenant_id", "id"],
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint("version_id", "ordinal"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["knowledge_versions.tenant_id", "knowledge_versions.id"],
        ),
    )
    op.create_index("ix_knowledge_chunks_tenant_id", "knowledge_chunks", ["tenant_id"])
    op.create_index("ix_knowledge_chunks_version_id", "knowledge_chunks", ["version_id"])


def downgrade() -> None:
    op.drop_table("knowledge_chunks")
    op.drop_constraint(
        "fk_knowledge_documents_current_version", "knowledge_documents", type_="foreignkey"
    )
    op.drop_table("knowledge_versions")
    op.drop_table("knowledge_documents")
    op.execute("DROP TYPE knowledge_audience")
