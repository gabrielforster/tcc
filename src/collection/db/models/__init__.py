"""Importing the package registers every model on `Base.metadata`.

Alembic and the test fixtures read the metadata, so a model that is not imported here is
invisible to autogenerate.
"""

from collection.db.models.customer import Customer, Receivable, ReceivableStatus, days_overdue
from collection.db.models.internal_user import InternalUser
from collection.db.models.knowledge import (
    Audience,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeVersion,
)
from collection.db.models.tenant import InvalidTransition, Tenant, TenantState, advance

__all__ = [
    "Audience",
    "Customer",
    "InternalUser",
    "InvalidTransition",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "KnowledgeVersion",
    "Receivable",
    "ReceivableStatus",
    "Tenant",
    "TenantState",
    "advance",
    "days_overdue",
]
