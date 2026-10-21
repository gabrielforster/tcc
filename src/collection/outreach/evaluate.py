"""Comparing composers on the same briefs, and writing the report.

Same shape as `llm/evaluate.py`: both arms see the identical briefs, so a difference in
the numbers is attributable to how the message was written and to nothing else.

What is reported, and why each one is here:

* **compliance rate** -- share of messages with no violation at all. The template arm is
  the ceiling: slot filling cannot invent a figure or choose a threatening word, so it
  should score 1.0, and an LLM result is read as a loss against that. When the control
  scores below 1.0 the checker is suspect before the composer is -- which is how the SMS
  length finding in `compose.py` surfaced.
* **invented figures** -- messages stating a number the brief does not carry. The single
  most expensive failure in outbound: a wrong amount is a wrong debt.
* **over-authority offers** -- discounts and instalment counts beyond what the stage's
  table row permits. An offer the operation cannot honour is worse than no offer.
* **distinctness** -- how much the messages differ *within a stage*. This is the metric
  the template arm is expected to lose, and it is here so the comparison is two-sided: a
  perfectly compliant system that sends one identical paragraph to every customer is a
  system customers stop reading. It is 1 minus the mean pairwise cosine between messages
  at the same stage, over the same TF-IDF representation the retriever uses. Both arms
  carry the same mandatory disclosures, which drags both scores down by roughly the same
  amount, so the figure is comparable between arms and not meaningful on its own.
* **length** -- mean characters and how many messages exceed their channel's cap.
* **latency** -- p50 and p95. Outbound is batch work, so the budget is looser than the
  responsive one; the figure is here for cost, not for service level.
"""

import itertools
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from collection.outreach.brief import Brief, load_briefs
from collection.outreach.compliance import Compliance, check
from collection.outreach.compose import Composer, OutboundMessage, get_composer
from collection.rag.embeddings import TfidfEmbedder


@dataclass
class MessageRecord:
    brief_id: str
    stage: str
    channel: str
    composer: str
    text: str
    compliance: Compliance
    latency_ms: float

    def as_row(self) -> dict[str, object]:
        row: dict[str, object] = {
            "customer": self.brief_id,
            "stage": self.stage,
            "channel": self.channel,
            "composer": self.composer,
        }
        row.update(self.compliance.as_row())
        row["violations"] = "; ".join(self.compliance.violations)
        return row


@dataclass
class ComposerScores:
    composer: str
    messages: int
    compliance_rate: float
    sendable_rate: float
    invented_rate: float
    over_authority_rate: float
    disclosure_rate: float
    distinctness: float
    distinct_pairs: int
    mean_chars: float
    over_cap: int
    latency_p50: float
    latency_p95: float
    fallbacks: int = 0
    records: list[MessageRecord] = field(default_factory=list)

    def as_row(self) -> dict[str, object]:
        return {
            "composer": self.composer,
            "messages": self.messages,
            "compliant": round(self.compliance_rate, 3),
            "sendable": round(self.sendable_rate, 3),
            "invented_figures": round(self.invented_rate, 3),
            "over_authority": round(self.over_authority_rate, 3),
            "disclosures": round(self.disclosure_rate, 3),
            "distinctness": round(self.distinctness, 3),
            "pairs": self.distinct_pairs,
            "mean_chars": round(self.mean_chars, 1),
            "over_cap": self.over_cap,
            "p95_ms": round(self.latency_p95, 1),
            "fallbacks": self.fallbacks,
        }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), round(fraction * len(ordered))))
    return ordered[rank - 1]


def distinctness(records: list[MessageRecord]) -> tuple[float, int]:
    """1 minus the mean pairwise cosine between messages at the same stage and channel.

    Both halves of the grouping are load-bearing. Messages at different *stages* differ
    because they carry different offers, which is the template's doing and not the
    composer's. Messages on different *channels* differ because an SMS segment is 160
    characters and a WhatsApp bubble is not, which is the cap's doing. Only the variation
    among customers who get the same offer on the same channel says anything about
    personalisation.

    Returns the score and the number of pairs it rests on, because with a brief set this
    size the second number decides how much the first is worth.
    """
    texts = [record.text for record in records]
    if len(texts) < 2:
        return 0.0, 0
    embedder = TfidfEmbedder().fit(texts)
    vectors = embedder.encode(texts)
    groups: dict[tuple[str, str], list[int]] = {}
    for position, record in enumerate(records):
        groups.setdefault((record.stage, record.channel), []).append(position)

    similarities: list[float] = []
    for positions in groups.values():
        for left, right in itertools.combinations(positions, 2):
            similarities.append(float(np.dot(vectors[left], vectors[right])))
    if not similarities:
        return 0.0, 0
    return 1.0 - statistics.fmean(similarities), len(similarities)


