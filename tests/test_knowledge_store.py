import hashlib
import re

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.orm import Session

from collection.config import settings
from collection.db.base import Base
from collection.db.models import (
    Audience,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeVersion,
    Tenant,
)
from collection.db.repository import TenantScope
from collection.rag.chunking import chunk_text
from collection.rag.embeddings import SentenceTransformerEmbedder, _load_model, get_embedder
from collection.rag.store import content_hash, publish_document

BODY = "# Parcelamento\n\nO cliente pode parcelar a divida em ate 12 vezes.\n\nJuros de 1% ao mes."
EDITED = BODY + "\n\nEntrada minima de 10% do valor."


def _scope(db: Session, slug: str = "one") -> TenantScope:
    tenant = Tenant(name=slug, slug=slug)
    db.add(tenant)
    db.flush()
    session = Session(bind=db.connection(), join_transaction_mode="create_savepoint")
    return TenantScope(session, tenant.id)


def _publish(scope: TenantScope, body: str = BODY, **kwargs) -> KnowledgeVersion:
    return publish_document(
        scope, title="Parcelamento", path="faq/parcelamento.md", body=body, **kwargs
    )


def test_the_content_hash_is_sha256_of_the_normalised_body():
    assert content_hash("a\r\nb  \n\n") == hashlib.sha256(b"a\nb").hexdigest()
    assert content_hash("a\nb") == content_hash("a\r\nb\n")
    assert content_hash("a\nb") != content_hash("a\nc")


def test_the_embedding_model_is_loaded_once_and_shared(monkeypatch):
    import sentence_transformers

    built: list[str] = []

    class Counting:
        def __init__(self, name):
            built.append(name)

    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", Counting)
    _load_model.cache_clear()
    try:
        first, second = get_embedder(), get_embedder()
        assert isinstance(first, SentenceTransformerEmbedder)
        assert first.model_name == settings.embedding_model
        assert first._load() is second._load()
        assert built == [settings.embedding_model]
    finally:
        _load_model.cache_clear()


@pytest.mark.db
def test_publishing_twice_with_the_same_body_does_not_create_a_second_version(db):
    scope = _scope(db)
    first = _publish(scope)
    again = _publish(scope)

    assert again.id == first.id
    assert first.number == 1
    assert len(scope.all(KnowledgeVersion)) == 1
    assert len(scope.all(KnowledgeDocument)) == 1
    chunks_after_first = len(scope.all(KnowledgeChunk))
    assert chunks_after_first >= 1
    assert len(scope.all(KnowledgeChunk)) == chunks_after_first


@pytest.mark.db
def test_editing_creates_version_two_and_leaves_version_one_addressable(db):
    scope = _scope(db)
    one = _publish(scope)
    two = _publish(scope, EDITED)

    assert (one.number, two.number) == (1, 2)
    assert one.id != two.id
    document = scope.all(KnowledgeDocument)[0]
    assert document.current_version_id == two.id
    # Version one is untouched: same body, same hash, still reachable by its id.
    kept = scope.get(KnowledgeVersion, one.id)
    assert kept is not None
    assert kept.body == BODY
    assert kept.content_hash == content_hash(BODY)
    assert scope.get(KnowledgeVersion, two.id).body == EDITED


@pytest.mark.db
def test_each_chunk_of_a_version_carries_a_384_dimension_vector(db):
    scope = _scope(db)
    version = _publish(scope)
    chunks = scope.all(KnowledgeChunk, KnowledgeChunk.version_id == version.id)

    assert len(chunks) == len(chunk_text(BODY, "x"))
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert len(chunk.embedding) == settings.embedding_dimensions == 384
        assert any(value != 0 for value in chunk.embedding)
        assert chunk.word_count == len(chunk.text.split())


