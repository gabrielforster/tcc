"""The object store against a real MinIO, in a bucket of its own.

Every run creates a uniquely named bucket and deletes it afterwards, so a developer's
configured bucket is never read from or written to. The fixture skips when MinIO is
unreachable and fails instead under COLLECTION_REQUIRE_DB=1, as `db_engine` does.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from collection.db import session as session_module
from collection.db.models import StoredObject, Tenant
from collection.db.repository import TenantScope
from collection.db.session import CrossTenantWrite, UnscopedQuery, bind_tenant
from collection.storage.objects import ObjectStore, ObjectStoreError


def _tenant_scope(db: Session, slug: str) -> TenantScope:
    tenant = Tenant(name=slug, slug=slug)
    db.add(tenant)
    db.flush()
    session = Session(bind=db.connection(), join_transaction_mode="create_savepoint")
    return TenantScope(session, tenant.id)


def _platform_session(db: Session) -> Session:
    session = Session(bind=db.connection(), join_transaction_mode="create_savepoint")
    bind_tenant(session, None, platform=True)
    return session


@pytest.mark.db
def test_put_writes_the_object_and_a_row_describing_it(db, bucket):
    scope = _tenant_scope(db, "one")
    store = ObjectStore(scope, bucket=bucket)

    stored = store.put("a.parquet", b"12345", kind="frames", content_type="application/x-parquet")

    assert stored.key == f"tenants/{scope.tenant_id}/frames/a.parquet"
    assert store.exists(stored.key)
    row = scope.all(StoredObject, StoredObject.key == stored.key)[0]
    assert (row.tenant_id, row.kind, row.content_type, row.size_bytes) == (
        scope.tenant_id,
        "frames",
        "application/x-parquet",
        5,
    )
    assert row.created_at is not None


@pytest.mark.db
def test_a_platform_object_has_no_tenant_and_lives_under_platform(db, bucket):
    session = _platform_session(db)
    store = ObjectStore(bucket=bucket, session=session)

    stored = store.put("model.joblib", b"m", kind="models", content_type="application/octet-stream")

    assert stored.key == "platform/models/model.joblib"
    assert stored.tenant_id is None
    assert store.get(stored.key) == b"m"
    assert session.scalars(select(StoredObject).where(StoredObject.key == stored.key)).one()


@pytest.mark.db
def test_putting_a_key_twice_replaces_it_and_keeps_one_row(db, bucket):
    scope = _tenant_scope(db, "one")
    store = ObjectStore(scope, bucket=bucket)

    store.put("m.json", b"old", kind="metrics", content_type="application/json")
    stored = store.put("m.json", b"newer", kind="metrics", content_type="application/json")

    assert store.get(stored.key) == b"newer"
    rows = scope.all(StoredObject, StoredObject.key == stored.key)
    assert [r.size_bytes for r in rows] == [5]


@pytest.mark.db
def test_get_returns_exactly_what_put_stored(db, bucket):
    store = ObjectStore(_tenant_scope(db, "one"), bucket=bucket)
    payload = bytes(range(256)) * 5

    stored = store.put("blob.bin", payload, kind="raw", content_type="application/octet-stream")

    assert store.get(stored.key) == payload


@pytest.mark.db
def test_a_tenant_scoped_store_cannot_read_another_tenants_key(db, bucket):
    mine = ObjectStore(_tenant_scope(db, "one"), bucket=bucket)
    theirs = ObjectStore(_tenant_scope(db, "two"), bucket=bucket)
    secret = theirs.put("s.txt", b"secret", kind="raw", content_type="text/plain")

    with pytest.raises(ObjectStoreError):
        mine.get(secret.key)
    with pytest.raises(ObjectStoreError):
        mine.exists(secret.key)
    assert theirs.get(secret.key) == b"secret"


@pytest.mark.db
@pytest.mark.parametrize(
    "hostile",
    ["../x", "a/../../x", "/abs", "a//b", "a\\b", "", ".", "a/."],
)
def test_no_name_can_reach_outside_the_stores_prefix(db, bucket, hostile, monkeypatch):
    scope = _tenant_scope(db, "one")
    store = ObjectStore(scope, bucket=bucket)
    writes = []
    monkeypatch.setattr(store._client, "put_object", lambda **kw: writes.append(kw))

    with pytest.raises(ObjectStoreError):
        store.put(hostile, b"x", kind="raw", content_type="text/plain")
    with pytest.raises(ObjectStoreError):
        store.put("ok", b"x", kind=hostile, content_type="text/plain")
    with pytest.raises(ObjectStoreError):
        store.get(f"tenants/{scope.tenant_id}/raw/{hostile}")
    assert writes == []


@pytest.mark.db
@pytest.mark.parametrize("field", ["kind", "content_type"])
def test_an_overlong_column_is_refused_before_anything_is_uploaded(db, bucket, field, monkeypatch):
    store = ObjectStore(_tenant_scope(db, "one"), bucket=bucket)
    writes = []
    monkeypatch.setattr(store._client, "put_object", lambda **kw: writes.append(kw))
    args = {"kind": "k", "content_type": "t"} | {field: "x" * 300}

    with pytest.raises(ObjectStoreError):
        store.put("n", b"x", **args)
    assert writes == []


@pytest.mark.db
def test_a_tenant_session_sees_no_platform_row(db, bucket):
    platform = _platform_session(db)
    ObjectStore(bucket=bucket, session=platform).put(
        "m.bin", b"m", kind="models", content_type="text/plain"
    )
    scope = _tenant_scope(db, "one")

    assert scope.all(StoredObject) == []
    assert scope.session.scalars(select(StoredObject)).all() == []


@pytest.mark.db
def test_a_session_with_no_tenant_cannot_query_stored_objects(db):
    session = Session(bind=db.connection(), join_transaction_mode="create_savepoint")

    with pytest.raises(UnscopedQuery):
        session.scalars(select(StoredObject)).all()


@pytest.mark.db
def test_a_platform_session_reads_platform_rows(db, bucket):
    session = _platform_session(db)
    stored = ObjectStore(bucket=bucket, session=session).put(
        "m.bin", b"m", kind="models", content_type="text/plain"
    )

    # Scoped to the key this test wrote: a platform session deliberately sees every row,
    # so listing the table would also return whatever a seeded tenant left behind.
    rows = session.scalars(select(StoredObject).where(StoredObject.key == stored.key)).all()
    assert [(row.key, row.tenant_id) for row in rows] == [(stored.key, None)]


@pytest.mark.db
def test_a_tenant_session_cannot_add_a_null_tenant_row(db):
    scope = _tenant_scope(db, "one")
    scope.session.add(
        StoredObject(tenant_id=None, kind="k", key="platform/k/x", content_type="t", size_bytes=1)
    )

    with pytest.raises(CrossTenantWrite):
        scope.session.flush()


@pytest.mark.db
def test_a_platform_store_without_a_session_commits_through_its_own_unit_of_work(
    db, bucket, monkeypatch
):
    factory = sessionmaker(bind=db.connection(), join_transaction_mode="create_savepoint")
    monkeypatch.setattr(session_module, "session_factory", lambda: factory)

    stored = ObjectStore(bucket=bucket).put("m.bin", b"m", kind="models", content_type="text/plain")

    assert stored.key == "platform/models/m.bin" and stored.tenant_id is None
    check = _platform_session(db)
    keys = select(StoredObject.key).where(StoredObject.key == stored.key)
    assert check.scalars(keys).all() == [stored.key]


@pytest.mark.db
def test_a_key_under_the_platform_prefix_is_not_readable_by_a_tenant(db, bucket):
    platform = ObjectStore(bucket=bucket, session=_platform_session(db))
    stored = platform.put("m.bin", b"m", kind="models", content_type="application/octet-stream")
    tenant = ObjectStore(_tenant_scope(db, "one"), bucket=bucket)

    with pytest.raises(ObjectStoreError):
        tenant.get(stored.key)


@pytest.mark.db
def test_a_missing_key_raises_object_store_error_not_a_boto_exception(db, bucket):
    scope = _tenant_scope(db, "one")
    store = ObjectStore(scope, bucket=bucket)

    with pytest.raises(ObjectStoreError) as caught:
        store.get(f"tenants/{scope.tenant_id}/raw/nothing-here")

    assert caught.type is ObjectStoreError
    assert store.exists(f"tenants/{scope.tenant_id}/raw/nothing-here") is False


def test_an_unreachable_backend_raises_object_store_error():
    store = ObjectStore(
        endpoint_url="http://127.0.0.1:1",
        bucket="nope",
        session=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(ObjectStoreError):
        store.get("platform/models/x")
