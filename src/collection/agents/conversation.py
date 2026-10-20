"""Turning a thread into something the agent can answer against.

Until now the responsive agent answered each message on its own. That is wrong in the only
way customers actually write: "posso parcelar?" followed by "e em 6 vezes?" is one
question asked in two messages, and the second retrieves nothing useful by itself.

Two things need the thread, and they need it differently.

**Retrieval** needs a query that stands alone. A follow-up is resolved into one here,
behind a `QueryRewriter` seam with three implementations -- no context (the control),
lexical concatenation (the baseline) and an LLM rewrite. The same three-arm shape as every
other comparison in this project, for the same reason: a neural result means nothing
without a cheap one beside it.

**Intent classification** needs the previous intent. "sim" and "6" carry no intent at all;
inheriting the last confident one is what makes them answerable.

That inheritance is where the care goes, because it must not swallow the safety rule that
hands a customer to a person after three messages nobody understood. A measurement decided
the design:

    'e em 6 vezes?'        margin=0.3060  best=0.4320   -> a confident follow-up
    'sim'                  margin=0.0000  best=0.0000   -> a follow-up, no signal at all
    '6'                    margin=0.0000  best=0.0000   -> a follow-up, no signal at all
    'asdfgh qwerty zxcvb'  margin=0.0069  best=0.0822   -> gibberish, *more* signal than
                                                           either real follow-up

Confidence cannot separate a follow-up from nonsense; character n-grams give gibberish a
spurious score while a bare "sim" scores exactly zero. **Vocabulary can.** A follow-up
reuses words the conversation or the knowledge base already contains; "asdfgh" appears in
neither. So inheritance requires known words, and gibberish still escalates.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace

from collection.agents.intent import Classification, Intent, IntentClassifier

#: Turns of context carried into retrieval and generation. Four is two exchanges: enough
#: to resolve "e em 6 vezes?", short enough that a topic change two questions ago does not
#: still colour the answer.
CONTEXT_WINDOW = 4
#: A message this short is a continuation rather than a new question.
FOLLOW_UP_MAX_WORDS = 6
#: Share of a message's words that must be known before its intent may be inherited.
MIN_KNOWN_RATIO = 0.5

_WORD = re.compile(r"[0-9a-zà-ÿ]+", re.IGNORECASE)

#: Words a continuation is made of, seeded into the vocabulary.
#:
#: Drawing the vocabulary from the knowledge base alone misses them: the corpus is policy
#: and FAQ text, and it turns out to contain no "sim" at all. Without this seed the most
#: common follow-up in any conversation would be treated as nonsense. These are
#: closed-class words of agreement and reference, not domain terms, so listing them is
#: stable in a way that listing domain vocabulary would not be.
CONTINUATION_WORDS = frozenset(
    # fmt: off
    [
        "sim",
        "nao",
        "não",
        "ok",
        "certo",
        "claro",
        "isso",
        "esse",
        "essa",
        "aquele",
        "aquela",
        "quero",
        "queria",
        "pode",
        "podia",
        "consigo",
        "posso",
        "tambem",
        "também",
        "entao",
        "então",
        "mais",
        "menos",
        "tudo",
        "todo",
        "toda",
        "qual",
        "quais",
        "quanto",
        "quantos",
        "quantas",
        "quando",
        "onde",
        "como",
        "porque",
        "por",
        "que",
        "e",
        "ou",
        "mas",
        "so",
        "só",
        "ja",
        "já",
        "ainda",
        "talvez",
        "beleza",
        "blz",
        "obrigado",
        "obrigada",
        "valeu",
        "entendi",
        "sei",
        "nenhum",
        "nenhuma",
        "outro",
        "outra",
    ]
    # fmt: on
)


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


@dataclass(frozen=True)
class Turn:
    role: str
    text: str
    intent: str | None = None


@dataclass
class Conversation:
    """What has been said so far, oldest first."""

    turns: list[Turn] = field(default_factory=list)

    def add(self, role: str, text: str, intent: str | None = None) -> "Conversation":
        self.turns.append(Turn(role=role, text=text, intent=intent))
        return self

    @property
    def customer_turns(self) -> list[Turn]:
        return [turn for turn in self.turns if turn.role == "customer"]

    def window(self, size: int = CONTEXT_WINDOW) -> list[Turn]:
        return self.turns[-size:] if size > 0 else []

    def last_intent(self) -> Intent | None:
        """The most recent intent the classifier was confident about."""
        for turn in reversed(self.turns):
            if turn.role == "customer" and turn.intent:
                try:
                    intent = Intent(turn.intent)
                except ValueError:
                    continue
                if intent not in (Intent.UNKNOWN, Intent.SMALL_TALK):
                    return intent
        return None

    def last_customer_message(self) -> str | None:
        customer = self.customer_turns
        return customer[-1].text if customer else None

    def transcript(self, size: int = CONTEXT_WINDOW) -> str:
        """The recent thread as plain text, for a prompt."""
        labels = {"customer": "Cliente", "agent": "Atendimento"}
        return "\n".join(
            f"{labels.get(turn.role, turn.role)}: {' '.join(turn.text.split())}"
            for turn in self.window(size)
        )

    def __len__(self) -> int:
        return len(self.turns)

    def __bool__(self) -> bool:
        return bool(self.turns)


def is_follow_up(message: str, conversation: Conversation | None) -> bool:
    """A short message in an ongoing thread continues it rather than starting one.

    Length alone, deliberately. Trying to detect anaphora lexically ("e", "sim", "esse")
    misses the cases that matter -- a bare "6" is a follow-up and contains no cue at all --
    while a long message carries enough of its own words to retrieve on.
    """
    if not conversation or not conversation.customer_turns:
        return False
    return len(words(message)) <= FOLLOW_UP_MAX_WORDS


# --------------------------------------------------------------------- rewriting
class QueryRewriter(ABC):
    """How a follow-up becomes something to retrieve on.

    The interface returns a **list** of queries, not one string, and that is the whole
    lesson of building this module. The first version replaced the customer's message
    with the message plus the previous turn, and measurably made retrieval worse --
    follow-up hit rate fell from 0.89 to 0.78, because a long previous question dominates
    the TF-IDF vector of a four-word follow-up. Weighting the follow-up did not rescue it:
    the vectoriser uses sublinear term frequency, so repeating a term barely moves it.

    Returning several queries fixes the shape of the problem rather than tuning around it.
    The customer's own words are always one of the queries, so context can only *add*
    candidates; the agent merges the results and keeps the best. A strategy cannot lose a
    passage the message alone would have found.
    """

    name: str

    @abstractmethod
    def queries(self, message: str, conversation: Conversation | None) -> list[str]:
        """Every query worth retrieving on for this turn, the message always among them."""

    def rewrite(self, message: str, conversation: Conversation | None) -> str:
        """The resolved query, for reporting. The last one is the most context-aware."""
        return self.queries(message, conversation)[-1]


class NoContextRewriter(QueryRewriter):
    """The control: the message alone, which is what the agent did before this module."""

    name = "none"

    def queries(self, message: str, conversation: Conversation | None) -> list[str]:
        return [message]


class ConcatRewriter(QueryRewriter):
    """Lexical baseline: the message, plus the message joined to the previous turn.

    Cheap, deterministic and needs no key. Because the bare message is retrieved on too,
    the failure mode that sank the single-query version -- a customer changing subject in
    a short message, dragging the old topic along -- costs nothing: the contextual query
    returns the wrong passages, the plain one returns the right ones, and the merge keeps
    the best of both.
    """

    name = "concat"

    def queries(self, message: str, conversation: Conversation | None) -> list[str]:
        if not is_follow_up(message, conversation):
            return [message]
        previous = conversation.last_customer_message() if conversation else None
        return [message, f"{previous} {message}"] if previous else [message]


class LLMRewriter(QueryRewriter):
    """Ask the model to turn a follow-up into a standalone question.

    Falls back to the lexical baseline on any generation failure, for the same reason the
    responder does: a rewrite that raises takes the whole answer down with it, and a
    slightly worse query is better than no answer.
    """

    name = "llm"

    SYSTEM = (
        "Você reescreve a última mensagem do cliente como uma pergunta independente, "
        "usando o contexto da conversa. Responda apenas com a pergunta reescrita, sem "
        "explicação. Se a mensagem já for independente, repita-a sem alterações. "
        "Não invente informação que não esteja na conversa."
    )

    def __init__(self, client=None, fallback: QueryRewriter | None = None) -> None:
        from collection.llm.client import get_client

        self.client = client or get_client()
        self.fallback = fallback or ConcatRewriter()

    def queries(self, message: str, conversation: Conversation | None) -> list[str]:
        from collection.llm.client import LLMError

        if not is_follow_up(message, conversation):
            return [message]
        prompt = (
            f"CONVERSA:\n{conversation.transcript() if conversation else ''}\n\n"
            f"ÚLTIMA MENSAGEM DO CLIENTE:\n{message}\n\n"
            "Pergunta independente:"
        )
        try:
            rewritten = self.client.complete(self.SYSTEM, prompt).text.strip()
        except LLMError:
            return self.fallback.queries(message, conversation)
        # An empty or runaway rewrite is worse than the baseline's noise.
        if not rewritten or len(words(rewritten)) > 60:
            return self.fallback.queries(message, conversation)
        return [message, rewritten]


REWRITERS: dict[str, type[QueryRewriter]] = {
    "none": NoContextRewriter,
    "concat": ConcatRewriter,
    "llm": LLMRewriter,
}


def get_rewriter(name: str = "concat") -> QueryRewriter:
    if name not in REWRITERS:
        raise ValueError(f"Unknown rewriter: {name!r}. Available: {', '.join(REWRITERS)}")
    return REWRITERS[name]()


# ----------------------------------------------------------------------- intent
def build_vocabulary(texts: list[str], classifier: IntentClassifier | None = None) -> set[str]:
    """Every word the knowledge base and the cue phrases use.

    This is what separates "sim" from "asdfgh": both are unclassifiable, only one is a
    word anybody in this domain has written.
    """
    vocabulary: set[str] = set(CONTINUATION_WORDS)
    for text in texts:
        vocabulary.update(words(text))
    if classifier is not None:
        for phrases in classifier.cues.values():
            for phrase in phrases:
                vocabulary.update(words(phrase))
    return vocabulary


def known_ratio(message: str, vocabulary: set[str]) -> float:
    """Share of the message's words that appear in the vocabulary.

    Digits count as known: "6" is a perfectly good answer to "em quantas vezes?".
    """
    tokens = words(message)
    if not tokens:
        return 0.0
    known = sum(1 for token in tokens if token.isdigit() or token in vocabulary)
    return known / len(tokens)


def resolve_intent(
    classification: Classification,
    message: str,
    conversation: Conversation | None,
    vocabulary: set[str] | None = None,
) -> Classification:
    """Inherit the thread's intent when the message alone carries none.

    Three conditions, each load-bearing:

    * the classifier was **not confident** -- a message that classifies on its own is
      never overridden, so a topic change is honoured;
    * the message is a **short follow-up**, so a long rambling message still escalates;
    * its words are **known**, so gibberish still escalates. This is the condition the
      measurement in the module docstring forced: confidence alone would have let
      "asdfgh qwerty zxcvb" inherit `negotiation` and never reach a person.
    """
    if classification.is_confident or not is_follow_up(message, conversation):
        return classification
    inherited = conversation.last_intent() if conversation else None
    if inherited is None:
        return classification
    if vocabulary is not None and known_ratio(message, vocabulary) < MIN_KNOWN_RATIO:
        return classification
    return replace(classification, intent=inherited, inherited=True)
