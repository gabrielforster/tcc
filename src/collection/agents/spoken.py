"""A reply shaped for the ear: the few sentences that answer, not the documents that hold them.

The template responder answers by quoting the two best passages whole. In a chat that is
merely long; read aloud it is a minute and a half of headings and lists the customer has to
sit through before they can say anything (median 102 s in the first call benchmark,
docs/voice-call.md). The channel is not allowed to cut it -- what the agent says is the
agent's decision, and a channel that trimmed replies would be deciding which part of a
policy the customer hears. So the shaping happens here, on the agent's side of the seam,
when the turn arrives with a word budget.

What it keeps is the template's guarantee. Every sentence spoken is a sentence of a
retrieved passage, verbatim: nothing is paraphrased, summarised or joined, so the spoken
reply cannot say anything the documents do not. What it adds is a choice of *which*
sentences: those of the one section -- a heading and what sits under it -- most like the
customer's question, in the order they were written. The heading is scored with its
sentences because in an FAQ the heading carries the question ("Como emito a segunda via do
boleto?") and the text under it carries only the answer.

One section, not the best sentences from anywhere. The first version ranked sentences
across all passages and filled the budget greedily, and the budget got filled: after the
sentence that answered came fragments of the escalation list ("Contestação do valor ou da
existência da dívida.") that matched a word of the question and meant nothing on their own.
A section was written to be read as a unit; sentences pulled out of three of them were not.
A reply shorter than the budget is the point, not a waste of it.

Headings are scored but never spoken: a heading announces, it does not answer. List markers
and emphasis are dropped because a synthesiser reads them as noise; the words between them
are kept as written.
"""

import re
from dataclasses import dataclass

from collection.rag.embeddings import TfidfEmbedder
from collection.rag.index import Retrieved
from collection.voice.speak import spoken_words

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_LIST_MARKER = re.compile(r"^(?:[-*+]|\d+[.)])\s+")


@dataclass(frozen=True)
class Sentence:
    text: str
    #: The heading the sentence sits under, scored with it but never spoken.
    heading: str
    document_id: str

    @property
    def words(self) -> int:
        return spoken_words(self.text)


def sentences_of(passage: Retrieved) -> list[Sentence]:
    """A passage as speakable sentences, each with the heading above it.

    Lines are joined into paragraphs before splitting, since the corpus wraps sentences
    across lines. A list item is its own paragraph: it was written as a unit.
    """
    found: list[Sentence] = []
    heading = ""
    paragraph: list[str] = []

    def flush() -> None:
        text = " ".join(paragraph)
        paragraph.clear()
        for part in _SENTENCE_END.split(text):
            part = " ".join(part.split())
            if part:
                found.append(Sentence(part, heading, passage.document_id))

    for raw in passage.text.splitlines():
        line = raw.strip()
        if not line:
            flush()
        elif line.startswith("#"):
            flush()
            heading = line.lstrip("#").strip()
        elif _LIST_MARKER.match(line):
            flush()
            paragraph.append(_LIST_MARKER.sub("", line).replace("*", ""))
            flush()
        else:
            paragraph.append(line.replace("*", ""))
    flush()
    return found


def sections_of(passages: list[Retrieved]) -> list[list[Sentence]]:
    """The passages' sentences grouped by the heading they sit under, in written order."""
    sections: dict[tuple[str, str], list[Sentence]] = {}
    for passage in passages:
        for sentence in sentences_of(passage):
            sections.setdefault((sentence.document_id, sentence.heading), []).append(sentence)
    return list(sections.values())


def select(message: str, passages: list[Retrieved], max_words: int) -> list[Sentence]:
    """The section that best answers `message`, as many of its sentences as fit.

    Sentences are taken in the order they were written and the first one that does not fit
    ends the reply. A sentence is never cut -- half a sentence of a policy can say the
    opposite of the whole one -- and none is skipped for a later one that fits, because a
    section's later sentences lean on its earlier ones.

    If not even the first sentence fits, it is spoken anyway. An over-long reply is a worse
    call; an empty one is a wrong answer. The corpus has no such sentence (the longest is 28
    spoken words against a budget of 43), and `tests/test_spoken.py` holds it to that.
    """
    sections = sections_of(passages)
    if not sections:
        return []
    units = [" ".join([section[0].heading, *(s.text for s in section)]) for section in sections]
    embedder = TfidfEmbedder().fit([*units, message])
    scores = embedder.encode(units) @ embedder.encode([message])[0]
    # `max` keeps the first of equal scores, so the same question always gets the same reply.
    best = sections[max(range(len(sections)), key=lambda i: scores[i])]

    chosen = [best[0]]
    used = best[0].words
    for sentence in best[1:]:
        if used + sentence.words > max_words:
            break
        chosen.append(sentence)
        used += sentence.words
    return chosen
