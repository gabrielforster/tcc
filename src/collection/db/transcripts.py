"""Writing a turn and the evidence behind it as one thing.

A turn whose evidence was lost is worse than no turn: it looks like an answer given with
nothing retrieved, which is a different and more alarming fact. So the turn and its
retrieval rows are staged together inside a savepoint, and the passages are checked
before anything is staged -- a passage with no `chunk_id` or `version_id` came from the
in-memory index, cannot be cited, and is refused rather than silently dropped.

A turn with no passages is written all the same. "No row" and "no retrieval" must not look
alike, and an answer given with nothing retrieved is the case an analyst most wants to
find. The caller owns the commit; this function never commits, so the turn lands or fails
with the rest of the unit of work it is part of.
"""

from collections.abc import Sequence
from typing import Any

from collection.db.models import Conversation, Turn, TurnRetrieval
from collection.db.repository import TenantScope
from collection.rag.index import Retrieved


def record_turn(
    scope: TenantScope,
    conversation: Conversation,
    *,
    role: str,
    text: str,
    passages: Sequence[Retrieved] = (),
    **fields: Any,
) -> Turn:
    """Stage a turn and one `TurnRetrieval` per passage, ranked from 1 in the given order."""
    uncitable = [p for p in passages if p.chunk_id is None or p.version_id is None]
    if uncitable:
        raise ValueError(
            f"{len(uncitable)} passage(s) carry no chunk_id/version_id and cannot be recorded; "
            "only the database retriever's results can be cited"
        )

    with scope.session.begin_nested():
        turn = Turn(conversation_id=conversation.id, role=role, text=text, **fields)
        scope.add(turn)
        scope.session.flush()
        for rank, passage in enumerate(passages, start=1):
            scope.add(
                TurnRetrieval(
                    turn_id=turn.id,
                    version_id=passage.version_id,
                    chunk_id=passage.chunk_id,
                    score=passage.score,
                    rank=rank,
                )
            )
    return turn
