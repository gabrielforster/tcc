"""Intent classification for inbound customer messages.

The five intents are the ones the schedule names: a question about the debt, a request for
a new payment slip, a negotiation attempt, a dispute, and a claim that payment was already
made. Everything else is `unknown`, which is a routing decision rather than a failure — an
unclassified message goes to a human rather than getting a confident wrong answer.

**This classifier is lexical, and that is a deliberate baseline rather than the intended
final implementation.** It scores a message against per-intent cue phrases, using the same
character n-gram similarity as retrieval so Portuguese inflection is handled without a
stemmer. Two reasons to have it:

* the responsive agent runs end to end with no API key, so the whole pipeline is testable
  and reviewable before any model is wired in;
* it gives the LLM classifier something to be measured against. "The LLM classified 91%"
  is not a result on its own; "91% against a lexical baseline at 74% on the same messages"
  is.

`confidence` is a margin, not a probability: the gap between the best and second-best
intent score. It is the right shape for the decision being made — how sure are we that this
is *this* intent rather than that one — and it must not be reported as a calibrated
probability, because it is not one.
"""

from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from collection.rag.embeddings import TfidfEmbedder

#: Below this margin the message is treated as unclear and escalated.
MIN_CONFIDENCE = 0.04


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


#: Cue phrases per intent, in the language customers actually write in.
CUES: dict[Intent, list[str]] = {
    Intent.DEBT_INQUIRY: [
        "quanto eu devo",
        "qual o valor da minha divida",
        "tenho alguma pendencia em aberto",
        "qual o vencimento da fatura",
        "quero saber sobre o meu debito",
    ],
    Intent.SECOND_COPY: [
        "quero a segunda via do boleto",
        "pode reenviar o boleto",
        "me manda o codigo de barras",
        "preciso do link de pagamento",
        "manda o pix para eu pagar",
    ],
    Intent.NEGOTIATION: [
        "posso parcelar a divida",
        "consigo dividir em quantas vezes",
        "tem desconto para pagar a vista",
        "nao tenho como pagar tudo agora",
        "quero fazer um acordo",
        "da para diminuir o valor",
    ],
    Intent.DISPUTE: [
        "nao reconheco essa divida",
        "esse valor esta errado",
        "eu nunca comprei isso",
        "ja cancelei esse contrato",
        "vou procurar o procon",
    ],
    Intent.PAYMENT_CLAIM: [
        "ja paguei esse boleto",
        "fiz o pagamento ontem",
        "segue o comprovante do pagamento",
        "paguei mas continua aparecendo em aberto",
        "o pix ja foi feito",
    ],
    Intent.OPT_OUT: [
        "nao quero mais receber mensagens",
        "parem de me ligar",
        "me tirem da lista de cobranca",
        "nao me mandem mais nada",
    ],
    Intent.SMALL_TALK: [
        "bom dia tudo bem",
        "boa tarde",
        "boa noite",
        "obrigado pela ajuda",
        "ok entendi obrigado",
        "oi",
    ],
}


@dataclass(frozen=True)
class Classification:
    intent: Intent
    confidence: float
    scores: dict[str, float]

    @property
    def is_confident(self) -> bool:
        return self.intent is not Intent.UNKNOWN and self.confidence >= MIN_CONFIDENCE


class IntentClassifier:
    """Lexical baseline classifier. See the module docstring for why it is lexical."""

    name = "lexical"

    def __init__(self, cues: dict[Intent, list[str]] | None = None) -> None:
        self.cues = cues or CUES
        self._intents: list[Intent] = []
        self._corpus: list[str] = []
        for intent, phrases in self.cues.items():
            for phrase in phrases:
                self._intents.append(intent)
                self._corpus.append(phrase)
        self.embedder = TfidfEmbedder().fit(self._corpus)
        self._vectors = self.embedder.encode(self._corpus)

    def classify(self, message: str) -> Classification:
        if not message.strip():
            return Classification(Intent.UNKNOWN, 0.0, {})

        similarity = self._vectors @ self.embedder.encode([message])[0]

        # An intent scores as its single best-matching cue, not the average: a message
        # matching one phrase strongly is a match, even if the intent's other phrases
        # are unrelated to it.
        per_intent: dict[Intent, float] = {}
        for intent, score in zip(self._intents, similarity, strict=True):
            per_intent[intent] = max(per_intent.get(intent, 0.0), float(score))

        ranked = sorted(per_intent.items(), key=lambda item: item[1], reverse=True)
        best, best_score = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = best_score - runner_up

        scores = {intent.value: round(score, 4) for intent, score in ranked}
        if best_score <= 0 or margin < MIN_CONFIDENCE:
            return Classification(Intent.UNKNOWN, round(margin, 4), scores)
        return Classification(best, round(margin, 4), scores)


def evaluate(classifier: IntentClassifier, labelled: list[tuple[str, Intent]]) -> dict:
    """Accuracy and per-intent recall over labelled messages."""
    correct = 0
    per_intent: dict[str, list[int]] = {}
    for message, expected in labelled:
        predicted = classifier.classify(message).intent
        hit = int(predicted is expected)
        correct += hit
        per_intent.setdefault(expected.value, []).append(hit)

    return {
        "accuracy": correct / max(len(labelled), 1),
        "n": len(labelled),
        "recall_by_intent": {
            intent: round(float(np.mean(hits)), 3) for intent, hits in sorted(per_intent.items())
        },
    }
