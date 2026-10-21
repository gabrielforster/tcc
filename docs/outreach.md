# Outbound messages: what the proactive agent says

Until this, the proactive agent decided *whether* to contact a customer, *when*, and
*through which channel* — and then had nothing to say. `contact_scheduled` carried a
channel, a stage and an expected value, and no text. The collection rules were real and
the message was missing, which is the wrong half to be missing: the rules decide whether a
contact is lawful, the message decides whether it is.

## The brief is the only input

A composer receives a `Brief` and nothing else: the amount, the days late, the customer's
name, the attempt number, the channel, and the commercial terms that apply at that
delinquency stage. This is the outbound twin of the retrieved passages the responsive
agent answers from, and it exists for the same reason — a message may state a figure
exactly when the brief carries it, which is what makes an invented figure *detectable*
rather than merely regrettable.

The commercial terms are not written in Python. They are read from
`knowledge/tabela-de-parcelamento.csv`, the same file the retriever serves to the chat, so
the offer the system **sends** and the answer it **gives** cannot disagree. Duplicating
the table into a dict would let the two drift, and the first symptom would be a customer
quoting an outbound message back at the chat and being contradicted.

One rule is not in the table. `knowledge/limites-de-alcada.md` sets a floor of R$ 50,00 per
instalment, and that floor binds more often than the table's cap does: an old debt of
R$ 230 is allowed twelve instalments by the faixa and four by the floor. `Brief.instalments`
applies both, so the message never offers a plan the operation cannot honour.

## Two arms, and what each is for

| arm | what it is | what it is good at |
|---|---|---|
| `template` | slot filling, one template per stage and channel | compliance — it is the ceiling |
| `llm` | a model writing under the same constraints | variety — the template's weak spot |

The template arm is the control for the same reason `TemplateResponder` is: it cannot
invent a figure, because every figure is a slot, and it cannot threaten, because the
wording is fixed. It should score a perfect compliance rate. **When it does not, the
checker is wrong before the composer is** — which is exactly how the SMS finding below
surfaced.

The comparison is deliberately two-sided. Compliance alone would make the control
unbeatable and the experiment pointless, and it would also miss the real failure mode of
template collection: a customer who receives the same four sentences every fortnight stops
reading them. So the report also measures **distinctness** — one minus the mean pairwise
similarity between messages at the same stage *and* the same channel. Both halves of that
grouping are load-bearing. Messages at different stages differ because they carry
different offers, which is the template's doing; messages on different channels differ
because of the length cap. Only the spread among customers who get the same offer on the
same channel says anything about personalisation.

The template arm does not score zero distinctness (it is 0.307 over sixteen pairs) because
the slots themselves vary — names, amounts, days. The figure is a floor to beat, not a
zero.

## The compliance check, and why it lives in the agent

Five checks, each failing for a different reason:

* **Invented figures** — every number in the message must appear in the brief. No
  tolerance band: in this domain a digit is almost always a claim about somebody's debt,
  and the template arm proves the standard is satisfiable.
* **Threats and shaming** — CDC art. 42 forbids exposing the debtor to ridicule or
  subjecting them to constraint. The lexicon flags vocabulary that cannot appear in a
  compliant contact (`advogado`, `protesto`, `serasa`, `último aviso`), accent-folded so it
  matches either spelling.
* **Missing disclosures** — the creditor must be named, the message must say it is
  automated, and it must carry the opt-out. The LGPD right to object is worth nothing if
  the customer is not told it exists.
* **Over-authority offers** — a number can be legitimate as a *fact* and illegitimate as an
  *offer*. `juros de 1,5% ao mês` is in the brief; `40% de desconto` at a stage whose row
  says 20 is a promise the operation cannot honour. So discounts and instalment counts are
  parsed where they are offered and compared against the stage's own limits, independently
  of whether the digits appear anywhere in the brief.
* **Length** — per channel, because an SMS segment is 160 characters and a WhatsApp bubble
  is not.

