"""The delivery ladder on rows: the in-memory log's contract, kept by the database version.

`tests/test_channels.py` pins the ladder for `DeliveryLog`; these are the same properties
asked of `DatabaseDeliveryLog`, plus what only rows can get wrong: that each rung stamps
its own column, that a delivery cannot point at another tenant's conversation, and that two
reports racing for one row cannot leave it contradicting itself.
"""

import importlib.util
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from collection.channels.delivery import BackwardsStatus, DatabaseDeliveryLog, DeliveryStatus
from collection.db.models import Conversation, Customer, Delivery, Tenant
from collection.db.repository import TenantScope
from collection.db.transcripts import record_turn


def _scope(db: Session, slug: str = "one") -> TenantScope:
    tenant = Tenant(name=slug, slug=slug)
    db.add(tenant)
    db.flush()
    session = Session(bind=db.connection(), join_transaction_mode="create_savepoint")
    return TenantScope(session, tenant.id)


def _conversation(scope: TenantScope) -> Conversation:
    customer = Customer(external_id="C1", pseudonym="p")
    scope.add(customer)
    scope.session.flush()
    conversation = Conversation(customer_id=customer.id, channel="web")
    scope.add(conversation)
    scope.session.flush()
    return conversation


class _Clock:
    """A clock that moves only when told to, so each rung gets a distinguishable stamp."""

    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


def _log(db: Session, clock=None):
    scope = _scope(db)
    conversation = _conversation(scope)
    log = DatabaseDeliveryLog(scope, **({"clock": clock} if clock else {}))
    return scope, conversation, log


@pytest.mark.db
def test_a_recorded_message_starts_enviada_and_can_be_read_back(db):
    scope, conversation, log = _log(db)
    record = log.record("oi", owner="sess_1", conversation=conversation)
    assert record.status is DeliveryStatus.ENVIADA
    assert record.owner == "sess_1"
    assert log.get(record.id) == record


@pytest.mark.db
def test_status_only_moves_forward(db):
    scope, conversation, log = _log(db)
    rec = log.record("oi", conversation=conversation)
    log.advance(rec.id, DeliveryStatus.LIDA)
    with pytest.raises(ValueError) as caught:
        log.advance(rec.id, DeliveryStatus.ENTREGUE)
    assert isinstance(caught.value, BackwardsStatus)
    assert caught.value.current is DeliveryStatus.LIDA
    assert caught.value.attempted is DeliveryStatus.ENTREGUE
    assert log.get(rec.id).status is DeliveryStatus.LIDA


@pytest.mark.db
def test_entregue_after_lida_raises_backwards_status(db):
    scope, conversation, log = _log(db)
    rec = log.record("oi", conversation=conversation)
    log.advance(rec.id, DeliveryStatus.ENTREGUE)
    log.advance(rec.id, DeliveryStatus.LIDA)
    with pytest.raises(BackwardsStatus) as caught:
        log.advance(rec.id, DeliveryStatus.ENTREGUE)
    assert caught.value.delivery_id == rec.id
    assert (caught.value.current, caught.value.attempted) == (
        DeliveryStatus.LIDA,
        DeliveryStatus.ENTREGUE,
    )
    row = scope.get(Delivery, uuid.UUID(rec.id))
    assert row.status is DeliveryStatus.LIDA
    assert row.delivered_at is not None and row.read_at is not None


@pytest.mark.db
def test_repeating_the_current_status_is_a_no_op(db):
    clock = _Clock()
    scope, conversation, log = _log(db, clock)
    rec = log.record("oi", conversation=conversation)
    clock.now += 10
    first = log.advance(rec.id, DeliveryStatus.LIDA)
    clock.now += 10
    again = log.advance(rec.id, DeliveryStatus.LIDA)
    assert again == first
    # The retry must not restamp: the record says when the claim first arrived.
    assert again.at == first.at


@pytest.mark.db
def test_rungs_may_be_skipped(db):
    scope, conversation, log = _log(db)
    rec = log.record("oi", conversation=conversation)
    assert log.advance(rec.id, DeliveryStatus.RESPONDIDA).status is DeliveryStatus.RESPONDIDA
    row = scope.get(Delivery, uuid.UUID(rec.id))
    assert row.delivered_at is None and row.read_at is None
    assert row.replied_at is not None


@pytest.mark.db
def test_an_unknown_delivery_id_raises_key_error(db):
    scope, conversation, log = _log(db)
    for unknown in (f"dlv_{uuid.uuid4().hex}", str(uuid.uuid4()), "dlv_nope", ""):
        with pytest.raises(KeyError):
            log.advance(unknown, DeliveryStatus.LIDA)
        with pytest.raises(KeyError):
            log.get(unknown)
    assert scope.all(Delivery) == []


