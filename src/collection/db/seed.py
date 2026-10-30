"""One command that turns a fresh clone into a tenant that can answer.

Everything the serving path reads now lives in the record: a tenant row with a lifecycle,
knowledge as versioned rows with vectors, customers and receivables. A clone has none of
it, so `collection serve` on a new machine used to greet a visitor and then retrieve
nothing. Writing the setup out as instructions in the README does not fix that -- a list
of steps is a thing to get wrong, and the first step a reader gets wrong is the one that
leaves a half-provisioned tenant behind.

So the setup is code. It creates the tenant, publishes the repository's own `knowledge/`
directory into it, ingests the synthetic source, and leaves the tenant `ACTIVE`, which is
the only state the chat will serve.

The lifecycle is climbed honestly rather than written straight to `ACTIVE`: `INGESTING`
while the data is loading, `READY` once it is, `ACTIVE` at the end. A tenant that fails
half way through therefore stays in the state it actually reached, and is not servable --
which is the whole point of the ladder, and would be a lie if seeding set the last rung
itself.

Re-running is safe and is the expected thing to do after editing a file under
`knowledge/`: `publish_document` is idempotent on content, so unchanged bytes keep their
version and an edit mints the next one, and ingestion replaces the tenant's rows rather
than adding to them. A tenant that is already `ACTIVE` simply stays there.

`limites-de-alcada.md` is published `INTERNAL` and everything else `CUSTOMER`, because
that file holds the discount ceilings and the escalation triggers. The retriever filters
on the audience; this is where the filter is given something to act on, and getting it
wrong here is the disclosure the filter exists to prevent.
"""

import re
import uuid
from pathlib import Path

from sqlalchemy import select

from collection.config import settings
from collection.data.ingest import ingest
from collection.data.sources.synthetic import SyntheticSource
from collection.db.models import Audience, Tenant, TenantState, advance
from collection.db.models.tenant import InvalidTransition
from collection.db.repository import TenantScope
from collection.db.session import unit_of_work
from collection.rag.documents import SUPPORTED, load_file
from collection.rag.store import publish_document

#: The documents a customer must never be quoted from: discount ceilings, authority limits.
INTERNAL_DOCUMENTS = frozenset({"limites-de-alcada.md"})

#: The lifecycle in order, so seeding can climb from wherever a previous run stopped.
_LADDER = (
    TenantState.PROVISIONED,
    TenantState.INGESTING,
    TenantState.READY,
    TenantState.ACTIVE,
)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _climb_to(tenant: Tenant, rung: TenantState) -> None:
    """Advance up the ladder to `rung`, one rung at a time, from wherever the tenant is.

    A tenant already at or above the rung stays where it is, which is what makes a second
    seeding a no-op rather than an `InvalidTransition`. A suspended tenant is refused:
    whoever suspended it decides whether it may serve again, and a seeding script is not
    that person.
    """
    if tenant.state not in _LADDER:
        raise InvalidTransition(tenant.state, rung)
    for next_rung in _LADDER[_LADDER.index(tenant.state) + 1 : _LADDER.index(rung) + 1]:
        advance(tenant, next_rung)


def _title(body: str, path: Path) -> str:
    """The document's own first heading, or its file name read as words."""
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem.replace("-", " ").capitalize()


def knowledge_documents(directory: Path | str | None = None) -> list[tuple[str, str, str]]:
    """Every file under `knowledge/` as `(path, title, body)`, in a stable order.

    The loader is reused rather than reading the bytes here so that a CSV becomes the same
    row-per-line text the in-memory index was always built from, and so that the file types
    this corpus may contain are decided in one place. The rows are joined back into one
    body because a document is a file: `publish_document` versions a path, and a table
    whose rows were separate documents would version and cite row by row.
    """
    root = Path(directory or settings.dir_knowledge)
    documents = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED:
            continue
        body = "\n\n".join(part.text for part in load_file(path, root))
        documents.append((str(path.relative_to(root)), _title(body, path), body))
    return documents


def seed_development_tenant(name: str = "Desenvolvimento") -> Tenant:
    """Create (or re-seed) the development tenant and leave it servable."""
    tenant_id = _provision(name)
    with unit_of_work(tenant_id) as session:
        scope = TenantScope(session, tenant_id)
        for path, title, body in knowledge_documents():
            audience = Audience.INTERNAL if path in INTERNAL_DOCUMENTS else Audience.CUSTOMER
            publish_document(scope, title=title, path=path, body=body, audience=audience)
        ingest(scope, SyntheticSource())
    return _activate(tenant_id)


def _provision(name: str) -> uuid.UUID:
    """The tenant row, created if this is the first run, and moved to `INGESTING`."""
    with unit_of_work(platform=True) as session:
        slug = slugify(name)
        tenant = session.scalars(select(Tenant).where(Tenant.slug == slug)).first()
        if tenant is None:
            tenant = Tenant(name=name, slug=slug)
            session.add(tenant)
        _climb_to(tenant, TenantState.INGESTING)
        return tenant.id


def _activate(tenant_id: uuid.UUID) -> Tenant:
    """The last two rungs, and the tenant the caller gets back.

    It is returned from inside the unit of work and read after it: that works because the
    session factory sets `expire_on_commit=False`, so a committed object keeps its loaded
    attributes instead of needing a refresh on a session that is already closed.
    """
    with unit_of_work(platform=True) as session:
        tenant = session.scalars(select(Tenant).where(Tenant.id == tenant_id)).one()
        _climb_to(tenant, TenantState.ACTIVE)
        return tenant
