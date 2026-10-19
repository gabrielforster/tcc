"""Retrieval evaluation.

Answer quality cannot be assessed without first knowing whether the right passage was even
retrieved: an LLM that hallucinates because retrieval returned nothing relevant is a
retrieval failure, not a generation one. This module measures the retrieval half on its own,
against a labelled question set, so the two failure modes stay distinguishable.

Metrics, per the schedule's "retrieval top-k avaliado por precisão/revocação":

* **precision@k** -- share of returned documents that are relevant.
* **recall@k** -- share of relevant documents that were returned. With one relevant document
  per question this is hit rate, which is the number that matters operationally: did the
  agent get what it needed at all.
* **MRR** -- how far down the list the first relevant document sat. It separates "found it
  first" from "found it fifth", which precision@5 alone hides.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from collection.rag.index import VectorIndex


@dataclass(frozen=True)
class Question:
    question: str
    relevant: frozenset[str]
    note: str = ""


@dataclass
class RetrievalScores:
    k: int
    precision: float
    recall: float
    mrr: float
    questions: int
    misses: list[str]

    def as_row(self) -> dict[str, float | int]:
        return {
            "k": self.k,
            "precision@k": round(self.precision, 4),
            "recall@k": round(self.recall, 4),
            "mrr": round(self.mrr, 4),
            "questions": self.questions,
        }


def evaluate(index: VectorIndex, questions: list[Question], k: int = 5) -> RetrievalScores:
    precisions, recalls, reciprocal_ranks = [], [], []
    misses: list[str] = []

    for item in questions:
        retrieved = index.search_documents(item.question, top_k=k)
        hits = [doc for doc in retrieved if doc in item.relevant]

        precisions.append(len(hits) / k)
        recalls.append(len(hits) / len(item.relevant) if item.relevant else 0.0)

        rank = next((i for i, doc in enumerate(retrieved, start=1) if doc in item.relevant), None)
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        if rank is None:
            misses.append(item.question)

    n = max(len(questions), 1)
    return RetrievalScores(
        k=k,
        precision=sum(precisions) / n,
        recall=sum(recalls) / n,
        mrr=sum(reciprocal_ranks) / n,
        questions=len(questions),
        misses=misses,
    )


def load_questions(path: Path | str) -> list[Question]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        Question(
            question=item["question"],
            relevant=frozenset(item["relevant"]),
            note=item.get("note", ""),
        )
        for item in payload
    ]


def sweep(index: VectorIndex, questions: list[Question], ks=(1, 3, 5, 10)) -> pd.DataFrame:
    """Metrics across several k, since the right k is an operational choice."""
    return pd.DataFrame([evaluate(index, questions, k=k).as_row() for k in ks])
