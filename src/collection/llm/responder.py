"""The LLM responder: the generation half of the responsive agent.

It plugs into the `Responder` seam the agent already defines, so retrieval, intent
classification and escalation are byte-for-byte the same as with `TemplateResponder`.
That is what makes the comparison in `evaluate.py` mean something: the only variable
between the two arms is how the answer is written.

Three behaviours are worth stating up front, because each is a decision rather than an
implementation detail.

**No passages, no call.** With nothing retrieved the answer hands over to a human, and the
model is never invoked. Asking a model to answer without evidence is asking it to invent,
and it also spends money to produce the one output that is guaranteed to be wrong.

**A refusal is a success.** The prompt gives the model `NAO_SEI` as an exit, and emitting
it marks the answer ungrounded, which the agent already routes to a person. A system that
can only answer will answer anything.

**Failure falls back, it does not raise.** Expired key, rate limit, timeout: the customer
still gets the templated reply. The fallback is recorded on the answer rather than hidden,
so an evaluation run cannot quietly report template results under the LLM's name.
"""

from collection.agents.intent import Intent
from collection.agents.responsive import Answer, Responder, TemplateResponder
from collection.llm.client import LLMClient, LLMError, get_client
from collection.llm.prompt import HANDOVER, REFUSAL, SYSTEM_PROMPT, build_user_prompt
from collection.rag.index import Retrieved


def _is_refusal(text: str) -> bool:
    """True when the model took the exit the prompt gave it.

    Matched on a stripped, punctuation-free form: models append a full stop to a bare token
    often enough that exact equality would miss real refusals.
    """
    return text.strip().strip(".!").upper() == REFUSAL


class LLMResponder(Responder):
    name = "llm"

    def __init__(
        self,
        client: LLMClient | None = None,
        fallback: Responder | None = None,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.client = client or get_client()
        self.fallback = fallback if fallback is not None else TemplateResponder()
        self.system_prompt = system_prompt
        #: Generation failures in this process, for the evaluation to report honestly.
        self.failures: list[str] = []

    def respond(self, message: str, intent: Intent, passages: list[Retrieved]) -> Answer:
        if not passages:
            return Answer(text=HANDOVER, citations=[], grounded=False, responder=self.name)

        user = build_user_prompt(message, intent, passages)
        try:
            completion = self.client.complete(self.system_prompt, user)
        except LLMError as error:
            self.failures.append(str(error))
            answer = self.fallback.respond(message, intent, passages)
            answer.responder = f"{self.name}:fallback"
            return answer

        text = completion.text.strip()
        if not text or _is_refusal(text):
            return Answer(text=HANDOVER, citations=[], grounded=False, responder=self.name)

        return Answer(
            text=text,
            citations=self._citations(text, passages),
            grounded=True,
            responder=self.name,
        )

    def _citations(self, text: str, passages: list[Retrieved]) -> list[str]:
        """What the answer cited, kept to documents that were actually retrieved.

        A citation of a document the model was never shown is dropped rather than
        reported: it is not evidence, and passing it on would put a fabricated source in
        front of the customer. The drop is still visible -- `faithfulness.evaluate` reads
        the raw text and counts it as an invalid citation.
        """
        from collection.llm.faithfulness import citations_in

        sources = [hit.document_id for hit in passages]
        cited = [name for name in citations_in(text) if name in sources]
        # The model omitted the Fonte line: credit the passages it was given, since the
        # answer could not have been built from anything else.
        return cited or list(dict.fromkeys(sources))