def run(
    briefs: list[Brief],
    composer: Composer | str = "template",
) -> ComposerScores:
    """Write a message for every brief with one composer and score the results."""
    import time

    if isinstance(composer, str):
        composer = get_composer(composer)

    records: list[MessageRecord] = []
    for brief in briefs:
        started = time.perf_counter()
        message: OutboundMessage = composer.compose(brief)
        elapsed = (time.perf_counter() - started) * 1000
        records.append(
            MessageRecord(
                brief_id=brief.customer_id,
                stage=brief.stage,
                channel=brief.channel,
                composer=message.composer,
                text=message.text,
                compliance=check(message, brief),
                latency_ms=message.latency_ms or elapsed,
            )
        )

    total = len(records) or 1
    spread, pairs = distinctness(records)
    latencies = [record.latency_ms for record in records]
    return ComposerScores(
        composer=getattr(composer, "name", str(composer)),
        messages=len(records),
        compliance_rate=sum(1 for r in records if r.compliance.compliant) / total,
        sendable_rate=sum(1 for r in records if r.compliance.sendable) / total,
        invented_rate=sum(1 for r in records if r.compliance.invented_numbers) / total,
        over_authority_rate=sum(1 for r in records if r.compliance.over_authority) / total,
        disclosure_rate=sum(1 for r in records if not r.compliance.missing_disclosures) / total,
        distinctness=spread,
        distinct_pairs=pairs,
        mean_chars=statistics.fmean(len(r.text) for r in records) if records else 0.0,
        over_cap=sum(1 for r in records if r.compliance.too_long),
        latency_p50=_percentile(latencies, 0.50),
        latency_p95=_percentile(latencies, 0.95),
        fallbacks=sum(1 for r in records if r.composer.endswith(":fallback")),
        records=records,
    )


def compare(
    briefs: list[Brief],
    composers: list[Composer | str],
) -> tuple[pd.DataFrame, list[ComposerScores]]:
    results = [run(briefs, composer) for composer in composers]
    return pd.DataFrame([result.as_row() for result in results]), results


def write_report(table: pd.DataFrame, results: list[ComposerScores], path: Path | str) -> str:
    """Write the comparison, then every message in full.

    The messages are the reviewable part. A compliance rate says a message passed a
    lexicon and a number check; only reading it says whether a person would want to
    receive it, and no automated check in this module claims to answer that.
    """
    path = Path(path)
    lines = [
        "# Outbound composer comparison: template vs LLM",
        "",
        "Same briefs, same commercial terms, same compliance checker. The only variable is",
        "how the message is written. Generated by `make outreach` (template only) or",
        '`make outreach ARGS="--compare"` (both arms).',
        "",
        "## Summary",
        "",
        table.to_markdown(index=False),
        "",
    ]
    if len(results) < 2:
        lines += [
            f"> Only the `{results[0].composer}` arm was run, so this is a baseline rather "
            "than a comparison. The LLM arm needs `ANTHROPIC_API_KEY`: "
            '`make outreach ARGS="--compare"`.',
            "",
        ]

    for result in results:
        lines += [
            f"## {result.composer}",
            "",
            f"- compliant {result.compliance_rate:.0%} of {result.messages} messages",
            f"- stated an invented figure in {result.invented_rate:.0%}",
            f"- offered beyond its authority in {result.over_authority_rate:.0%}",
            f"- carried every mandatory disclosure in {result.disclosure_rate:.0%}",
            f"- distinctness {result.distinctness:.3f} over "
            f"{result.distinct_pairs} same-stage, same-channel pairs",
            f"- {result.mean_chars:.0f} characters on average, {result.over_cap} over cap",
            f"- p95 latency {result.latency_p95:.0f} ms",
            "",
        ]
        if result.fallbacks:
            lines += [
                f"> {result.fallbacks} message(s) fell back to the template composer after a "
                "generation failure; their rows are the template's behaviour, not this "
                "composer's.",
                "",
            ]
        lines += ["### Messages", ""]
        for record in result.records:
            flag = "ok" if record.compliance.compliant else "; ".join(record.compliance.violations)
            lines += [
                f"**{record.brief_id}** · {record.stage} · {record.channel} · "
                f"{record.compliance.length} chars · {flag}",
                "",
                f"> {record.text}",
                "",
            ]

    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def load(path: Path | str | None = None) -> list[Brief]:
    return load_briefs(Path(path) if path else None)
