"""Does context actually help? Measuring the rewriters against each other.

`rag/evaluate.py` measures retrieval on standalone questions. `llm/evaluate.py` measures
what generation does with it. Neither can see the failure this module exists for: a
follow-up like "e em 6 vezes?" retrieves nothing useful on its own, and a single-turn
evaluation never asks it, because every question in that set stands alone.

The comparison holds everything fixed but the rewriter -- same index, same classifier,
same responder -- across three arms:

* **none**, the control, which is what the agent did before multi-turn existed;
* **concat**, the lexical baseline;
* **llm**, which needs a key.

Reported separately for **follow-up turns** and **all turns**, and the split is the point.
A rewriter can only help where context is needed, so averaging over every turn dilutes the
effect until it looks like noise. The follow-up column is the result; the all-turns column
is the check that the rewriter did not break the questions that were already working.

`inherited` counts turns whose intent came from the thread rather than the message -- the
"sim" and "6" cases that would otherwise march a customer towards a handover for answering
a question we asked.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from collection.agents.conversation import Conversation, QueryRewriter, get_rewriter
from collection.agents.responsive import Responder, ResponsiveAgent
from collection.rag.index import VectorIndex


@dataclass(frozen=True)
class DialogueTurn:
    customer: str
    relevant: frozenset[str]
    follow_up: bool = False


@dataclass(frozen=True)
class Dialogue:
    id: str
    turns: list[DialogueTurn]
    note: str = ""


def load_dialogues(path: Path | str) -> list[Dialogue]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        Dialogue(
            id=item["id"],
            note=item.get("note", ""),
            turns=[
                DialogueTurn(
                    customer=turn["customer"],
                    relevant=frozenset(turn["relevant"]),
                    follow_up=bool(turn.get("follow_up", False)),
                )
                for turn in item["turns"]
            ],
        )
        for item in payload
    ]


@dataclass
class TurnRecord:
    dialogue: str
    position: int
    message: str
    query: str
    follow_up: bool
    retrieved: list[str]
    relevant: frozenset[str]
    intent: str
    inherited: bool
    escalated: bool

    @property
    def hit(self) -> bool:
        """Did retrieval return any document the labels call relevant?"""
        return bool(set(self.retrieved) & set(self.relevant))

    @property
    def rewritten(self) -> bool:
        return self.query.strip() != self.message.strip()


@dataclass
class RewriterScores:
    rewriter: str
    turns: int
    follow_ups: int
    hit_rate: float
    follow_up_hit_rate: float
    inherited: int
    escalations: int
    rewritten: int
    records: list[TurnRecord] = field(default_factory=list)

    def as_row(self) -> dict[str, object]:
        return {
            "rewriter": self.rewriter,
            "turns": self.turns,
            "follow_ups": self.follow_ups,
            "hit_rate": round(self.hit_rate, 4),
            "follow_up_hit_rate": round(self.follow_up_hit_rate, 4),
            "intents_inherited": self.inherited,
            "escalations": self.escalations,
            "rewritten": self.rewritten,
        }


def run(
    index: VectorIndex,
    dialogues: list[Dialogue],
    rewriter: QueryRewriter | str = "concat",
    responder: Responder | None = None,
) -> RewriterScores:
    """Replay every dialogue turn by turn, feeding each turn the thread before it."""
    if isinstance(rewriter, str):
        rewriter = get_rewriter(rewriter)
    agent = ResponsiveAgent(index=index, responder=responder, rewriter=rewriter)

    records: list[TurnRecord] = []
    for dialogue in dialogues:
        conversation = Conversation()
        # A fresh customer id per dialogue: the agent counts unclear messages per
        # customer, and sharing one would leak escalation pressure between dialogues.
        customer_id = f"eval_{dialogue.id}"
        for position, turn in enumerate(dialogue.turns):
            reply = agent.answer(turn.customer, customer_id=customer_id, history=conversation)
            records.append(
                TurnRecord(
                    dialogue=dialogue.id,
                    position=position,
                    message=turn.customer,
                    query=reply.query,
                    follow_up=turn.follow_up,
                    retrieved=[hit.document_id for hit in reply.passages],
                    relevant=turn.relevant,
                    intent=reply.classification.intent.value,
                    inherited=reply.classification.inherited,
                    escalated=reply.escalated,
                )
            )
            conversation.add("customer", turn.customer, reply.classification.intent.value)
            conversation.add("agent", reply.answer.text)

    follow_ups = [record for record in records if record.follow_up]
    return RewriterScores(
        rewriter=getattr(rewriter, "name", str(rewriter)),
        turns=len(records),
        follow_ups=len(follow_ups),
        hit_rate=sum(1 for r in records if r.hit) / len(records) if records else 0.0,
        follow_up_hit_rate=(
            sum(1 for r in follow_ups if r.hit) / len(follow_ups) if follow_ups else 0.0
        ),
        inherited=sum(1 for r in records if r.inherited),
        escalations=sum(1 for r in records if r.escalated),
        rewritten=sum(1 for r in records if r.rewritten),
        records=records,
    )


def compare(
    index: VectorIndex,
    dialogues: list[Dialogue],
    rewriters: list[QueryRewriter | str],
    responder: Responder | None = None,
) -> tuple[pd.DataFrame, list[RewriterScores]]:
    results = [run(index, dialogues, rewriter, responder) for rewriter in rewriters]
    return pd.DataFrame([result.as_row() for result in results]), results


def write_report(table: pd.DataFrame, results: list[RewriterScores], path) -> str:
    """Write the comparison and, for every follow-up, the query each rewriter produced.

    The per-turn queries are the reviewable part. A hit rate says a rewriter helped; the
    query it wrote says why, and a rewrite that drags the previous topic into an unrelated
    question is visible here and invisible in the table.
    """
    path = Path(path)
    lines = [
        "# Multi-turn: does context help retrieval?",
        "",
        "Same index, same classifier, same responder. The only variable is how a follow-up",
        "is resolved into a query. Generated by `collection answer --conversations`.",
        "",
        "## Summary",
        "",
        table.to_markdown(index=False),
        "",
        "`follow_up_hit_rate` is the result. `hit_rate` over all turns is the check that",
        "nothing that already worked got worse.",
        "",
    ]

    baseline = next((r for r in results if r.rewriter == "none"), None)
    for result in results:
        lines += [f"## {result.rewriter}", ""]
        if baseline is not None and result is not baseline:
            delta = result.follow_up_hit_rate - baseline.follow_up_hit_rate
            lines += [
                f"Follow-up hit rate {result.follow_up_hit_rate:.0%} "
                f"({delta:+.0%} against no context).",
                "",
            ]
        lines += [
            "### Follow-up turns",
            "",
            "| dialogue | message | query used | hit |",
            "|---|---|---|---|",
        ]
        for record in result.records:
            if not record.follow_up:
                continue
            query = record.query if record.rewritten else "_(unchanged)_"
            lines.append(
                f"| {record.dialogue} | {record.message} | {query} | "
                f"{'yes' if record.hit else 'no'} |"
            )
        lines.append("")

        missed = [r for r in result.records if r.follow_up and not r.hit]
        if missed:
            lines += ["### Follow-ups still missed", ""]
            for record in missed:
                lines.append(
                    f'- **{record.dialogue}** — "{record.message}" retrieved '
                    f"{record.retrieved or 'nothing'}, wanted {sorted(record.relevant)}"
                )
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)
