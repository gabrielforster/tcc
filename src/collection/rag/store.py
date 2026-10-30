"""Publishing knowledge: the one write path into the versioned tables.

A version's vectors are computed here, once, and never again. Recomputing them later
would let a model upgrade or a library bump change what a past answer's citation points
at, so an edit adds a version and leaves the old ones alone.

The hash is taken over the normalised body (Unicode NFC, `\\n` line endings, no trailing
whitespace, no leading or trailing blank lines) so that an editor that re-saves a file
with different line endings does not mint a version nobody wrote. The normalised text is
what is stored and chunked, so the hash always describes the stored body.

Republishing an unchanged body returns the existing version, but a changed `title` or
`audience` is still applied to the document. They are not part of a version's text, and
swallowing an audience change would leave a document marked internal still being served
to customers.
"""

import hashlib
import unicodedata

from sqlalchemy import func

from collection.config import settings
from collection.db.models import Audience, KnowledgeChunk, KnowledgeDocument, KnowledgeVersion
from collection.db.repository import TenantScope
from collection.rag.chunking import chunk_text
from collection.rag.embeddings import get_embedder


def normalise(body: str) -> str:
    text = unicodedata.normalize("NFC", body).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip("\n")


def content_hash(body: str) -> str:
    return hashlib.sha256(normalise(body).encode("utf-8")).hexdigest()


def publish_document(
    scope: TenantScope,
    *,
    title: str,
    path: str,
    body: str,
    audience: Audience = Audience.CUSTOMER,
) -> KnowledgeVersion:
    """Version 1, or version N+1 with its own chunks and vectors; the current one if unchanged."""
    text = normalise(body)
    digest = content_hash(text)
    session = scope.session

    document = scope.all(KnowledgeDocument, KnowledgeDocument.path == path)
    if document:
        current = document[0]
        if current.current_version_id is not None:
            version = scope.get(KnowledgeVersion, current.current_version_id)
            if version is not None and version.content_hash == digest:
                current.title, current.audience = title, audience
                session.flush()
                return version
        current.title, current.audience = title, audience
    else:
        current = KnowledgeDocument(title=title, path=path, audience=audience)
        scope.add(current)
        session.flush()

    latest = session.scalar(
        scope.select(KnowledgeVersion)
        .with_only_columns(func.max(KnowledgeVersion.number))
        .where(KnowledgeVersion.document_id == current.id)
    )
    version = KnowledgeVersion(
        document_id=current.id, number=(latest or 0) + 1, content_hash=digest, body=text
    )
    scope.add(version)
    session.flush()

    chunks = chunk_text(text, str(version.id))
    vectors = get_embedder().encode([chunk.text for chunk in chunks]) if chunks else []
    for chunk, vector in zip(chunks, vectors, strict=True):
        if len(vector) != settings.embedding_dimensions:
            raise ValueError(
                f"the embedder produced {len(vector)} dimensions; the platform stores "
                f"{settings.embedding_dimensions}"
            )
        scope.add(
            KnowledgeChunk(
                version_id=version.id,
                ordinal=chunk.ordinal,
                text=chunk.text,
                embedding=[float(x) for x in vector],
                word_count=chunk.word_count,
            )
        )
    current.current_version_id = version.id
    session.flush()
    return version
