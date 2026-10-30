"""Files in object storage, each with a row that says what it is.

Nothing the application depends on may live on disk; a local path is a development
convenience. This is the store the pipeline's frames, models and metrics go to.

Keys are `tenants/<tenant_id>/<kind>/<name>` or `platform/<kind>/<name>`. The prefix is
fixed by who the store serves, never by the caller: `put` composes the key from a `kind`
and a `name` that are each validated to be a single plain path segment, and `get` and
`exists` take a full key and refuse any that is not under this store's own prefix. A
tenant's store therefore cannot be pointed at another tenant's data by any argument --
`..`, a leading `/`, an empty segment or another tenant's whole key all fail before a
request is made. A scope-less store serves the platform prefix only.

The bytes are written before the row. A crash between the two leaves an orphan object,
which costs a few bytes; the other order leaves a row promising a file that is not there.

`ObjectStoreError` wraps every backend failure for the reason `LLMError` does: callers
decide what a missing or unreachable file means (fall back, retry, fail the request), and
cannot if each failure is a different botocore type. The bucket is created on first use
because a fresh MinIO has none. MinIO's image has no healthcheck, so readiness is not
assumed: the client retries its first calls instead.
"""

import uuid
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.orm import Session

from collection.config import settings
from collection.db.models.stored_object import StoredObject
from collection.db.repository import TenantScope
from collection.db.session import unit_of_work

PLATFORM_PREFIX = "platform/"


class ObjectStoreError(RuntimeError):
    """The backend failed or the key is not this store's to touch."""


def _segment(value: str, what: str) -> str:
    if (
        not value
        or value in (".", "..")
        or "/" in value
        or "\\" in value
        or any(ord(c) < 32 for c in value)
    ):
        raise ObjectStoreError(f"{what} must be a single plain path segment, got {value!r}")
    return value


class ObjectStore:
    def __init__(
        self,
        scope: TenantScope | None = None,
        *,
        bucket: str | None = None,
        endpoint_url: str | None = None,
        session: Session | None = None,
    ) -> None:
        """`session` is only for the platform store, to write its rows in a caller's
        transaction; without it a platform write opens and commits its own."""
        self.scope = scope
        self.bucket = bucket or settings.s3_bucket
        self.prefix = f"tenants/{scope.tenant_id}/" if scope is not None else PLATFORM_PREFIX
        self._session = session
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url or settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name="us-east-1",
            config=Config(
                connect_timeout=3,
                read_timeout=30,
                retries={"max_attempts": 4, "mode": "standard"},
                s3={"addressing_style": "path"},
            ),
        )
        self._bucket_ready = False

    def put(self, key: str, data: bytes, *, kind: str, content_type: str) -> StoredObject:
        """Store `data` as `<prefix><kind>/<key>` and return the row describing it."""
        full_key = f"{self.prefix}{_segment(kind, 'kind')}/{_segment(key, 'name')}"
        self._call(
            self._client.put_object,
            Bucket=self._ensure_bucket(),
            Key=full_key,
            Body=data,
            ContentType=content_type,
        )
        return self._record(full_key, kind, content_type, len(data))

    def get(self, key: str) -> bytes:
        response = self._call(
            self._client.get_object, Bucket=self._ensure_bucket(), Key=self._own(key)
        )
        return self._call(response["Body"].read)

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._ensure_bucket(), Key=self._own(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise ObjectStoreError(f"could not check {key!r}: {exc}") from exc
        except BotoCoreError as exc:
            raise ObjectStoreError(f"could not check {key!r}: {exc}") from exc
        return True

    def _own(self, key: str) -> str:
        """The key, if it is under this store's prefix and is made of plain segments."""
        if not key.startswith(self.prefix):
            raise ObjectStoreError(f"{key!r} is not under {self.prefix!r}")
        for part in key[len(self.prefix) :].split("/"):
            _segment(part, "key segment")
        return key

    def _call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStoreError(str(exc)) from exc

    def _ensure_bucket(self) -> str:
        if not self._bucket_ready:
            try:
                self._client.head_bucket(Bucket=self.bucket)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in (
                    "404",
                    "NoSuchBucket",
                    "NotFound",
                ):
                    raise ObjectStoreError(str(exc)) from exc
                self._call(self._client.create_bucket, Bucket=self.bucket)
            except BotoCoreError as exc:
                raise ObjectStoreError(str(exc)) from exc
            self._bucket_ready = True
        return self.bucket

    def _record(self, full_key: str, kind: str, content_type: str, size: int) -> StoredObject:
        if self.scope is not None:
            session = self.scope.session
            row = self.scope.all(StoredObject, StoredObject.key == full_key)
            return self._upsert(
                session, row[0] if row else None, full_key, kind, content_type, size, self.scope.add
            )
        if self._session is not None:
            return self._platform_row(self._session, full_key, kind, content_type, size)
        with unit_of_work(platform=True) as session:
            stored = self._platform_row(session, full_key, kind, content_type, size)
            session.flush()
            session.expunge(stored)
            return stored

    def _platform_row(
        self, session: Session, full_key: str, kind: str, content_type: str, size: int
    ) -> StoredObject:
        existing = session.scalars(select(StoredObject).where(StoredObject.key == full_key)).first()
        return self._upsert(session, existing, full_key, kind, content_type, size, session.add)

    @staticmethod
    def _upsert(
        session: Session,
        existing: StoredObject | None,
        full_key: str,
        kind: str,
        content_type: str,
        size: int,
        add: Any,
    ) -> StoredObject:
        row = existing or StoredObject(id=uuid.uuid4(), key=full_key)
        row.kind, row.content_type, row.size_bytes = kind, content_type, size
        if existing is None:
            add(row)
        session.flush()
        session.refresh(row)
        return row
