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

from collection.agents.conversation import (
    CONTEXT_WINDOW,
    Conversation,
    QueryRewriter,
    get_rewriter,
)
from collection.agents.responsive import Responder, ResponsiveAgent
from collection.rag.index import VectorIndex


@dataclass(frozen=True)
class DialogueTurn:
    customer: str
    relevant: frozenset[str]
    follow_up: bool = False
    #: The intent a human reader assigns the turn *in context*. For "sim" that is only
    #: answerable by reading the thread, which is what makes it worth labelling.
    intent: str | None = None


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
                    intent=turn.get("intent"),
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
    expected_intent: str | None = None
    escalation_reason: str | None = None

    @property
    def intent_correct(self) -> bool | None:
        """None when the turn carries no label, so unlabelled turns do not count."""
        if self.expected_intent is None:
            return None
        return self.intent == self.expected_intent

    @property
    def stale(self) -> bool:
        """An intent inherited from the thread that the labels say is the wrong one."""
        return self.inherited and self.intent_correct is False

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
    intent_accuracy: float = 0.0
    stale_intents: int = 0
    records: list[TurnRecord] = field(default_factory=list)

    def as_row(self) -> dict[str, object]:
        return {
            "rewriter": self.rewriter,
            "turns": self.turns,
            "follow_ups": self.follow_ups,
            "hit_rate": round(self.hit_rate, 4),
            "follow_up_hit_rate": round(self.follow_up_hit_rate, 4),
            "intent_accuracy": round(self.intent_accuracy, 4),
            "intents_inherited": self.inherited,
            "stale_intents": self.stale_intents,
            "escalations": self.escalations,
            "rewritten": self.rewritten,
        }


def run(
    index: VectorIndex,
    dialogues: list[Dialogue],
    rewriter: QueryRewriter | str = "concat",
    responder: Responder | None = None,
    context_window: int = CONTEXT_WINDOW,
) -> RewriterScores:
    """Replay every dialogue turn by turn, feeding each turn the thread before it."""
    if isinstance(rewriter, str):
        rewriter = get_rewriter(rewriter)
    agent = ResponsiveAgent(
        index=index, responder=responder, rewriter=rewriter, context_window=context_window
    )

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
                    expected_intent=turn.intent,
                    escalation_reason=reply.escalation_reason,
                )
            )
            conversation.add("customer", turn.customer, reply.classification.intent.value)
            conversation.add("agent", reply.answer.text)

    follow_ups = [record for record in records if record.follow_up]
    labelled = [record for record in records if record.intent_correct is not None]
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
        intent_accuracy=(
            sum(1 for r in labelled if r.intent_correct) / len(labelled) if labelled else 0.0
        ),
        stale_intents=sum(1 for r in records if r.stale),
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


def write_report(
    table: pd.DataFrame,
    results: list[RewriterScores],
    path,
    window_sweep: pd.DataFrame | None = None,
) -> str:
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

    if window_sweep is not None:
        lines += [
            "## Context window",
            "",
            "Why `CONTEXT_WINDOW` is the value it is, rather than a number somebody liked.",
            "",
            window_sweep.to_markdown(index=False),
            "",
            "A window of 0 disables intent inheritance and costs accuracy. Everything from",
            "2 upwards scores identically here, because each customer turn is recorded with",
            "its resolved intent, so the search rarely reaches past the previous exchange —",
            "while `max_prompt_chars` grows linearly the whole way.",
            "",
            "The case this table cannot show is why 4 rather than 2: these dialogues contain",
            "no courtesy turns. When one sits between the question and the follow-up",
            '("posso parcelar?" / "ok obrigado" / "sim"), `last_intent` skips it and a',
            "two-turn window has nothing left to find. Four survives exactly one such",
            "exchange; six and above buy nothing measurable and cost tokens on every call.",
            "",
            "`hit_rate` is flat across the sweep, confirming the window does not touch",
            "retrieval — the lexical rewriter only ever reads the previous customer message.",
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


#: Window sizes the sweep reports. 0 is no context at all; 99 stands in for unbounded.
WINDOW_SIZES = (0, 2, 4, 6, 8, 99)


def sweep_window(
    index: VectorIndex,
    dialogues: list[Dialogue],
    sizes: tuple[int, ...] = WINDOW_SIZES,
    rewriter: QueryRewriter | str = "concat",
    responder: Responder | None = None,
) -> pd.DataFrame:
    """How the context window changes behaviour, so the constant is derived not asserted.

    The window bounds how far back the agent may look. Two things depend on it, and they
    pull in opposite directions:

    * **Intent inheritance** needs enough turns to find the thread's subject. Too small
      and a follow-up inherits nothing.
    * **Staleness** grows with the window. Too large and a customer who changed subject
      three turns ago has their old intent applied to "sim".

    `stale_intents` is the cost side of that trade and the reason the sweep exists: it
    counts turns whose inherited intent the labels say is wrong. A window chosen for
    inheritance alone would keep growing until every bare "sim" inherited something, and
    some of it would be wrong.

    Retrieval is unaffected -- the lexical rewriter only ever looks at the previous
    customer message -- so `hit_rate` is reported as the check that it stays flat.
    """
    rows = []
    for size in sizes:
        scores = run(index, dialogues, rewriter, responder, context_window=size)
        row = scores.as_row()
        rows.append(
            {
                "window": size,
                "intent_accuracy": row["intent_accuracy"],
                "intents_inherited": row["intents_inherited"],
                "stale_intents": row["stale_intents"],
                "escalations": row["escalations"],
                "hit_rate": row["hit_rate"],
                # The cost side: what this window would put in an LLM prompt. Measured on
                # the longest dialogue, since that is where a window either bites or does
                # not, and a mean over mostly-short threads would hide it.
                "max_prompt_chars": _longest_transcript(dialogues, size),
            }
        )
    return pd.DataFrame(rows)


def _longest_transcript(dialogues: list[Dialogue], size: int) -> int:
    """Characters the fullest thread would contribute to a prompt at this window."""
    longest = 0
    for dialogue in dialogues:
        conversation = Conversation()
        for turn in dialogue.turns:
            conversation.add("customer", turn.customer, turn.intent)
            # A stand-in for the agent's reply: real answers quote passages and are far
            # longer, so this is a floor on the prompt cost, not an estimate of it.
            conversation.add("agent", "Resposta do atendimento.")
            longest = max(longest, len(conversation.transcript(size)))
    return longest
