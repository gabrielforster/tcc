"""The intents a customer message can be classified as.

Kept apart from the classifier because the database records an intent on every turn: the
models and migrations must be able to name the vocabulary without loading numpy and the
embedding stack. `collection.agents.intent` re-exports it, so existing imports hold.
"""

from enum import StrEnum


class Intent(StrEnum):
    DEBT_INQUIRY = "debt_inquiry"
    SECOND_COPY = "second_copy"
    NEGOTIATION = "negotiation"
    DISPUTE = "dispute"
    PAYMENT_CLAIM = "payment_claim"
    OPT_OUT = "opt_out"
    #: Greetings and courtesy. Not one of the five intents the schedule names, but a real
    #: category in any customer channel -- and without it "bom dia, tudo bem?" collided
    #: with the negotiation cue "nao tenho como pagar tudo agora" on the shared word.
    SMALL_TALK = "small_talk"
    UNKNOWN = "unknown"
