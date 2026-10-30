"""Retrieval for the serving path: one SQL statement, scoped, current and customer-safe.

The in-memory `VectorIndex` is fitted to one corpus in one process, so it cannot answer
for many tenants or survive an edit. This reads the versioned rows instead, and three
filters carry the weight, each in the statement rather than in Python so that no spelling
of a call can skip them:

* **Tenant.** The query starts from `scope.select(...)`, and the session guard injects the
  predicate as well. Every join also repeats `tenant_id`, so a row can only match a row of
  the same tenant even if a foreign key were one day dropped.
* **Current version only.** The document names its current version; a chunk is a
  candidate only if its version is that one. Older versions stay addressable for the
  audit trail, but a live answer must never quote text that has since been replaced.
* **Customer audience.** `Audience.INTERNAL` documents hold the authority limits --
  discount ceilings, maximum instalments, what always needs a person. An earlier version
  of this system quoted that file to a debtor. The filter is on the document, so every
  version of an internal document is excluded, and it is an allow-list (`== CUSTOMER`):
  an audience added later is withheld until someone decides it may be served.

Similarity is `1 - cosine distance`, ordered by distance and limited in SQL so the
database never ships the corpus. The relevance floor is applied afterwards in Python,
using the constant the responsive agent already owns, so it keeps one definition rather
than a second copy inside a query. The limit therefore applies before the floor: asking
for `top_k` returns at most `top_k`, possibly fewer.

There is deliberately no ANN index. The scan is exact, which is what lets recall be
stated without caveat; HNSW would also post-filter, so the tenant and audience predicates
would shrink an already-truncated candidate list and quietly cut recall per tenant.
"""

from collection.db.models import Audience, KnowledgeChunk, KnowledgeDocument, KnowledgeVersion
from collection.db.repository import TenantScope
from collection.rag.chunking import Chunk
from collection.rag.embeddings import get_embedder
from collection.rag.index import MIN_RELEVANCE, TOP_K, Retrieved


class PgVectorRetriever:
    def __init__(self, scope: TenantScope) -> None:
        self.scope = scope

    def search(self, query: str, top_k: int = TOP_K) -> list[Retrieved]:
        vector = [float(x) for x in get_embedder().encode([query])[0]]
        distance = KnowledgeChunk.embedding.cosine_distance(vector).label("distance")
        statement = (
            self.scope.select(KnowledgeChunk)
            .add_columns(KnowledgeDocument.path, distance)
            .join(
                KnowledgeVersion,
                (KnowledgeVersion.id == KnowledgeChunk.version_id)
                & (KnowledgeVersion.tenant_id == KnowledgeChunk.tenant_id),
            )
            .join(
                KnowledgeDocument,
                (KnowledgeDocument.current_version_id == KnowledgeVersion.id)
                & (KnowledgeDocument.tenant_id == KnowledgeVersion.tenant_id),
            )
            .where(KnowledgeDocument.audience == Audience.CUSTOMER)
            .order_by(distance, KnowledgeChunk.id)
            .limit(top_k)
        )
        hits = []
        for chunk, path, dist in self.scope.session.execute(statement):
            score = 1.0 - float(dist)
            if score < MIN_RELEVANCE:
                continue
            hits.append(
                Retrieved(
                    chunk=Chunk(
                        text=chunk.text,
                        document_id=path,
                        ordinal=chunk.ordinal,
                        metadata={"parent_id": path},
                    ),
                    score=score,
                    chunk_id=chunk.id,
                    version_id=chunk.version_id,
                )
            )
        return hits

    def search_documents(self, query: str, top_k: int = TOP_K) -> list[str]:
        """Distinct document paths behind the top chunks, best first.

        The evaluation compares retrievers at the document level, so this mirrors the
        in-memory index exactly -- including asking for three times as many chunks as
        documents wanted, because several chunks of one document would otherwise crowd the
        list and make the ranking look shorter than it is.
        """
        seen: list[str] = []
        for hit in self.search(query, top_k=top_k * 3):
            if hit.document_id not in seen:
                seen.append(hit.document_id)
            if len(seen) == top_k:
                break
        return seen
