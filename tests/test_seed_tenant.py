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
from collection.data.sources.synthetic import SyntheticSource
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
from collection.db.seed import seed_development_tenant, slugify
from collection.rag.retriever import PgVectorRetriever

CUSTOMER_FACING = {
    "faq.md",
    "politica-de-cobranca.md",
    "script-de-negociacao.md",
    "tabela-de-parcelamento.csv",
}
INTERNAL = "limites-de-alcada.md"
#: The dataset the `seeding` fixture shrinks the synthetic source down to.
CUSTOMERS, INVOICES = 20, 60


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
    monkeypatch.setattr(settings, "synthetic_n_customers", CUSTOMERS)
    monkeypatch.setattr(settings, "synthetic_n_invoices", INVOICES)


def _seeded_customer_ids() -> set[str]:
    """The external ids the shrunk source describes, which is what seeding must write."""
    source = SyntheticSource(n_customers=CUSTOMERS, n_invoices=INVOICES)
    return {str(cid) for cid in source.extract().customers["customer_id"]}


def _assert_the_source_was_ingested(scope: TenantScope) -> None:
    """The rows this seeding wrote, named rather than counted against the whole table.

    The receivables are the honest witness. Ingestion replaces a tenant's receivables as a
    set, so immediately after seeding the tenant holds exactly the ones the shrunk source
    carries and none of the ones a previous, larger seeding committed -- a count that is
    about this call rather than about the database it ran against. A seeding that quietly
    stopped writing anything leaves the earlier receivables (or none at all) and fails here.

    The customers cannot be counted the same way: ingestion matches them on `external_id`
    and deliberately leaves the ones the source does not name alone, so a tenant seeded
    before at full size legitimately keeps the rest. They are pinned instead by requiring
    every fresh receivable to hang off one of the twenty the source describes, which the
    pre-existing rows cannot satisfy on their own.
    """
    external_ids = [c.external_id for c in scope.all(Customer)]
    assert _seeded_customer_ids() <= set(external_ids)
    # The guard the old absolute count carried: re-ingesting updates a customer in place,
    # it never adds a second row for the same external id.
    assert len(external_ids) == len(set(external_ids))

    by_id = {c.id: c.external_id for c in scope.all(Customer)}
    receivables = scope.all(Receivable)
    assert len(receivables) == INVOICES
    assert {by_id[r.customer_id] for r in receivables} <= _seeded_customer_ids()


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
    _assert_the_source_was_ingested(scope)
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
    _assert_the_source_was_ingested(after)


def test_a_name_with_nothing_to_slug_is_refused_rather_than_slugged_to_nothing():
    """An empty slug would be created once and collide on the unique constraint after."""
    with pytest.raises(ValueError, match="tenant slug"):
        slugify("###")
