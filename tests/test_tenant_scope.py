"""The tenant boundary: what the guard refuses, and what it lets through.

The two probe models below exist only here. A guard that is only ever proved against the
one table that happens to exist today proves very little, and adding a speculative
production table to test with would be worse: these are mapped, migrated into the test
transaction and rolled back with it.
"""

import uuid

import pytest
from sqlalchemy import ForeignKey, String, delete, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship, sessionmaker

from collection.db import session as session_module
from collection.db.base import Base, TenantScoped
from collection.db.models import Tenant
from collection.db.repository import TenantScope
from collection.db.session import CrossTenantWrite, UnscopedQuery, unit_of_work


class ScopedProbe(TenantScoped, Base):
    __tablename__ = "scoped_probe"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    label: Mapped[str] = mapped_column(String(50))
    notes: Mapped[list["ScopedNote"]] = relationship(lazy="select")


class ScopedNote(TenantScoped, Base):
    __tablename__ = "scoped_note"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    probe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scoped_probe.id"))
    body: Mapped[str] = mapped_column(String(50))


#: Derived from the registry, not written by hand: a table added later without a scope
#: becomes a failing case here rather than a silent hole.
TENANT_SCOPED_MAPPERS = [
    mapper
    for mapper in Base.registry.mappers
    if mapper.local_table is not None and "tenant_id" in mapper.local_table.c
]


@pytest.fixture
def probes(db):
    """The probe tables, created inside the test transaction and rolled back with it."""
    bind = db.get_bind()
    ScopedProbe.__table__.create(bind)
    ScopedNote.__table__.create(bind)
    return None


def _tenant(db, slug: str) -> Tenant:
    tenant = Tenant(name=slug.title(), slug=slug)
    db.add(tenant)
    db.flush()
    return tenant


def _unbind(db) -> None:
    db.info.pop("tenant_id", None)
    db.info.pop("platform", None)


def test_the_probe_models_are_among_the_tenant_scoped_mappers():
    """Guards the guard: the parametrised test below must have cases to run."""
    assert {ScopedProbe, ScopedNote} <= {mapper.class_ for mapper in TENANT_SCOPED_MAPPERS}


@pytest.mark.parametrize("mapper", TENANT_SCOPED_MAPPERS, ids=lambda m: m.class_.__name__)
def test_every_tenant_scoped_mapper_declares_the_mixin(mapper):
    """The injected predicate hangs off `TenantScoped`; a hand-rolled column would miss it."""
    assert issubclass(mapper.class_, TenantScoped)


@pytest.mark.db
@pytest.mark.parametrize("mapper", TENANT_SCOPED_MAPPERS, ids=lambda m: m.class_.__name__)
def test_every_tenant_scoped_mapper_is_unreachable_without_a_tenant(db, mapper):
    with pytest.raises(UnscopedQuery):
        db.execute(select(mapper.class_)).all()


@pytest.mark.db
def test_a_table_without_a_tenant_column_is_readable_without_a_tenant(db):
    """The guard refuses what is scoped, not everything: `tenants` is the one it hangs off."""
    db.execute(select(Tenant)).all()


@pytest.mark.db
def test_a_bulk_update_or_delete_is_refused_without_a_tenant(db):
    with pytest.raises(UnscopedQuery):
        db.execute(update(ScopedProbe).values(label="x"))
    with pytest.raises(UnscopedQuery):
        db.execute(delete(ScopedProbe))


@pytest.mark.db
def test_a_lazy_load_across_a_relationship_is_refused_without_a_tenant(db, probes):
    tenant = _tenant(db, "lazy")
    scope = TenantScope(db, tenant.id)
    probe = ScopedProbe(id=uuid.uuid4(), label="p")
    scope.add(probe)
    scope.add(ScopedNote(probe_id=probe.id, body="n"))
    db.flush()
    db.expire_all()
    probe = scope.get(ScopedProbe, probe.id)
    _unbind(db)

    with pytest.raises(UnscopedQuery):
        list(probe.notes)


@pytest.mark.db
def test_select_through_a_scope_returns_only_that_tenants_rows(db, probes):
    one, other = _tenant(db, "one"), _tenant(db, "other")
    TenantScope(db, one.id).add(ScopedProbe(label="mine"))
    db.flush()
    _unbind(db)
    TenantScope(db, other.id).add(ScopedProbe(label="theirs"))
    db.flush()
    _unbind(db)

    assert [p.label for p in TenantScope(db, one.id).all(ScopedProbe)] == ["mine"]
    _unbind(db)
    assert [p.label for p in TenantScope(db, other.id).all(ScopedProbe)] == ["theirs"]


@pytest.mark.db
def test_a_hand_written_select_on_a_bound_session_still_sees_one_tenant(db, probes):
    """The predicate is injected, not assumed: there is no spelling that reads across."""
    one, other = _tenant(db, "one"), _tenant(db, "other")
    TenantScope(db, one.id).add(ScopedProbe(label="mine"))
    db.flush()
    _unbind(db)
    TenantScope(db, other.id).add(ScopedProbe(label="theirs"))
    db.flush()
    db.expunge_all()

    _unbind(db)
    TenantScope(db, one.id)
    assert [p.label for p in db.scalars(select(ScopedProbe)).all()] == ["mine"]

    db.expunge_all()
    _unbind(db)
    TenantScope(db, other.id)
    assert [p.label for p in db.scalars(select(ScopedProbe)).all()] == ["theirs"]