The check runs in `ProactiveAgent`, not inside the composer, and the placement is the
point: **the composer is the thing under test, and a component that grades its own output
cannot be compared against an alternative.** A message that fails is emitted as
`escalated_to_human` with the violations in the payload, rather than being sent. A machine
that cannot write a lawful message hands the customer to someone who can.

Length is the one violation that does not block sending. A long message is bad service, not
an unlawful act, so `Compliance.sendable` is deliberately narrower than
`Compliance.compliant`: the report scores the second, the agent gates on the first.

## What the control arm failed, and what it cost

The first template library had one template per stage. Run against the labelled briefs, it
scored **18/20** — and the two failures were both SMS:

```
1-7  sms  245 chars  ['length:245>160']
60+  sms  260 chars  ['length:260>160']
```

A real finding, not a metric bug. Every full template runs to roughly 250 characters, and
an SMS segment is 160; a second segment is a second message, billed and delivered
separately, and a collection message that arrives split in two reads as broken rather than
as careful. **The message is a function of `(stage, channel)`, not of stage alone.**

What the cap takes is visible in the short templates: the greeting by name goes first,
because the three mandatory disclosures — who is writing, that it is automated, how to stop
it — are not negotiable and consume most of the budget. So on the narrow channel,
personalisation is the first thing sacrificed, and that is a legal constraint producing a
product consequence rather than a coding choice.

With the short templates the control arm is 20/20.

## Tone does not escalate with the debt

The five templates escalate in what they **offer** — a reminder, then instalments, then a
down payment — and never in how they ask. This is deliberate. A message that gets firmer as
the debt ages is how collection prose slides into pressure, and the CDC does not grade
harassment by days late. `knowledge/outreach-briefs.json` carries a fourth-attempt brief and
a 140-day brief specifically so a future change in that direction shows up as a diff in the
report rather than as a complaint.

## The results

| composer | messages | compliant | invented | over-authority | disclosures | distinctness | mean chars |
|---|---|---|---|---|---|---|---|
| template | 20 | 1.000 | 0.000 | 0.000 | 1.000 | 0.307 | 231 |

**The LLM arm is unmeasured.** It needs `ANTHROPIC_API_KEY`; the row above is the control
only, and `docs/outreach-comparison.md` is regenerated with both arms by
`make outreach ARGS="--compare"`.

What a reader should take from the control row is narrow and worth stating plainly: it says
the checker and the templates agree, not that the messages are good. No check in this
module reads tone, and a message can clear the lexicon, the number check and the cap and
still land badly. That is why the generated report prints every message in full — the rates
are the reviewable summary, the messages are the reviewable evidence.

## Running it

```bash
make outreach                             # the template arm, no key needed
make outreach ARGS="--compare"            # adds the LLM arm

uv run collection outreach --days-late 45 --amount 2300 --name "Marina" --channel sms
```

The single-message form composes and checks one brief and prints the violations, which is
the fastest way to see what the checker does to a message you wrote by hand.

CI runs the template arm. It exercises the whole outbound path — terms loaded from the
table, composition, the compliance checker, the report — and the command **exits non-zero**
when the control arm is not compliant on every brief, so a change to the instalment table,
to the templates or to the checker that breaks that agreement stops the build. The LLM arm
is not gated the same way: a model writing a bad message is a result to report, not a
broken build.

## Known limitations

**Nothing is sent.** `contact_scheduled` carries the text that *would* go out; no provider
integration exists, and `contact_sent` is still an event type with no producer. The channel
adapters that would send it are the web chat's siblings and are not written.

**The lexicon is a floor, not a certificate.** It catches vocabulary that cannot appear in a
compliant message. It cannot catch a message that is threatening in construction while
using none of those words, and nothing here measures tone.

**Attempt number is carried but unused.** The brief has it and the prompt shows it to the
model; no template varies on it, by the choice above. Whether a second attempt should
acknowledge the first is a real question this does not answer.

**`acima de 180 dias` is unreachable.** The detector's deepest bucket is `60+`, so the
table's last row — 18 instalments, 40% discount — can never be offered. Either the bucket
set grows a row or the table has one the system cannot use.
