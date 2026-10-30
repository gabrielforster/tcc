"""Knowledge as immutable versions, so "what did the agent see?" stays answerable.

A document that is edited in place rewrites the history of every answer that cited it:
months later the passage a customer was quoted no longer exists, and nobody can say what
the agent was shown. So the document row holds only identity (title, path, audience) and a
pointer to its current version; the text lives in `KnowledgeVersion`, which is never
updated, and an edit is a new version with its own chunks. A past answer cites a chunk
and the chunk still says what it said.

Vectors are stored per chunk and computed once, at publish time, with the platform's one
embedding model. That is why they can live in a shared table at all: a TF-IDF vocabulary
is fitted per corpus, so the same sentence meant different vectors in different tenants
and any edit invalidated all of them. A fixed model gives every tenant the same space.
Changing the model re-embeds every chunk, which makes it a migration and not a setting.

`Audience` is a column here because internal-only knowledge -- discount ceilings,
escalation triggers -- must never reach the context a customer-facing answer is generated
from. The retriever filters on it; it lives on the document because a version of an
internal document is just as internal.

Every table carries its own `tenant_id` and the composite foreign keys make the tenants
agree, for the reason given in `customer.py`: a chunk reachable only through its version
would be filtered by nothing when queried directly. There are deliberately no
relationships: the document and its current version point at each other, and a lazy load
across that cycle would hide queries the guard should see written out.
"""

import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from collection.config import settings
from collection.db.base import Base, TenantScoped


class Audience(enum.StrEnum):
    CUSTOMER = "customer"
    INTERNAL = "internal"


class KnowledgeDocument(TenantScoped, Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "path"),
        # The target of `KnowledgeVersion`'s composite foreign key.
        UniqueConstraint("tenant_id", "id"),
        # `use_alter`: the two tables point at each other, so this one is added after both
        # exist. It is NULL until the first version is written, and a NULL column is not
        # checked, which is what lets the document be inserted first.
        ForeignKeyConstraint(
            ["tenant_id", "current_version_id"],
            ["knowledge_versions.tenant_id", "knowledge_versions.id"],
            name="fk_knowledge_documents_current_version",
            use_alter=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(300))
    path: Mapped[str] = mapped_column(String(500))
    audience: Mapped[Audience] = mapped_column(
        SAEnum(Audience, name="knowledge_audience", values_callable=lambda e: [m.value for m in e]),
        default=Audience.CUSTOMER,
    )
    current_version_id: Mapped[uuid.UUID | None]


class KnowledgeVersion(TenantScoped, Base):
    __tablename__ = "knowledge_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "number"),
        # The target of `KnowledgeChunk`'s composite foreign key, and of the document's
        # pointer to its current version.
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["knowledge_documents.tenant_id", "knowledge_documents.id"],
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(index=True)
    number: Mapped[int]
    content_hash: Mapped[str] = mapped_column(String(64))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeChunk(TenantScoped, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("version_id", "ordinal"),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["knowledge_versions.tenant_id", "knowledge_versions.id"],
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(index=True)
    ordinal: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dimensions))
    word_count: Mapped[int]
