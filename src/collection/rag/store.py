"""Publishing knowledge: the one write path into the versioned tables.

A version's vectors are computed here, once, and never again. Recomputing them later
would let a model upgrade or a library bump change what a past answer's citation points
at, so an edit adds a version and leaves the old ones alone.

The hash is taken over the normalised body (Unicode NFC, `\\n` line endings, no trailing
whitespace, no leading or trailing blank lines) so that an editor that re-saves a file
with different line endings does not mint a version nobody wrote. The normalised text is
what is stored and chunked, so the hash always describes the stored body.

`title` and `audience` are mutable metadata on the document row and sit outside the
version audit trail: changing them does not mint a version, and what applied when an old
answer was generated is not recorded. That is deliberate for `audience`. It is an
access-control decision that has to take effect immediately and for every version at
once; if marking a document internal only bound future versions, the response to a live
disclosure would be to mint a version, which is slower and leaves the old one still
serving. Immediate and retroactive is the safe direction.

Because of that, `audience` is `None` by default and `None` never changes an existing
document: a caller that edits an internal document and says nothing must not make the
discount ceilings retrievable by customers. A new document defaults to CUSTOMER, and
widening INTERNAL to CUSTOMER requires the caller to ask for it by name. An unchanged
body is returned as the existing version, with any explicit title or audience applied.
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


def _apply_metadata(document: KnowledgeDocument, title: str, audience: Audience | None) -> None:
    document.title = title
    if audience is not None:
        document.audience = audience


def publish_document(
    scope: TenantScope,
    *,
    title: str,
    path: str,
    body: str,
    audience: Audience | None = None,
) -> KnowledgeVersion:
    """Version 1, or version N+1 with its own chunks and vectors; the current one if unchanged."""
    text = normalise(body)
    if not text:
        raise ValueError(f"{path} has an empty body; a version with no chunks retrieves nothing")
    digest = content_hash(text)
    session = scope.session

    # Lock the document row for the rest of the transaction. The next number is read as
    # max+1 and the embedding runs before commit, so without it two publishers overlap for
    # as long as model inference takes; the unique constraint would stop the duplicate but
    # fail one of them, where a lock simply makes the second wait and then see version N.
    document = list(
        session.scalars(
            scope.select(KnowledgeDocument).where(KnowledgeDocument.path == path).with_for_update()
        )
    )
    if document:
        current = document[0]
        if current.current_version_id is not None:
            version = scope.get(KnowledgeVersion, current.current_version_id)
            if version is not None and version.content_hash == digest:
                _apply_metadata(current, title, audience)
                session.flush()
                return version
        _apply_metadata(current, title, audience)
    else:
        current = KnowledgeDocument(title=title, path=path, audience=audience or Audience.CUSTOMER)
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
