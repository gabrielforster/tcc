"""The delivery ladder's vocabulary, apart from the code that moves messages along it.

Kept apart from `collection.channels.delivery` for the same reason `intent.py` is kept
apart from the classifier: the database records a delivery's status, so the models and
migrations must be able to name the ladder, and the channel module in turn needs the
models to persist it. Without a third module the two would import each other.
`collection.channels.delivery` re-exports it, so existing imports hold, and the meaning
of each rung is documented there.
"""

from enum import StrEnum


class DeliveryStatus(StrEnum):
    """The ladder, lowest rung first. Names are the schedule's vocabulary, in Portuguese."""

    ENVIADA = "enviada"
    ENTREGUE = "entregue"
    LIDA = "lida"
    RESPONDIDA = "respondida"
