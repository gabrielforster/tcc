# Responsive agent

Answers an inbound customer message, grounded in the retrieved knowledge base.

## Flow

1. **Classify** — intent plus a confidence margin.
2. **Decide whether to escalate** — *before* answering.
3. **Retrieve** — the passages bearing on the question.
4. **Compose** — an answer from those passages, citing them.

Escalation is checked before answering because the cases that need a person — a disputed
debt, a message nobody understood — are exactly where a confident automated answer does the
most damage.

## Intents

The five the schedule names, plus two that the channel forces on you:

| Intent | Example |
|---|---|
| `debt_inquiry` | "quanto eu to devendo?" |
| `second_copy` | "me manda a segunda via" |
| `negotiation` | "consigo parcelar em 6 vezes?" |
| `dispute` | "eu nunca contratei esse servico" |
| `payment_claim` | "ja paguei semana passada" |
| `opt_out` | "parem de me mandar mensagem" |
| `small_talk` | "bom dia, tudo bem?" |

`small_talk` was not planned. Without it, *"bom dia, tudo bem com você?"* classified as
**negotiation**, because the cue *"não tenho como pagar tudo agora"* shares the word "tudo"
and a good deal of character n-gram overlap. Greetings are a real category in any customer
channel, so the fix is an intent rather than a special case.

`unknown` is a routing decision, not a failure: an unclassified message goes to a person
instead of getting a confident wrong answer.

### Confidence is a margin, not a probability

It is the gap between the best and second-best intent score — the right shape for the
decision being made ("is this *this* intent rather than that one"), and it must not be
reported as a calibrated probability, because it is not one.

## The classifier is a baseline, on purpose

It is lexical: cue phrases scored with the same character n-gram similarity as retrieval.
Two reasons rather than one:

- the agent runs end to end with **no API key**, so the pipeline is testable and reviewable
  before any model is wired in;
- it gives the LLM classifier something to be measured **against**. "The LLM classified
  91%" is not a result; "91% against a lexical baseline at 91.3% on the same messages" is —
  and if the LLM cannot beat this, that is worth knowing before it goes into the paper.

Measured on 23 labelled messages (`knowledge/messages.json`):

| metric | value |
|---|---|
| accuracy | **0.913** |
| dispute · opt_out · payment_claim · second_copy | 1.00 |
| debt_inquiry | 0.75 |
| negotiation | 0.75 |

## Escalation rules

| Trigger | Reason |
|---|---|
| Intent is `dispute` | Always a human — per `knowledge/limites-de-alcada.md` |
| Nothing relevant retrieved | `no_grounding` — a guess is worse than a handover |
| Three unclear messages in a row | `unclear_repeatedly` |
| Empty message | `empty_message` |

The unclear counter resets only when a message **is** understood. Resetting it
unconditionally cleared the count an unclear message had just incremented, so the rule
could never fire — a bug the tests caught.

## Generation is behind a seam

`TemplateResponder` quotes what was retrieved and cites the source. It **cannot
hallucinate, because it does not generate** — it can only answer awkwardly, which is
exactly the trade an LLM comparison will quantify.

An LLM responder implements the same `Responder` interface. Retrieval stays identical
across the two, so any measured difference is attributable to generation alone — which is
what makes the comparison worth reporting.

## Latency

Measured on every reply and carried in the `reply_sent` event. The lexical path answers in
roughly 2 ms; the schedule's 5-second target applies to the full LLM path, and this number
is the floor it will be measured against, not evidence the target is met.