@pytest.mark.db
def test_each_rung_stamps_its_own_timestamp(db):
    clock = _Clock()
    scope, conversation, log = _log(db, clock)
    rec = log.record("oi", conversation=conversation)
    sent = clock.now
    stamps = {}
    for status in (DeliveryStatus.ENTREGUE, DeliveryStatus.LIDA, DeliveryStatus.RESPONDIDA):
        clock.now += 60
        record = log.advance(rec.id, status)
        assert record.at == clock.now
        stamps[status] = clock.now

    row = scope.get(Delivery, uuid.UUID(rec.id))
    expected = {
        "sent_at": sent,
        "delivered_at": stamps[DeliveryStatus.ENTREGUE],
        "read_at": stamps[DeliveryStatus.LIDA],
        "replied_at": stamps[DeliveryStatus.RESPONDIDA],
    }
    for column, seconds in expected.items():
        assert getattr(row, column) == datetime.fromtimestamp(seconds, UTC), column


@pytest.mark.db
def test_a_delivery_can_be_tied_to_the_turn_it_answers(db):
    scope, conversation, log = _log(db)
    turn = record_turn(scope, conversation, role="agent", text="oi")
    rec = log.record("oi", conversation=conversation, turn=turn)
    assert scope.get(Delivery, uuid.UUID(rec.id)).turn_id == turn.id


@pytest.mark.db
def test_another_tenants_delivery_is_an_unknown_id(db):
    scope, conversation, log = _log(db)
    rec = log.record("oi", conversation=conversation)
    other = DatabaseDeliveryLog(_scope(db, "two"))
    with pytest.raises(KeyError):
        other.advance(rec.id, DeliveryStatus.LIDA)
    with pytest.raises(KeyError):
        other.get(rec.id)
    assert log.get(rec.id).status is DeliveryStatus.ENVIADA


@pytest.mark.db
def test_a_delivery_cannot_point_at_another_tenants_conversation(db):
    mine = _scope(db, "one")
    theirs = _conversation(_scope(db, "two"))
    row = Delivery(conversation_id=theirs.id, sent_at=datetime.now(UTC))
    mine.add(row)
    with pytest.raises(IntegrityError):
        mine.session.flush()


def test_the_migration_statuses_equal_the_live_enum():
    path = Path(__file__).parent.parent / "migrations" / "versions" / "0006_deliveries.py"
    spec = importlib.util.spec_from_file_location("migration_0006", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert list(migration.STATUSES) == [m.value for m in DeliveryStatus]


@pytest.fixture
def committed_delivery(db_engine):
    """A delivery committed for real, because a race needs two connections to see it.

    The `db` fixture's rolled-back transaction is invisible to a second connection, so
    this one cleans up by hand.
    """
    slug = f"race-{uuid.uuid4().hex[:8]}"
    with Session(db_engine) as session:
        tenant = Tenant(name=slug, slug=slug)
        session.add(tenant)
        session.commit()
        scope = TenantScope(session, tenant.id)
        conversation = _conversation(scope)
        record = DatabaseDeliveryLog(scope).record("oi", conversation=conversation)
        session.commit()
        tenant_id = tenant.id
    yield db_engine, tenant_id, record.id
    with db_engine.begin() as conn:
        for table in ("deliveries", "turns", "conversations", "customers"):
            conn.execute(text(f"delete from {table} where tenant_id = :t"), {"t": tenant_id})
        conn.execute(text("delete from tenants where id = :t"), {"t": tenant_id})


@pytest.mark.db
def test_a_late_report_racing_a_reply_cannot_move_the_row_backwards(committed_delivery):
    """A reply holds the row; a client's LIDA retry arrives meanwhile and must lose.

    Without the row lock the retry would read `enviada`, pass the ladder check, and
    overwrite `respondida` once the reply committed. With it, the retry waits, re-reads
    `respondida`, and is refused as a late report.
    """
    engine, tenant_id, delivery_id = committed_delivery
    reply = Session(engine)
    reply_log = DatabaseDeliveryLog(TenantScope(reply, tenant_id))
    reply_log.advance(delivery_id, DeliveryStatus.RESPONDIDA)  # row lock held, uncommitted

    outcome: dict[str, object] = {}
    started = threading.Event()

    def late_report() -> None:
        with Session(engine) as session:
            log = DatabaseDeliveryLog(TenantScope(session, tenant_id))
            started.set()
            try:
                log.advance(delivery_id, DeliveryStatus.LIDA)
                session.commit()
                outcome["result"] = "advanced"
            except BackwardsStatus as exc:
                outcome["result"] = exc

    worker = threading.Thread(target=late_report)
    worker.start()
    started.wait(5)
    worker.join(0.5)
    assert worker.is_alive(), "the late report should be waiting on the reply's row lock"
    reply.commit()
    reply.close()
    worker.join(10)

    assert isinstance(outcome["result"], BackwardsStatus)
    assert outcome["result"].current is DeliveryStatus.RESPONDIDA
    with Session(engine) as session:
        final = DatabaseDeliveryLog(TenantScope(session, tenant_id)).get(delivery_id)
    assert final.status is DeliveryStatus.RESPONDIDA
