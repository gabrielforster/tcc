"""Reads and writes for one tenant, with the predicate already in them.

The guard in `session.py` is what makes a missing predicate an error; this is what makes
writing the predicate unnecessary. Every method starts from `select(model).where(tenant_id
== ...)`, so the narrow path is also the shortest one and nobody has a reason to build a
statement by hand.

`get` deliberately does not use `Session.get`: that can answer from the identity map
without issuing a statement, which would hand back a row loaded earlier under another
scope. Asking the database with the predicate attached means another tenant's row is
`None` here, not an object that happens to be lying around.
"""

import uuid
from typing import Any, TypeVar

from sqlalchemy import Select, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from collection.db.session import TENANT_COLUMN, CrossTenantWrite, bind_tenant, is_tenant_scoped

T = TypeVar("T")


class TenantScope:
    """Every read and write made through this object is confined to one tenant."""

    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        bind_tenant(session, tenant_id)
        self.session = session
        self.tenant_id = tenant_id

    def add(self, obj: Any) -> None:
        """Stamp an object with this tenant and stage it, refusing one that is not ours."""
        self._scoped_mapper(type(obj))
        carried = getattr(obj, TENANT_COLUMN, None)
        if carried is not None and carried != self.tenant_id:
            raise CrossTenantWrite(
                f"{type(obj).__name__} belongs to tenant {carried}, "
                f"and this scope serves {self.tenant_id}"
            )
        setattr(obj, TENANT_COLUMN, self.tenant_id)
        self.session.add(obj)

    def select(self, model: type[T]) -> Select[tuple[T]]:
        """A `Select` for this tenant's rows, to be narrowed further by the caller."""
        self._scoped_mapper(model)
        return select(model).where(getattr(model, TENANT_COLUMN) == self.tenant_id)

    def all(self, model: type[T], *criteria: Any) -> list[T]:
        return list(self.session.scalars(self.select(model).where(*criteria)).all())

    def get(self, model: type[T], id: Any) -> T | None:
        """This tenant's row with that primary key, or `None` -- including when it is theirs."""
        mapper = self._scoped_mapper(model)
        key = mapper.primary_key
        if len(key) != 1:
            raise TypeError(f"{model.__name__} has a composite primary key; use `all` instead")
        return self.session.scalars(self.select(model).where(key[0] == id)).first()

    def _scoped_mapper(self, model: type[Any]):
        mapper = sa_inspect(model, raiseerr=False)
        if mapper is None or not is_tenant_scoped(mapper):
            raise TypeError(
                f"{model.__name__} is not tenant-scoped; read it through the platform scope"
            )
        return mapper
