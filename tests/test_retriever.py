import hashlib
import re

import numpy as np
import pytest
from sqlalchemy.orm import Session

from collection.config import settings
from collection.db.models import Audience, KnowledgeDocument, Tenant
from collection.db.repository import TenantScope
from collection.rag.embeddings import Embedder
from collection.rag.index import MIN_RELEVANCE, TOP_K
from collection.rag.retriever import PgVectorRetriever
from collection.rag.store import publish_document


class BagOfWords(Embedder):
    """Deterministic and offline: each word hashes to one dimension, so overlap is cosine."""

    name = "bag-of-words"

    def fit(self, texts):
        return self

    def encode(self, texts):
        matrix = np.zeros((len(texts), settings.embedding_dimensions))
        for row, text in enumerate(texts):
            for word in re.findall(r"\w+", text.lower()):
                digest = hashlib.sha256(word.encode()).digest()
                matrix[row, int.from_bytes(digest[:4], "big") % settings.embedding_dimensions] += 1
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.where(norms == 0, 1.0, norms)


@pytest.fixture(autouse=True)
def offline_embedder(monkeypatch):
    monkeypatch.setattr("collection.rag.store.get_embedder", lambda *a: BagOfWords())
    monkeypatch.setattr("collection.rag.retriever.get_embedder", lambda *a: BagOfWords())


def _scope(db: Session, slug: str) -> TenantScope:
    tenant = Tenant(name=slug, slug=slug)
    db.add(tenant)
    db.flush()
    session = Session(bind=db.connection(), join_transaction_mode="create_savepoint")
    return TenantScope(session, tenant.id)


def _publish(scope, path, body, **kwargs):
    return publish_document(scope, title=path, path=path, body=body, **kwargs)


@pytest.mark.db
def test_search_returns_chunks_of_this_tenant_only(db):
    mine, theirs = _scope(db, "mine"), _scope(db, "theirs")
    _publish(mine, "faq/parcelar.md", "parcelamento em ate doze vezes")
    other = _publish(theirs, "faq/parcelar.md", "parcelamento em ate doze vezes")

    hits = PgVectorRetriever(mine).search("parcelamento em ate doze vezes")

    assert len(hits) == 1
    assert hits[0].version_id != other.id
    assert PgVectorRetriever(theirs).search("parcelamento")[0].version_id == other.id


@pytest.mark.db
def test_search_scores_are_cosine_similarity_in_zero_to_one_descending(db):
    scope = _scope(db, "t")
    _publish(scope, "a.md", "parcelamento em ate doze vezes")
    _publish(scope, "b.md", "parcelamento com entrada minima obrigatoria")
    _publish(scope, "c.md", "boleto segunda via vencimento")

    hits = PgVectorRetriever(scope).search("parcelamento em ate doze vezes", top_k=3)

    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s <= 1.0 + 1e-9 for s in scores)
    assert hits[0].document_id == "a.md"
    assert hits[0].score == pytest.approx(1.0)
    # One shared word between two five-word bags: 1 / (sqrt(5) * sqrt(5)).
    assert hits[1].score == pytest.approx(0.2)
    assert hits[1].document_id == "b.md"
    assert hits[0].chunk_id is not None and hits[0].version_id is not None


@pytest.mark.db
def test_search_only_reads_the_current_version_of_each_document(db):
    scope = _scope(db, "t")
    old = _publish(scope, "a.md", "juros de um por cento ao mes")
    new = _publish(scope, "a.md", "juros de dois por cento ao mes")

    hits = PgVectorRetriever(scope).search("juros de um por cento ao mes", top_k=10)

    assert {hit.version_id for hit in hits} == {new.id}
    assert old.id not in {hit.version_id for hit in hits}
    assert all("um por cento" not in hit.text for hit in hits)


@pytest.mark.db
def test_search_excludes_internal_audience_documents(db):
    scope = _scope(db, "t")
    _publish(scope, "alcada.md", "desconto maximo de trinta por cento", audience=Audience.INTERNAL)
    _publish(scope, "faq.md", "desconto para pagamento a vista")

    hits = PgVectorRetriever(scope).search("desconto maximo de trinta por cento", top_k=10)
    assert [hit.document_id for hit in hits] == ["faq.md"]
    assert all("trinta" not in hit.text for hit in hits)

    # Editing the internal document without naming an audience keeps it internal.
    _publish(scope, "alcada.md", "desconto maximo de quarenta por cento")
    assert all(
        "quarenta" not in hit.text
        for hit in PgVectorRetriever(scope).search("desconto maximo de quarenta", top_k=10)
    )

    # Flipping it to customer is the only way back in, and then it is served.
    document = scope.all(KnowledgeDocument, KnowledgeDocument.path == "alcada.md")[0]
    document.audience = Audience.CUSTOMER
    scope.session.flush()
    served = PgVectorRetriever(scope).search("desconto maximo de quarenta", top_k=10)
    assert "alcada.md" in {hit.document_id for hit in served}


@pytest.mark.db
def test_a_query_matching_nothing_returns_an_empty_list(db):
    scope = _scope(db, "t")
    _publish(scope, "a.md", "parcelamento em ate doze vezes")

    assert PgVectorRetriever(scope).search("zzz qqq xxx") == []
    assert PgVectorRetriever(_scope(db, "empty")).search("parcelamento") == []


@pytest.mark.db
def test_the_floor_and_the_limit_are_the_responsive_agents(db):
    scope = _scope(db, "t")
    for n in range(TOP_K + 2):
        _publish(scope, f"{n}.md", f"parcelamento documento{n}")

    assert len(PgVectorRetriever(scope).search("parcelamento")) == TOP_K
    assert all(
        hit.score >= MIN_RELEVANCE for hit in PgVectorRetriever(scope).search("parcelamento")
    )
