"""Comparing responders on the same questions, with the same retrieval.

`rag/evaluate.py` measures whether the right passage was found. This measures what was
done with it. Holding retrieval fixed is the whole design: both arms run through the same
`ResponsiveAgent`, the same index and the same intent classifier, so a difference in the
numbers below is attributable to generation and nothing else.

What is reported, and why each one is here:

* **grounded rate** -- share of questions answered instead of handed to a person. On its
  own it is not a quality measure: a system that answers everything scores 1.0 and may be
  wrong every time. It is read against faithfulness.
* **faithful rate** -- share of *answered* questions with no invented figure and no
  unsupported sentence. This is the hallucination rate, inverted, and it is the number the
  template arm exists to anchor: a responder that only quotes cannot hallucinate, so it
  should score 1.0, and any LLM result is read as a loss against that ceiling.
* **citation accuracy** -- share of answers citing at least one document the gold labels
  mark relevant. Retrieval can hand over the right passage and generation can still cite
  the wrong file.
* **latency** -- p50 and p95 in milliseconds. The schedule sets a 5 s budget for a text
  reply; p95 is what that budget should be checked against, not the mean.

The template arm needs no API key and no network, so the comparison always has one side.
"""

import statistics
from dataclasses import dataclass, field

import pandas as pd

from collection.agents.responsive import Responder, ResponsiveAgent, get_responder
from collection.llm import faithfulness as faith
from collection.rag.evaluate import Question
from collection.rag.index import VectorIndex

#: A text reply the customer waits for, per the schedule. Checked against p95.
LATENCY_BUDGET_MS = 5000.0


@dataclass
class AnswerRecord:
    question: str
    responder: str
    answer: str
    grounded: bool
    escalated: bool
    latency_ms: float
    cited: list[str]
    relevant: frozenset[str]
    scores: faith.Faithfulness | None = None

    @property
    def citation_correct(self) -> bool:
        return bool(set(self.cited) & set(self.relevant))

    def as_row(self) -> dict[str, object]:
        row: dict[str, object] = {
            "question": self.question,
            "responder": self.responder,
            "grounded": self.grounded,
            "latency_ms": round(self.latency_ms, 1),
            "cited": ", ".join(self.cited),
            "citation_correct": self.citation_correct,
        }
        if self.scores is not None:
            row.update(self.scores.as_row())
        return row


@dataclass
class ResponderScores:
    responder: str
    questions: int
    grounded_rate: float
    faithful_rate: float
    citation_accuracy: float
    mean_sentence_support: float
    latency_p50: float
    latency_p95: float
    fallbacks: int = 0
    records: list[AnswerRecord] = field(default_factory=list)

    @property
    def within_budget(self) -> bool:
        return self.latency_p95 <= LATENCY_BUDGET_MS

    def as_row(self) -> dict[str, object]:
        return {
            "responder": self.responder,
            "questions": self.questions,
            "grounded_rate": round(self.grounded_rate, 4),
            "faithful_rate": round(self.faithful_rate, 4),
            "citation_accuracy": round(self.citation_accuracy, 4),
            "sentence_support": round(self.mean_sentence_support, 4),
            "latency_p50_ms": round(self.latency_p50, 1),
            "latency_p95_ms": round(self.latency_p95, 1),
            "fallbacks": self.fallbacks,
        }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    # Nearest-rank: with a question set this small, interpolation would invent a latency
    # that was never observed.
    rank = max(1, min(len(ordered), round(fraction * len(ordered))))
    return ordered[rank - 1]


def run(
    index: VectorIndex,
    questions: list[Question],
    responder: Responder | str = "template",
    customer_id: str = "eval",
) -> ResponderScores:
    """Answer every question with one responder and score the results.

    Each question gets a fresh customer id. The agent counts consecutive unclear messages
    per customer and hands over after three, so evaluating a whole question set under one
    id would start escalating partway through for a reason that has nothing to do with the
    responder under test.
    """
    if isinstance(responder, str):
        responder = get_responder(responder)
    agent = ResponsiveAgent(index=index, responder=responder)

    records: list[AnswerRecord] = []
    for position, item in enumerate(questions):
        reply = agent.answer(item.question, customer_id=f"{customer_id}_{position}")
        scores = faith.evaluate(reply.answer.text, reply.passages) if not reply.escalated else None
        records.append(
            AnswerRecord(
                question=item.question,
                responder=reply.answer.responder,
                answer=reply.answer.text,
                grounded=reply.answer.grounded,
                escalated=reply.escalated,
                latency_ms=reply.latency_ms,
                cited=reply.answer.citations,
                relevant=item.relevant,
                scores=scores,
            )
        )

    answered = [record for record in records if not record.escalated]
    latencies = [record.latency_ms for record in records]
    supports = [r.scores.sentence_support for r in answered if r.scores is not None]

    return ResponderScores(
        responder=getattr(responder, "name", str(responder)),
        questions=len(records),
        grounded_rate=len(answered) / len(records) if records else 0.0,
        # Over answered questions only: a handover is a correct outcome, not an
        # unfaithful answer, and counting it as either would distort the rate.
        faithful_rate=(
            sum(1 for r in answered if r.scores and r.scores.faithful) / len(answered)
            if answered
            else 0.0
        ),
        citation_accuracy=(
            sum(1 for r in answered if r.citation_correct) / len(answered) if answered else 0.0
        ),
        mean_sentence_support=statistics.fmean(supports) if supports else 0.0,
        latency_p50=_percentile(latencies, 0.50),
        latency_p95=_percentile(latencies, 0.95),
        fallbacks=sum(1 for record in records if record.responder.endswith(":fallback")),
        records=records,
    )


