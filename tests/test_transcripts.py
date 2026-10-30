import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from collection.db.models import (
    Conversation,
    Customer,
    KnowledgeChunk,
    Tenant,
    Turn,
    TurnRetrieval,
)
from collection.db.repository import TenantScope
from collection.db.transcripts import record_turn
from collection.domain.intent import Intent
from collection.rag.chunking import Chunk
from collection.rag.index import Retrieved
from collection.rag.store import publish_document


def _scope(db: Session) -> TenantScope:
    tenant = Tenant(name="one", slug="one")
    db.add(tenant)
    db.flush()
    session = Session(bind=db.connection(), join_transaction_mode="create_savepoint")
    return TenantScope(session, tenant.id)


def _conversation(scope: TenantScope) -> Conversation:
    customer = Customer(external_id="C1", pseudonym="p")
    scope.add(customer)
    scope.session.flush()
    conversation = Conversation(customer_id=customer.id, channel="whatsapp")
    scope.add(conversation)
    scope.session.flush()
    return conversation


def _passages(scope: TenantScope, version_id) -> list[Retrieved]:
    chunks = scope.all(KnowledgeChunk, KnowledgeChunk.version_id == version_id)
    chunks.sort(key=lambda c: c.ordinal)
    return [
        Retrieved(
            chunk=Chunk(text=c.text, document_id="d", ordinal=c.ordinal),
            score=0.9 - 0.1 * i,
            chunk_id=c.id,
            version_id=c.version_id,
        )
        for i, c in enumerate(chunks)
    ]


def _publish(scope: TenantScope, body: str):
    return publish_document(scope, title="T", path="faq/t.md", body=body)


@pytest.mark.db
def test_a_turn_with_passages_writes_one_retrieval_row_per_passage_with_its_rank(db):
    scope = _scope(db)
    conversation = _conversation(scope)
    version = _publish(scope, "# A\n\n" + "palavra " * 1500 + "\n\n# B\n\n" + "outra " * 1500)
    passages = _passages(scope, version.id)
    assert len(passages) >= 2

    turn = record_turn(
        scope, conversation, role="agent", text="resposta", passages=passages, model="m"
    )
    scope.session.flush()

    rows = sorted(scope.all(TurnRetrieval, TurnRetrieval.turn_id == turn.id), key=lambda r: r.rank)
    assert [r.rank for r in rows] == list(range(1, len(passages) + 1))
    assert [r.chunk_id for r in rows] == [p.chunk_id for p in passages]
    assert [r.score for r in rows] == pytest.approx([p.score for p in passages])
    assert all(r.version_id == version.id for r in rows)
    assert turn.model == "m"


@pytest.mark.db
def test_a_turn_with_no_passages_is_still_written(db):
    scope = _scope(db)
    conversation = _conversation(scope)

    turn = record_turn(scope, conversation, role="agent", text="sem base")
    scope.session.flush()

    assert scope.get(Turn, turn.id) is not None
    assert scope.all(TurnRetrieval, TurnRetrieval.turn_id == turn.id) == []


@pytest.mark.db
def test_a_retrieval_row_points_at_the_version_that_was_read_not_the_current_one(db):
    scope = _scope(db)
    conversation = _conversation(scope)
    old = _publish(scope, "# A\n\nO prazo e de 30 dias.")
    passages = _passages(scope, old.id)
    new = _publish(scope, "# A\n\nO prazo agora e de 60 dias.")
    assert new.id != old.id

    turn = record_turn(scope, conversation, role="agent", text="r", passages=passages)
    scope.session.flush()

    rows = scope.all(TurnRetrieval, TurnRetrieval.turn_id == turn.id)
    assert {r.version_id for r in rows} == {old.id}


@pytest.mark.db
def test_a_passage_without_a_chunk_id_is_refused_by_pre_validation_before_anything_is_staged(db):
    scope = _scope(db)
    conversation = _conversation(scope)
    version = _publish(scope, "# A\n\nTexto.")
    good = _passages(scope, version.id)[0]
    bad = Retrieved(chunk=good.chunk, score=0.5)

    with pytest.raises(ValueError):
        record_turn(scope, conversation, role="agent", text="r", passages=[good, bad])
    scope.session.flush()

    assert scope.all(Turn) == []
    assert scope.all(TurnRetrieval) == []


@pytest.mark.db
def test_a_conversation_keeps_its_turns_in_order_across_two_units_of_work(db):
    scope = _scope(db)
    conversation = _conversation(scope)
    record_turn(scope, conversation, role="customer", text="primeira")
    record_turn(scope, conversation, role="agent", text="segunda")
    scope.session.commit()

    second = TenantScope(
        Session(bind=db.connection(), join_transaction_mode="create_savepoint"), scope.tenant_id
    )
    record_turn(second, conversation, role="customer", text="terceira")
    second.session.commit()

    third = TenantScope(
        Session(bind=db.connection(), join_transaction_mode="create_savepoint"), scope.tenant_id
    )
    turns = third.all(Turn, Turn.conversation_id == conversation.id)
    turns.sort(key=lambda t: t.created_at)
    assert [t.text for t in turns] == ["primeira", "segunda", "terceira"]


@pytest.mark.db
def test_a_failure_mid_write_leaves_no_turn_and_no_partial_retrieval_rows(db):
    scope = _scope(db)
    conversation = _conversation(scope)
    one = _publish(scope, "# A\n\nTexto da primeira versao.")
    good = _passages(scope, one.id)[0]
    two = _publish(scope, "# A\n\nTexto bem diferente da segunda versao.")
    other = _passages(scope, two.id)[0]
    # A real chunk, cited under a version it does not belong to.
    liar = Retrieved(chunk=other.chunk, score=0.1, chunk_id=other.chunk_id, version_id=one.id)

    with pytest.raises(IntegrityError):
        record_turn(scope, conversation, role="agent", text="r", passages=[good, liar])

    assert scope.all(Turn) == []
    assert scope.all(TurnRetrieval) == []
    # The session survived: the savepoint was rolled back, not the whole transaction.
    assert len(scope.all(Conversation)) == 1


def test_the_migration_intents_equal_the_live_enum():
    path = Path(__file__).parent.parent / "migrations" / "versions" / "0005_conversations.py"
    spec = importlib.util.spec_from_file_location("migration_0005", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert list(migration.INTENTS) == [m.value for m in Intent]


def test_the_models_load_without_the_classifier_stack():
    code = (
        "import sys, collection.db.models;"
        "bad = [m for m in sys.modules if m.startswith('collection.agents') or m == 'numpy'];"
        "sys.exit(1 if bad else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