@pytest.mark.db
def test_a_versions_chunks_belong_to_that_version_only(db):
    scope = _scope(db)
    one = _publish(scope)
    two = _publish(scope, EDITED)
    first_texts = [c.text for c in scope.all(KnowledgeChunk, KnowledgeChunk.version_id == one.id)]
    second = scope.all(KnowledgeChunk, KnowledgeChunk.version_id == two.id)

    assert second and all(c.version_id == two.id for c in second)
    assert "Entrada minima" not in " ".join(first_texts)
    assert any("Entrada minima" in c.text for c in second)
    total = len(scope.all(KnowledgeChunk))
    assert total == len(first_texts) + len(second)


@pytest.mark.db
def test_a_versions_vectors_are_not_recomputed_by_a_later_version(db):
    scope = _scope(db)
    one = _publish(scope)
    before = [
        list(c.embedding) for c in scope.all(KnowledgeChunk, KnowledgeChunk.version_id == one.id)
    ]
    _publish(scope, EDITED)
    scope.session.expire_all()
    after = [
        list(c.embedding) for c in scope.all(KnowledgeChunk, KnowledgeChunk.version_id == one.id)
    ]
    assert before == after


@pytest.mark.db
def test_the_audience_defaults_to_customer_and_internal_is_kept(db):
    scope = _scope(db)
    publish_document(scope, title="FAQ", path="faq.md", body=BODY)
    publish_document(
        scope, title="Limites", path="alcada.md", body=BODY, audience=Audience.INTERNAL
    )
    audiences = {d.path: d.audience for d in scope.all(KnowledgeDocument)}
    assert audiences == {"faq.md": Audience.CUSTOMER, "alcada.md": Audience.INTERNAL}


@pytest.mark.db
def test_republishing_an_unchanged_body_still_applies_a_new_audience(db):
    """Marking a document internal must not be swallowed because its text did not change."""
    scope = _scope(db)
    first = _publish(scope)
    again = _publish(scope, audience=Audience.INTERNAL)

    assert again.id == first.id
    assert scope.all(KnowledgeDocument)[0].audience is Audience.INTERNAL


@pytest.mark.db
def test_two_tenants_may_publish_the_same_path_and_see_only_their_own(db):
    one, two = _scope(db, "one"), _scope(db, "two")
    _publish(one)
    _publish(two, EDITED)

    assert [v.body for v in one.all(KnowledgeVersion)] == [BODY]
    assert [v.body for v in two.all(KnowledgeVersion)] == [EDITED]
    assert {c.version_id for c in one.all(KnowledgeChunk)} == {
        v.id for v in one.all(KnowledgeVersion)
    }


@pytest.mark.db
def test_migration_0004_matches_the_models(db_engine):
    knowledge = {"knowledge_documents", "knowledge_versions", "knowledge_chunks"}

    def include(obj, name, type_, reflected, compare_to):
        # Other tests register throwaway models on the same metadata; only ours are checked.
        return name in knowledge if type_ == "table" else True

    with db_engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"include_object": include})
        diff = compare_metadata(context, Base.metadata)
    # Tables of other revisions that this process did not register are out of scope too, so
    # keep only the differences that touch ours.
    ours = [d for d in diff if knowledge & set(re.findall(r"knowledge_\w+", repr(d)))]
    assert ours == []


@pytest.mark.db
def test_editing_an_internal_document_without_naming_an_audience_keeps_it_internal(db):
    scope = _scope(db)
    _publish(scope, audience=Audience.INTERNAL)
    _publish(scope, EDITED)
    _publish(scope, EDITED)

    assert scope.all(KnowledgeDocument)[0].audience is Audience.INTERNAL


@pytest.mark.db
def test_widening_internal_to_customer_requires_saying_so(db):
    scope = _scope(db)
    _publish(scope, audience=Audience.INTERNAL)
    _publish(scope, audience=Audience.CUSTOMER)

    assert scope.all(KnowledgeDocument)[0].audience is Audience.CUSTOMER


@pytest.mark.db
@pytest.mark.parametrize("body", ["", "   \n\r\n\t "])
def test_an_empty_body_is_refused_and_creates_nothing(db, body):
    scope = _scope(db)
    with pytest.raises(ValueError, match="empty body"):
        _publish(scope, body)
    assert scope.all(KnowledgeDocument) == []
    assert scope.all(KnowledgeVersion) == []
