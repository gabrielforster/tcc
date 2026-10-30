"""Seeding: what a fresh clone gets from one command, and what running it twice costs.

`seed_development_tenant` writes through `unit_of_work`, so the session factory is pointed
at the test transaction the way `test_tenant_scope` does it -- the seeding commits, and the
whole thing is still rolled back afterwards. The synthetic source is shrunk through the
settings it reads, because what is being tested is that the tenant ends up servable, not
how long twelve thousand invoices take.
"""

import pytest
from sqlalchemy.orm import Session, sessionmaker

from collection.config import settings
from collection.db import session as session_module
from collection.db.models import (
    Audience,
    Customer,
    KnowledgeDocument,
    KnowledgeVersion,
    Receivable,
    TenantState,
)
from collection.db.repository import TenantScope
from collection.db.seed import seed_development_tenant
from collection.rag.retriever import PgVectorRetriever

CUSTOMER_FACING = {
    "faq.md",
    "politica-de-cobranca.md",
    "script-de-negociacao.md",
    "tabela-de-parcelamento.csv",
}
INTERNAL = "limites-de-alcada.md"


@pytest.fixture
def seeding(db, bucket, monkeypatch):
    """Seeding bound to the test transaction, over a dataset small enough to be fast."""
    factory = sessionmaker(
        bind=db.get_bind(),
        class_=Session,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(session_module, "session_factory", lambda: factory)
    monkeypatch.setattr(settings, "synthetic_n_customers", 20)
    monkeypatch.setattr(settings, "synthetic_n_invoices", 60)


def _scope(db, tenant_id) -> TenantScope:
    """A reader on its own session, so nothing the seeding left in an identity map is reused."""
    return TenantScope(
        Session(bind=db.connection(), join_transaction_mode="create_savepoint"), tenant_id
    )


@pytest.mark.db
def test_seeding_produces_an_active_tenant_that_can_answer(seeding, db):
    tenant = seed_development_tenant()

    assert tenant.state is TenantState.ACTIVE
    assert tenant.servable is True
    scope = _scope(db, tenant.id)
    assert len(scope.all(Customer)) == 20
    assert scope.all(Receivable)
    hits = PgVectorRetriever(scope).search("em quantas vezes posso parcelar a minha divida?")
    assert hits, "a seeded tenant retrieves nothing, so it has nothing to answer from"
    assert {hit.chunk.document_id for hit in hits} <= CUSTOMER_FACING


@pytest.mark.db
def test_the_authority_limits_document_is_seeded_as_internal(seeding, db):
    tenant = seed_development_tenant()

    scope = _scope(db, tenant.id)
    audiences = {document.path: document.audience for document in scope.all(KnowledgeDocument)}

    assert audiences[INTERNAL] is Audience.INTERNAL
    assert set(audiences) == CUSTOMER_FACING | {INTERNAL}
    assert all(audiences[path] is Audience.CUSTOMER for path in CUSTOMER_FACING)
    # The point of the audience: the discount ceilings are not reachable by a customer.
    retrieved = PgVectorRetriever(scope).search("qual o desconto maximo que posso conceder?", 10)
    assert INTERNAL not in {hit.chunk.document_id for hit in retrieved}


@pytest.mark.db
def test_seeding_twice_does_not_duplicate_documents_or_versions(seeding, db):
    first = seed_development_tenant()
    before = _scope(db, first.id)
    documents = {d.path: d.current_version_id for d in before.all(KnowledgeDocument)}
    versions = len(before.all(KnowledgeVersion))

    again = seed_development_tenant()

    assert again.id == first.id
    assert again.state is TenantState.ACTIVE
    after = _scope(db, first.id)
    assert {d.path: d.current_version_id for d in after.all(KnowledgeDocument)} == documents
    assert len(after.all(KnowledgeVersion)) == versions
    assert len(after.all(Customer)) == 20
