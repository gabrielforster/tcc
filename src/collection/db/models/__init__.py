"""Importing the package registers every model on `Base.metadata`.

Alembic and the test fixtures read the metadata, so a model that is not imported here is
invisible to autogenerate.
"""

from collection.db.models.tenant import InvalidTransition, Tenant, TenantState, advance

__all__ = ["InvalidTransition", "Tenant", "TenantState", "advance"]
