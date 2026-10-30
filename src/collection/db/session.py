"""Where a tenant is bound to a session, and the one place a query can be refused.

Every tenant's rows share one table, so the difference between "my data" and "someone
else's data" is a predicate. Leaving that predicate to call sites makes correctness a
matter of remembering, and a call site that can be trusted to filter is a call site that
will eventually forget -- once, quietly, in a read that returns another customer's
invoices. So the boundary is enforced here instead, in two listeners on the `Session`
class, and the repository in `repository.py` is the ergonomic way to stay inside it
rather than the thing that enforces it.

A session carries its tenant in `session.info`. `do_orm_execute` refuses a read whose
mappers include a tenant-scoped table when no tenant is bound, and injects
`tenant_id = <bound>` into every statement when one is -- so a query that forgot the
predicate is not a leak, it is a query that returns nothing. `before_flush` refuses a
write of a tenant-scoped row that does not belong to the bound tenant. "Tenant-scoped" is
asked of the mapped table, not of a list kept here, so a table added later inherits the
guard by having a `tenant_id` column -- the same fact the migration already states. The
injected predicate hangs off the `TenantScoped` mixin, so a model that declared the
column by hand would be refused unscoped but not filtered when scoped; a test asserts
every scoped mapper carries the mixin, which keeps the two halves in step.

A predicate narrows a WHERE clause and says nothing about what is written into a row, so
the same listener also reads the values of an ORM INSERT or UPDATE: an INSERT has to name
this tenant, and an UPDATE may not move a row to another one. Without that, a row leaves
its tenant through the front door -- `update(Model).values(tenant_id=...)` is correctly
narrowed to our rows and then hands them away.

`platform=True` is the deliberate exception, for the rows that belong to no tenant: the
`tenants` table itself, and later anything a platform operator reads across tenants. It
is never a default and never inferred; a caller has to write it down, which is what makes
it auditable. It is also exclusive: a session is a platform session or a tenant's, and
`bind_tenant` refuses to turn one into the other, because a scope laid over a platform
session would look narrow while reading everything.

What the listener does not see, stated here rather than left to be discovered:

* **A statement that names the table rather than the mapped class.** `text(...)` is the
  obvious one, but `select(Model.__table__)`, `Model.__table__.insert()` and any Core
  statement built against the table are the same hole and do not look like raw SQL. None
  of them are ORM statements, so none reach this listener, on a bound session or an
  unbound one. `tests/test_tenant_scope.py` pins this with a characterisation test, so
  the gap is proven and visible rather than assumed. Going through the mapped class is
  the rule; a Core statement over a tenant-scoped table is the one thing a reviewer still
  has to look for.
* **`Session.get()` answered from the identity map** issues no statement at all, so it can
  hand back an object loaded earlier on the same session. That is why `TenantScope.get`
  asks the database instead of the identity map.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache

from sqlalchemy import BindParameter, ClauseElement, Engine, create_engine, event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Mapper, ORMExecuteState, Session, sessionmaker, with_loader_criteria

from collection.config import settings
from collection.db.base import TenantScoped

TENANT_COLUMN = "tenant_id"

#: A written value that is an expression rather than a literal. It cannot be proved to
#: stay inside the tenant, and "cannot prove" is refused rather than waved through.
_UNRESOLVED = object()


class UnscopedQuery(RuntimeError):
    """A read that would have crossed the tenant boundary because no tenant was bound."""


class CrossTenantWrite(RuntimeError):
    """A write of a row belonging to a tenant other than the one the session serves."""


@cache
def engine() -> Engine:
    # `pool_pre_ping` because a long-lived worker outlives a Postgres restart and would
    # otherwise hand out a dead connection on the next request.
    return create_engine(settings.database_url, pool_pre_ping=True)


@cache
def session_factory() -> sessionmaker[Session]:
    # `expire_on_commit=False` so attributes read after a commit do not trigger a refresh
    # outside the unit of work that the guard would then have to refuse.
    return sessionmaker(bind=engine(), expire_on_commit=False)


@contextmanager
def unit_of_work(
    tenant_id: uuid.UUID | None = None, *, platform: bool = False
) -> Iterator[Session]:
    """A session bound to one tenant for its whole life, committed or rolled back as one."""
    session = session_factory()()
    bind_tenant(session, tenant_id, platform=platform)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def bind_tenant(session: Session, tenant_id: uuid.UUID | None, *, platform: bool = False) -> None:
    """Bind the tenant a session serves. Rebinding it to another tenant is refused.

    A session is a unit of work for one tenant: objects already loaded into its identity
    map belong to the old tenant, so letting the binding move would leave them reachable
    under the new one.
    """
    bound = session.info.get(TENANT_COLUMN)
    if tenant_id is not None and bound is not None and bound != tenant_id:
        raise CrossTenantWrite(
            f"this session already serves tenant {bound}; it cannot also serve {tenant_id}"
        )
    if tenant_id is not None and session.info.get("platform"):
        raise CrossTenantWrite(
            "this is a platform session, which reads across tenants; a scope laid over it "
            f"would look like tenant {tenant_id} and not be one"
        )
    if platform and bound is not None:
        raise CrossTenantWrite(
            f"this session serves tenant {bound}; it cannot be raised to the platform scope"
        )
    if tenant_id is not None:
        session.info[TENANT_COLUMN] = tenant_id
    if platform:
        session.info["platform"] = True


def is_tenant_scoped(mapper: Mapper) -> bool:
    table = mapper.local_table
    return table is not None and TENANT_COLUMN in table.c


def _column_key(column: object) -> object:
    """The name an INSERT or UPDATE wrote under, whatever shape it was named by."""
    return getattr(column, "key", column)


def _literal(value: object) -> object:
    """A written value if it can be resolved, `_UNRESOLVED` if it is an expression."""
    if isinstance(value, BindParameter):
        return value.value
    if isinstance(value, ClauseElement):
        return _UNRESOLVED
    return value


def _written_rows(state: ORMExecuteState) -> list[dict[object, object]]:
    """Every column/value map an ORM INSERT or UPDATE will send, however it was spelled."""
    rows: list[dict[object, object]] = []
    values = getattr(state.statement, "_values", None)
    if values:
        rows.append({_column_key(k): _literal(v) for k, v in values.items()})
    for group in getattr(state.statement, "_multi_values", ()) or ():
        rows.extend({_column_key(k): _literal(v) for k, v in row.items()} for row in group)
    parameters = state.parameters
    if isinstance(parameters, dict):
        rows.append({_column_key(k): v for k, v in parameters.items()})
    elif isinstance(parameters, list | tuple):
        rows.extend({_column_key(k): v for k, v in row.items()} for row in parameters)
    return rows


def _refuse_a_write_that_leaves_the_tenant(state: ORMExecuteState, bound: uuid.UUID) -> None:
    """An INSERT must name this tenant; an UPDATE must not rename one."""
    for row in _written_rows(state):
        if TENANT_COLUMN not in row:
            # An UPDATE that does not touch the column leaves the row where it is; an
            # INSERT that omits it would land a row belonging to nobody.
            if state.is_insert:
                raise CrossTenantWrite(
                    f"this INSERT names no {TENANT_COLUMN}, and this session serves {bound}"
                )
            continue
        written = row[TENANT_COLUMN]
        if written != bound:
            raise CrossTenantWrite(
                f"this statement writes {TENANT_COLUMN}={written!r}, "
                f"and this session serves {bound}"
            )


@event.listens_for(Session, "do_orm_execute")
def _scope_the_statement_to_its_tenant(state: ORMExecuteState) -> None:
    """Every ORM statement passes here: selects, bulk writes, lazy loads and refreshes.

    Refusing a session with no tenant is only half of it: a session that has one could
    still be handed a statement with no predicate. So the predicate is injected rather
    than assumed, and a hand-written `select(Model)` returns that tenant's rows or
    nothing -- there is no spelling of an ORM query that reads across the boundary. A
    written value is checked rather than narrowed, because narrowing an UPDATE's WHERE
    says nothing about the tenant it sets.
    """
    info = state.session.info
    if info.get("platform"):
        return
    bound = info.get(TENANT_COLUMN)
    scoped = [mapper for mapper in state.all_mappers if is_tenant_scoped(mapper)]
    if bound is None:
        if scoped:
            raise UnscopedQuery(
                f"{scoped[0].class_.__name__} is tenant-scoped and this session binds no "
                "tenant; go through TenantScope, or ask for the platform scope explicitly"
            )
        return
    if scoped and (state.is_insert or state.is_update):
        _refuse_a_write_that_leaves_the_tenant(state, bound)
    # A column refresh reloads a row already filtered on its way in, a relationship load
    # inherits this criteria from the statement that loaded its parent, and an INSERT has
    # no rows to narrow -- it was checked above instead.
    if state.is_column_load or state.is_relationship_load or state.is_insert:
        return
    state.statement = state.statement.options(
        with_loader_criteria(TenantScoped, lambda cls: cls.tenant_id == bound, include_aliases=True)
    )


@event.listens_for(Session, "before_flush")
def _refuse_a_cross_tenant_write(
    session: Session, flush_context: object, instances: object
) -> None:
    """The write half: a staged row has to belong to the tenant the session serves."""
    if session.info.get("platform"):
        return
    bound = session.info.get(TENANT_COLUMN)
    for obj in (*session.new, *session.dirty, *session.deleted):
        mapper = sa_inspect(obj).mapper
        if not is_tenant_scoped(mapper):
            continue
        carried = getattr(obj, TENANT_COLUMN, None)
        if bound is None:
            raise CrossTenantWrite(
                f"{type(obj).__name__} is tenant-scoped and this session binds no tenant; "
                "stage it through TenantScope.add"
            )
        if carried != bound:
            raise CrossTenantWrite(
                f"{type(obj).__name__} belongs to tenant {carried}, and this session serves {bound}"
            )
