# Multi-turn conversations

Until this, the responsive agent answered every message as if it were the first. That is
wrong in the only way customers actually write: *"posso parcelar?"* followed by *"e em 6
vezes?"* is one question asked in two messages, and the second retrieves nothing useful on
its own.

Two things need the thread, and they need it differently.

## Retrieval: context adds a query, it never replaces one

A follow-up is resolved behind a `QueryRewriter` seam with three arms — `none` (the
control, the old behaviour), `concat` (lexical) and `llm`.

The interface returns a **list** of queries rather than one string, and that shape is the
whole lesson of building this module.

### What the first version did, and what it cost

The obvious design replaces the message with the message joined to the previous turn.
Measured on the labelled dialogues, it **made retrieval worse**:

| rewriter | follow-up hit rate |
|---|---|
| none | 0.889 |
| concat, single query | 0.778 |

A long previous question dominates the TF-IDF vector of a four-word follow-up, so
`"e a entrada?"` — which the bare message retrieves correctly — stopped working once
context was prepended.

Weighting the follow-up did not rescue it. Repeating the message two, three and five times
all scored identically to not repeating it, because the vectoriser uses sublinear term
frequency: `tf → 1 + log(tf)`, so repetition barely moves a term's weight.

### The fix, and the second thing it got wrong

Returning several queries changes the shape of the problem rather than tuning around it.
The customer's own words are always one of the queries, so context can only *add*
candidates.

That is not sufficient on its own. Merging the union **by score** still lost
`"e a entrada?"`, because a longer query produces systematically higher cosines against
long passages — the contextual query wins every comparison and crowds out the hits the
message found. The same failure, one layer down.

Passages are therefore **interleaved by rank**: every query's best passage is taken before
any query's second. Each query is compared only against itself, so the message's top hit
is always present no matter how the context scored.

| rewriter | hit rate | follow-up hit rate |
|---|---|---|
| none | 0.900 | 0.889 |
| concat | 0.900 | 0.889 |

Context now costs nothing. On this corpus it also gains nothing, which is reported rather
than buried — see the honest reading below.

## Intent: inheriting the thread's intent, safely

`"sim"` and `"6"` carry no intent at all. Inheriting the last confident one is what makes
them answerable instead of unintelligible.

The risk is that inheritance swallows the rule that hands a customer to a person after
three messages nobody understood. A measurement decided the design:

```
'e em 6 vezes?'        margin=0.3060  best=0.4320   a confident follow-up
'sim'                  margin=0.0000  best=0.0000   a follow-up, no signal at all
'6'                    margin=0.0000  best=0.0000   a follow-up, no signal at all
'asdfgh qwerty zxcvb'  margin=0.0069  best=0.0822   gibberish, MORE signal than either
```

**Confidence cannot separate a follow-up from nonsense.** Character n-grams give gibberish
a spurious score while a bare `"sim"` scores exactly zero — the opposite of what the
obvious rule assumes. **Vocabulary can**: a follow-up reuses words the conversation or the
knowledge base already contains, and `"asdfgh"` appears in neither.

So an intent is inherited only when all three hold:

1. the classifier was **not confident** — a message that classifies on its own is never
   overridden, so a topic change is honoured;
2. the message is a **short follow-up**, so a long rambling message still escalates;
3. its words are **known**, so gibberish still escalates.

One wrinkle worth recording: the knowledge base contains no `"sim"`. It is policy and FAQ
prose, and the words a continuation is made of simply are not in it. A small closed-class
seed list (`sim`, `nao`, `ok`, `quanto`, `quando`, …) is therefore part of the vocabulary.
Those are words of agreement and reference, not domain terms, so the list is stable in a
way a list of domain vocabulary would not be.

## Faithfulness had to change too

The faithfulness measure scores an answer against the passages it was given. In a thread
that is no longer the right denominator: a reply may legitimately restate a figure
established one turn ago, from a passage *this* turn did not retrieve. Scoring against
only the current turn would report remembering correctly as a hallucination.

`faithfulness.evaluate` therefore takes `prior_passages`, and the conversation's evidence
set is the union of what it has retrieved so far. That widens what counts as supported,
which is a real cost — an answer can now quote something from four turns back the customer
has moved on from. It is the right trade, because a metric that cannot tell memory from
fabrication measures neither.

## The honest reading of the results

On this corpus, lexical context **neither helps nor hurts** once queries are merged by
rank. That is a weaker claim than "context improves retrieval", and it is the one the data
supports.

The single follow-up that still fails is `"e em 6 vezes?"`, and it is instructive. An
oracle rewrite — *"em quantas parcelas posso dividir a minha dívida?"* — **does** retrieve
the right document. So the index can find it and the lexical rewriter cannot write the
query that does. The failure belongs to query formulation, which is exactly what the LLM
arm is for, and `test_an_oracle_rewrite_recovers_the_follow_up_the_baseline_misses` pins
that conclusion in the suite.

This mirrors the retrieval finding from `docs/rag.md`, where the six failing questions were
all semantic rather than lexical. Same corpus, same limit, reached from a different
direction.

Two caveats on the numbers. The knowledge base is five documents, so a standalone query
often lands on the right one by having few wrong options — the control arm is flattered.
And ten dialogues is a small set; the follow-up column rests on nine turns.

## Running it

```bash
make answer ARGS="--conversations"               # none vs concat, no key needed
make answer ARGS="--conversations --compare"     # adds the LLM arm
```

Writes `docs/conversation-comparison.md`: the summary, and for every follow-up the query
each strategy actually produced. The per-turn queries are the reviewable part — a hit rate
says a strategy helped, the query it wrote says why.

CI runs the lexical arms, which guard the property the design rests on: adding context
must never lose a passage the customer's own words would have found.

## Where the thread comes from

The web chat channel puts the session transcript on the `message_received` event, so the
agent receives it through the orchestrator like any other payload rather than being handed
it directly. A channel without a thread simply omits it and the agent answers single-turn,
which is what every existing caller does.

The greeting is dropped on the way: nobody typed it, and resolving a follow-up against the
system's own words is not context, it is an echo.

## Known limitations

**The window is four turns.** Enough to resolve `"e em 6 vezes?"`, short enough that a
topic change two questions ago does not still colour the answer. It is a constant, not a
tuned value — there is no evaluation behind the number.

**Generation sees the thread, but the thread is not evidence.** The prompt marks it
*"apenas para entender a pergunta; não é fonte"*, because a model handed a transcript will
otherwise treat the customer's own words as a source — and a customer saying *"então são 6
parcelas?"* must not license an answer confirming it.

**The LLM arm is unmeasured.** It needs a key; the numbers above are the lexical arms only.
