"""Persistence: the declarative base, the tenant mixin and the scoped repository.

`session` is imported here for its side effect. The tenant guard is a pair of listeners
registered when that module loads, so leaving it to be imported by whoever happens to
need `unit_of_work` would make the boundary depend on an import order -- and the one path
that skipped the import would be the one that leaked. Importing anything under
`collection.db` now installs the guard.
"""

from collection.db import session as session  # noqa: F401