def compare(
    index: VectorIndex,
    questions: list[Question],
    responders: list[Responder | str],
) -> tuple[pd.DataFrame, list[ResponderScores]]:
    results = [run(index, questions, responder) for responder in responders]
    return pd.DataFrame([result.as_row() for result in results]), results


def _provenance(retriever) -> str:
    """What these figures were measured against, read off the object that produced them.

    The figures are only readable against the retrieval behind them, and a retrieval that
    has since changed is exactly the sort of caveat nobody remembers to add. So the stamp
    is generated. It is derived from the retriever rather than asserted as a literal,
    because a hardcoded "TF-IDF" would survive a move of this harness onto the serving
    retriever and restamp, with a fresh date, a claim that had become false -- which is the
    failure the stamp exists to prevent, inverted.
    """
    from datetime import date

    from collection.rag.index import VectorIndex

    measured = date.today().isoformat()
    if isinstance(retriever, VectorIndex):
        return (
            f"> **Measured {measured} against the in-memory {retriever.embedder.name} "
            "index**, not against the pgvector retriever that serves a real question. This "
            "harness loads `knowledge/` straight off disk with no tenant, so it ranks a "
            "corpus the serving path does not: internal documents are withheld there and "
            "visible here, and a CSV is a document per row here and one versioned file "
            "there. The grounding and citation rates below therefore describe the "
            "responders, not the deployed answer path. The retrieval comparison between "
            "the two is in `docs/rag.md`."
        )
    return (
        f"> **Measured {measured} against `{type(retriever).__name__}`**, which is the "
        "retrieval the serving path uses. The comparison between the available retrievers "
        "is in `docs/rag.md`."
    )


def write_report(table: pd.DataFrame, results: list[ResponderScores], path, retriever=None) -> str:
    """Write the comparison, the per-question detail and the answers themselves.

    The answers are included in full. A table of rates is not reviewable -- the point of
    reading the report is to see the sentence the system would have sent to a customer.
    """
    from pathlib import Path

    path = Path(path)
    lines = [
        "# Responder comparison: template vs LLM",
        "",
        "Same index, same retrieval, same intent classifier. The only variable is how the",
        "answer is written. Generated by `make answer` (template only) or",
        '`make answer ARGS="--compare"` (both arms).',
        "",
        *([_provenance(retriever), ""] if retriever is not None else []),
        "## Summary",
        "",
        table.to_markdown(index=False),
        "",
        f"Latency budget: {LATENCY_BUDGET_MS:.0f} ms at p95.",
        "",
    ]
    if len(results) < 2:
        lines += [
            f"> Only the `{results[0].responder}` arm was run, so this is a baseline rather "
            "than a comparison. The LLM arm needs `ANTHROPIC_API_KEY`: "
            '`make answer ARGS="--compare"`.',
            "",
        ]

    for result in results:
        lines += [
            f"## {result.responder}",
            "",
            f"- grounded {result.grounded_rate:.0%} of {result.questions} questions",
            f"- faithful {result.faithful_rate:.0%} of the answered ones",
            f"- citation accuracy {result.citation_accuracy:.0%}",
            f"- p95 latency {result.latency_p95:.0f} ms "
            f"({'within' if result.within_budget else 'over'} budget)",
            "",
        ]
        if result.fallbacks:
            lines += [
                f"> {result.fallbacks} answer(s) fell back to the template responder after a "
                "generation failure; their rows are the template's behaviour, not this "
                "responder's.",
                "",
            ]

        unfaithful = [
            record
            for record in result.records
            if record.scores is not None and not record.scores.faithful
        ]
        if unfaithful:
            lines += ["### Unsupported claims", ""]
            for record in unfaithful:
                detail = []
                if record.scores.unsupported_numbers:
                    detail.append(f"numbers {record.scores.unsupported_numbers}")
                if record.scores.unsupported_sentences:
                    detail.append(f"{len(record.scores.unsupported_sentences)} sentence(s)")
                lines.append(f"- **{record.question}** — {'; '.join(detail)}")
            lines.append("")

        lines += ["### Answers", ""]
        for record in result.records:
            status = "escalated" if record.escalated else "answered"
            lines += [
                f"**{record.question}** _({status}, {record.latency_ms:.0f} ms)_",
                "",
                "> " + record.answer.replace("\n", "\n> "),
                "",
            ]

    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)