@pytest.mark.db
def test_a_bulk_update_on_a_bound_session_cannot_touch_another_tenants_row(db, probes):
    one, other = _tenant(db, "one"), _tenant(db, "other")
    TenantScope(db, one.id).add(ScopedProbe(label="mine"))
    db.flush()
    _unbind(db)
    TenantScope(db, other.id).add(ScopedProbe(label="theirs"))
    db.flush()
    db.expunge_all()

    _unbind(db)
    TenantScope(db, one.id)
    db.execute(update(ScopedProbe).values(label="rewritten"))
    db.flush()
    db.expunge_all()
    _unbind(db)

    assert [p.label for p in TenantScope(db, other.id).all(ScopedProbe)] == ["theirs"]


@pytest.mark.db
def test_all_narrows_further_with_extra_criteria(db, probes):
    tenant = _tenant(db, "narrow")
    scope = TenantScope(db, tenant.id)
    scope.add(ScopedProbe(label="keep"))
    scope.add(ScopedProbe(label="drop"))
    db.flush()

    found = scope.all(ScopedProbe, ScopedProbe.label == "keep")

    assert [p.label for p in found] == ["keep"]


@pytest.mark.db
def test_add_refuses_an_object_carrying_another_tenants_id(db, probes):
    one, other = _tenant(db, "one"), _tenant(db, "other")
    probe = ScopedProbe(label="theirs", tenant_id=other.id)

    with pytest.raises(CrossTenantWrite):
        TenantScope(db, one.id).add(probe)

    assert probe not in db.new


@pytest.mark.db
def test_add_stamps_the_bound_tenant_on_an_unstamped_object(db, probes):
    tenant = _tenant(db, "stamp")
    probe = ScopedProbe(label="mine")

    TenantScope(db, tenant.id).add(probe)

    assert probe.tenant_id == tenant.id


@pytest.mark.db
def test_a_scoped_row_cannot_be_flushed_without_a_tenant_bound(db, probes):
    """The write side of the same boundary: `session.add` is not a way around the scope."""
    tenant = _tenant(db, "direct")
    _unbind(db)
    db.add(ScopedProbe(label="smuggled", tenant_id=tenant.id))

    with pytest.raises(CrossTenantWrite):
        db.flush()


@pytest.mark.db
def test_a_session_serves_one_tenant(db, probes):
    one, other = _tenant(db, "one"), _tenant(db, "other")
    TenantScope(db, one.id)

    with pytest.raises(CrossTenantWrite):
        TenantScope(db, other.id)


@pytest.mark.db
def test_get_returns_none_for_another_tenants_row(db, probes):
    one, other = _tenant(db, "one"), _tenant(db, "other")
    theirs = ScopedProbe(label="theirs")
    TenantScope(db, other.id).add(theirs)
    db.flush()
    db.expunge_all()
    _unbind(db)

    assert TenantScope(db, one.id).get(ScopedProbe, theirs.id) is None
    _unbind(db)
    assert TenantScope(db, other.id).get(ScopedProbe, theirs.id).label == "theirs"


@pytest.mark.db
def test_a_scope_refuses_a_model_that_is_not_tenant_scoped(db):
    with pytest.raises(TypeError):
        TenantScope(db, uuid.uuid4()).select(Tenant)


@pytest.mark.db
def test_unit_of_work_binds_the_tenant_and_commits(db, probes, monkeypatch):
    tenant = _tenant(db, "uow")
    _bind_unit_of_work_to(db, monkeypatch)

    with unit_of_work(tenant.id) as session:
        assert session.info["tenant_id"] == tenant.id
        TenantScope(session, tenant.id).add(ScopedProbe(label="committed"))

    _unbind(db)
    assert [p.label for p in TenantScope(db, tenant.id).all(ScopedProbe)] == ["committed"]


@pytest.mark.db
def test_unit_of_work_rolls_back_on_an_exception(db, probes, monkeypatch):
    tenant = _tenant(db, "rollback")
    _bind_unit_of_work_to(db, monkeypatch)

    with pytest.raises(ZeroDivisionError), unit_of_work(tenant.id) as session:
        TenantScope(session, tenant.id).add(ScopedProbe(label="doomed"))
        session.flush()
        raise ZeroDivisionError("the unit of work must not survive this")

    _unbind(db)
    assert TenantScope(db, tenant.id).all(ScopedProbe) == []


@pytest.mark.db
def test_the_platform_scope_may_read_tenants_without_a_tenant_bound(db, monkeypatch):
    tenant = _tenant(db, "platform")
    _bind_unit_of_work_to(db, monkeypatch)

    with unit_of_work(platform=True) as session:
        assert session.info["platform"] is True
        slugs = session.scalars(select(Tenant.slug).where(Tenant.id == tenant.id)).all()

    assert list(slugs) == ["platform"]


@pytest.mark.db
def test_the_platform_scope_is_never_the_default(db, probes, monkeypatch):
    """The exception has to be asked for: a plain unit of work still refuses a scoped read."""
    _bind_unit_of_work_to(db, monkeypatch)

    with unit_of_work() as session:
        assert session.info.get("platform") is not True
        with pytest.raises(UnscopedQuery):
            session.execute(select(ScopedProbe)).all()


def _bind_unit_of_work_to(db, monkeypatch) -> None:
    """Point `unit_of_work` at the test transaction, so its commit is still rolled back."""
    factory = sessionmaker(
        bind=db.get_bind(), class_=Session, join_transaction_mode="create_savepoint"
    )
    monkeypatch.setattr(session_module, "session_factory", lambda: factory)
