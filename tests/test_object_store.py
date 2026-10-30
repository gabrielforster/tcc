"""The object store against a real MinIO, in a bucket of its own.

Every run creates a uniquely named bucket and deletes it afterwards, so a developer's
configured bucket is never read from or written to. The fixture skips when MinIO is
unreachable and fails instead under COLLECTION_REQUIRE_DB=1, as `db_engine` does.
"""

import os
import uuid

import boto3
import pytest
from botocore.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from collection.config import settings
from collection.db.models import StoredObject, Tenant
from collection.db.repository import TenantScope
from collection.db.session import bind_tenant
from collection.storage.objects import ObjectStore, ObjectStoreError


@pytest.fixture(scope="session")
def bucket():
    name = f"collection-test-{uuid.uuid4().hex[:12]}"
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name="us-east-1",
        config=Config(connect_timeout=3, retries={"max_attempts": 1}),
    )
    try:
        client.list_buckets()
    except Exception as exc:
        message = f"MinIO is unreachable at {settings.s3_endpoint_url}: {exc}"
        if os.environ.get("COLLECTION_REQUIRE_DB") == "1":
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    yield name
    try:
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=name):
            for item in page.get("Contents", []):
                client.delete_object(Bucket=name, Key=item["Key"])
        client.delete_bucket(Bucket=name)
    except Exception:
        pass  # the bucket was never created if no test got as far as a first use


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
def test_no_name_can_reach_outside_the_stores_prefix(db, bucket, hostile):
    scope = _tenant_scope(db, "one")
    store = ObjectStore(scope, bucket=bucket)
    other = ObjectStore(_tenant_scope(db, "two"), bucket=bucket)
    other.put("s.txt", b"secret", kind="raw", content_type="text/plain")

    with pytest.raises(ObjectStoreError):
        store.put(hostile, b"x", kind="raw", content_type="text/plain")
    with pytest.raises(ObjectStoreError):
        store.put("ok", b"x", kind=hostile, content_type="text/plain")
    with pytest.raises(ObjectStoreError):
        store.get(f"tenants/{scope.tenant_id}/raw/{hostile}")


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
