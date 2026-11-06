# The LLM responder and how it is held to the evidence

The responsive agent already classified intents, retrieved passages and escalated what it
should not answer. What it could not do was *write*: `TemplateResponder` quotes the
retrieved chunk back at the customer, which is honest and unreadable. This module adds
generation, and — more importantly — the measurement that makes generation reportable.

## Why the seam, and not a rewrite

`ResponsiveAgent` takes a `Responder`. The LLM is a second implementation of that one
method, so retrieval, intent classification, escalation, the opt-out path and the unclear
counter are identical between the two arms. A difference in the results is therefore
attributable to generation and nothing else.

That is the whole reason the template responder stays in the codebase after the LLM
arrives. It is not dead code and not a fallback of convenience: it is the control arm.

```
message → IntentClassifier → escalation check → VectorIndex.search → Responder → Answer
                                                                     ├── TemplateResponder
                                                                     └── LLMResponder
```

## The prompt

`prompt.py` holds the system prompt. Three things in it are load-bearing:

**The model sees only what was retrieved.** The context block is built from the passages
and nothing else. Handing the model the whole knowledge base would make grounding
unmeasurable — any sentence could be justified by something it was never shown.

**`NAO_SEI` is an exit.** A literal token, not a phrase, because detecting "the model
declined" by matching prose eventually matches a real answer. Emitting it marks the answer
ungrounded, and the agent routes ungrounded answers to a person. A system that can only
answer will answer anything.

**The legal prohibitions are stated, not assumed.** CDC art. 42 forbids exposing the
debtor to ridicule or constraint; the approval limits in `knowledge/limites-de-alcada.md`
cap what a discount may be. A model that threatens or invents a discount creates real
liability, so the prompt forbids both explicitly and everything outside the passages goes
to a human.

## Failure is visible, never silent

| What happens | What the customer gets | What the record shows |
|---|---|---|
| Nothing retrieved | Handover | `grounded=False`, model never called |
| Model emits `NAO_SEI` | Handover | `grounded=False`, escalated |
| No key, timeout, rate limit | The templated answer | `responder="llm:fallback"` |
| Model cites an unretrieved file | Citation dropped | counted as an invalid citation |

The fallback tag is the one that matters for the thesis. Without it an evaluation run with
an expired key would report the *template's* numbers under the LLM's name, and the result
would look like a tie.

## Measuring faithfulness

`faithfulness.py` scores an answer against the exact passages the model was given. Three
checks, failing for different reasons:

**Numbers.** Every figure in the answer must appear in the passages. This is the sharpest
check in this domain — an invented interest rate or instalment count is the failure that
costs money — and unlike prose it admits an exact comparison. Figures are normalised
first, so `1.234,56` and `1234.56` are one number and not a false positive created by the
Brazilian thousands separator.

**Sentences.** Each claim is scored by its best similarity against the passages, split into
sentence-sized pieces so a short quote is not punished for a long chunk's length. A
verbatim quote short-circuits to full support. This check is *weak evidence of support and
strong evidence of its absence*: a sentence close to a passage may still paraphrase it
wrongly, but a sentence close to nothing is grounded in nothing.

**Citations.** A cited document must be one that was retrieved.

Two kinds of text are excluded from scoring, and the exclusions are arm-neutral by design:
the citation line (`Fonte: faq.md` is evidence, not a claim) and clauses ending in a colon
(`Sobre o parcelamento:` announces what follows and asserts nothing). Both arms write
boilerplate; scoring it would report a hallucination rate that mostly tracks who writes
more of it. Neither exclusion can hide an invented figure, because numbers are checked
across the whole answer independently of which sentences are scored.

### A finding from building the measure

The first version scored each sentence against whole retrieved chunks and reported the
template responder — which quotes verbatim and *cannot* invent anything — as unfaithful on
nearly every question. Two distinct bugs, both worth recording because both would have
been invisible in the final numbers:

1. **Length mismatch.** Cosine between eight words and three hundred is small even when
   the eight are an exact quote of the three hundred. Fixed by splitting passages into
   sentence-sized units and short-circuiting verbatim quotes.
2. **Boilerplate scored as claims.** The lead-in lines were being counted as unsupported
   assertions, which would have made the measure a style detector.

