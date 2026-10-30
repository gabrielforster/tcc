"""Importing the package registers every model on `Base.metadata`.

Alembic and the test fixtures read the metadata, so a model that is not imported here is
invisible to autogenerate.
"""

from collection.db.models.conversation import (
    Conversation,
    ConversationState,
    Turn,
    TurnRetrieval,
)
from collection.db.models.customer import Customer, Receivable, ReceivableStatus, days_overdue
from collection.db.models.delivery import Delivery
from collection.db.models.internal_user import InternalUser
from collection.db.models.knowledge import (
    Audience,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeVersion,
)
from collection.db.models.stored_object import StoredObject
from collection.db.models.tenant import InvalidTransition, Tenant, TenantState, advance

__all__ = [
    "Audience",
    "Conversation",
    "ConversationState",
    "Customer",
    "Delivery",
    "InternalUser",
    "InvalidTransition",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "KnowledgeVersion",
    "Receivable",
    "ReceivableStatus",
    "StoredObject",
    "Tenant",
    "TenantState",
    "Turn",
    "TurnRetrieval",
    "advance",
    "days_overdue",
]