A faithfulness metric that fails on a responder incapable of hallucinating is measuring
something other than faithfulness. The template arm is what caught it — a second reason to
keep the control.

## Running it

```bash
make answer                        # template arm, no key needed
make answer ARGS="--compare"       # both arms; needs a provider (below)
uv run collection answer --message "posso parcelar em 6 vezes?" --responder llm

# the LLM arm with no account: a local model through Ollama
make ollama
LLM_PROVIDER=openai LLM_MODEL=qwen2.5:3b make answer ARGS="--compare"
```

`--compare` writes `docs/responder-comparison.md`: the summary table, every unsupported
claim it found, and the full text of every answer. The answers are included deliberately —
a table of rates is not reviewable, and the point of reading the report is to see the
sentence the system would have sent to a customer.

CI runs the template arm only. The LLM arm costs money per run, and a CI step that calls a
paid API on every pull request is a step that gets deleted the first week it is slow.

## Configuration

| Setting | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic`, or `openai` for any OpenAI-compatible server |
| `LLM_MODEL` | `claude-sonnet-5-5` | `qwen2.5:3b` for the local model |
| `ANTHROPIC_API_KEY` | empty | From the environment or `.env`; never committed |
| `LLM_BASE_URL` | `http://localhost:11434/v1` | The `openai` provider's server; the default is `make ollama` |
| `LLM_API_KEY` | empty | Sent as a bearer token only when set; a local server needs none |
| `LLM_TIMEOUT` | `180` | Seconds, `openai` only: a CPU model is slow, and a cut-off reply would be scored as the template's |

Temperature is fixed at zero. The same question must not produce a different instalment
plan on a second reading, and an evaluation has to be reproducible to be reportable.
`max_tokens` is 512: a collection reply that runs past a few sentences is already a bad
reply, and the budget also caps the cost per contact, which is one of the operational
indicators the final evaluation reports.

## The first measured LLM arm: a 3B model on a CPU

Measured 2026-11-06 against `qwen2.5:3b` served by Ollama on this machine (CPU only, no
GPU), because no hosted key was available. It is a floor, not the system's LLM: a
three-billion-parameter model, chosen because it runs with no account.
The hosted arm is the same command with a different `LLM_PROVIDER`.

| responder | grounded | faithful | citation | p50 | p95 |
|---|---|---|---|---|---|
| template | 0.875 | 1.000 | 0.857 | 2 ms | 2 ms |
| qwen2.5:3b | 0.813 | 0.692 | 0.846 | 4.5 s | 11.1 s |

**The run found a defect before it found a number.** The prompt says to write `NAO_SEI`
"and nothing else". Asked whether it could call on a Sunday, the model wrote
`NAO_SEI Fonte: (politica-de-cobranca.md)`: the refusal plus the citation line the same
prompt asks every answer to end with. `_is_refusal` matched the token only as the whole
reply, so this scored as an *answer*, and the customer would have read the raw token. It
now matches the token anywhere; the case is pinned in the refusal test. A hosted model may
never have produced that shape, which is the argument for running a weak model at all.

**Of the four answers flagged unfaithful, two are real and two are the measure.**

- *Real, and the dangerous kind:* asked about a debt 45 days late, it offered "até 12
  vezes". Twelve instalments is the 60+ day tier. An invented figure in a commercial term
  is the failure this whole comparison exists to catch, and the number check caught it.
- *Real, but harmless:* asked "posso parcelar minha divida?", it echoed the question back
  with `Fonte: [1]`. It is scored grounded because it does not refuse, which is a gap in
  what "grounded" means rather than a hallucination.
- *The measure:* the Pix and opt-out answers paraphrase their passage faithfully and lose
  on sentence support, the same cosine-against-a-long-chunk effect described above. They
  are counted as written; the strictness is the conservative side to err on.

**Latency is over the 5 s budget by a factor of two at p95**, and that is CPU inference,
not the design. It is the number that rules a local 3B model out of the live chat path,
and it is reported rather than tuned away.

## What this does not yet answer

The comparison measures faithfulness, citation accuracy and latency. It does not measure
whether a faithful answer is a *useful* one — an answer can quote the right passage and
still miss what the customer asked. That needs human judgement over a sample, and it is
the gap between this report and the answer-quality numbers the schedule asks for on 19/11.
