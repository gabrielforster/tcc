"""Measuring whether an answer is actually supported by what was retrieved.

The paper has to report a hallucination rate, and "we read the answers and they looked
fine" is not a result. This module scores an answer against the exact passages the model
was given, with three checks that fail for different reasons:

**Numbers.** Every figure in the answer must appear in the passages. This is the sharpest
check in this domain and the cheapest to trust: an invented interest rate, instalment count
or discount is the failure that costs money, and unlike prose it admits an exact comparison.
A number is normalised before matching, so `1.234,56`, `1234,56` and `1234.56` are the same
figure -- the Brazilian thousands separator is a full stop and naive matching would report
hallucinations that are only formatting.

**Sentences.** Each sentence is scored by its best cosine similarity against the passages,
reusing the same TF-IDF representation the retriever uses. This catches fabricated claims
that carry no digits ("o senhor pode pagar em qualquer agência"). It is lexical, so it is
*weak evidence of support and strong evidence of its absence*: a sentence close to a
passage may still be paraphrasing it wrongly, but a sentence close to nothing is grounded
in nothing. The threshold is low for that reason -- it is tuned to catch inventions, not to
certify correctness.

**Citations.** A cited document must be one of the retrieved documents. A model that cites
a file it was never shown has stopped tracking its own evidence.

The vectoriser is fitted on the passages and the answer together, per call. The corpus is a
handful of short texts, so this costs nothing, and fitting on the pair is what makes the
score independent of whatever else lives in the knowledge base.
"""

import re
from dataclasses import dataclass, field

import numpy as np

from collection.rag.embeddings import TfidfEmbedder
from collection.rag.index import Retrieved

#: Below this cosine, a sentence has no support in the passages worth the name.
SUPPORT_THRESHOLD = 0.18
#: Figures below this are ordinals and list markers ("3 frases"), not claims about money.
_NUMBER_PATTERN = re.compile(r"\d[\d.,]*")
_SENTENCE_PATTERN = re.compile(r"(?<=[.!?:])\s+|\n+")
_CITATION_LINE = re.compile(r"^\s*fontes?\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def normalise_number(raw: str) -> str:
    """Canonical form of a figure, so formatting differences are not hallucinations.

    `1.234,56` (pt-BR) and `1234.56` (en) both become `1234.56`; trailing separators that
    were really punctuation (`em 2.`) are dropped.
    """
    text = raw.strip().rstrip(".,")
    if not text:
        return ""
    if "," in text:
        # Brazilian: full stop groups thousands, comma is the decimal mark.
        text = text.replace(".", "").replace(",", ".")
    elif text.count(".") == 1:
        whole, _, fraction = text.partition(".")
        # `1.234` is one thousand two hundred, `1.5` is one and a half. Three digits after
        # the separator with a non-zero lead means grouping, not a decimal.
        if len(fraction) == 3 and whole and whole[0] != "0":
            text = whole + fraction
    else:
        text = text.replace(".", "")
    try:
        value = float(text)
    except ValueError:
        return raw.strip()
    return f"{value:.10g}"


def numbers_in(text: str) -> list[str]:
    found = (normalise_number(match.group()) for match in _NUMBER_PATTERN.finditer(text))
    return [number for number in found if number]


def citations_in(text: str) -> list[str]:
    """Document ids from a `Fonte: a.md, b.csv` line, if the answer wrote one."""
    cited: list[str] = []
    for match in _CITATION_LINE.finditer(text):
        for part in match.group(1).split(","):
            name = part.strip().strip(".;")
            if name:
                cited.append(name)
    return list(dict.fromkeys(cited))


def sentences_in(text: str) -> list[str]:
    """The claims in an answer: what it asserts about the world, and nothing else.

    Two kinds of text are excluded, both because scoring them measures style rather than
    grounding -- and because excluding them has to be arm-neutral. The template responder
    opens with a lead-in and closes with a citation; an LLM does the same. Scoring either
    would report a hallucination rate that mostly tracks who writes more boilerplate.

    * **The citation line.** `Fonte: faq.md` is evidence, not a claim.
    * **Clauses ending in a colon.** `Sobre as condições de parcelamento:` announces what
      follows and asserts nothing that could be true or false.

    Neither exclusion can hide an invented figure: numbers are checked across the whole
    answer, independently of which sentences are scored.
    """
    body = _CITATION_LINE.sub("", text)
    parts = (" ".join(part.split()) for part in _SENTENCE_PATTERN.split(body))
    # One or two words is a greeting or a sign-off; scoring it only adds noise.
    return [part for part in parts if len(part.split()) > 2 and not part.endswith(":")]


@dataclass
class Faithfulness:
    """What an answer claimed, and how much of it the passages actually support."""

    sentence_support: float
    numeric_support: float
    unsupported_sentences: list[str] = field(default_factory=list)
    unsupported_numbers: list[str] = field(default_factory=list)
    invalid_citations: list[str] = field(default_factory=list)
    cited: list[str] = field(default_factory=list)

    @property
    def faithful(self) -> bool:
        """No invented figure, no unsupported claim, no citation of an unseen document."""
        return not (self.unsupported_numbers or self.unsupported_sentences)

    @property
    def citations_valid(self) -> bool:
        return not self.invalid_citations

    def as_row(self) -> dict[str, object]:
        return {
            "sentence_support": round(self.sentence_support, 4),
            "numeric_support": round(self.numeric_support, 4),
            "faithful": self.faithful,
            "citations_valid": self.citations_valid,
            "unsupported_numbers": len(self.unsupported_numbers),
            "unsupported_sentences": len(self.unsupported_sentences),
        }


def _units(passages: list[str]) -> list[str]:
    """Passages broken into comparable pieces, plus the passages themselves.

    Scoring a sentence against a whole chunk punishes it for the chunk's length: cosine
    between eight words and three hundred is small even when the eight are a verbatim
    quote of the three hundred. Splitting the passages into sentences gives each claim
    something its own size to match against. The full passage stays in the pool so a
    sentence that paraphrases across two of them still finds support.
    """
    units = list(passages)
    for passage in passages:
        units.extend(part for part in (p.strip() for p in _SENTENCE_PATTERN.split(passage)) if part)
    return units


def _quoted(sentence: str, passages: list[str]) -> bool:
    """True when the sentence appears in a passage verbatim, ignoring case and spacing."""
    needle = " ".join(sentence.lower().split())
    return any(needle in " ".join(passage.lower().split()) for passage in passages)


def _support_scores(sentences: list[str], passages: list[str]) -> np.ndarray:
    """Best similarity of each sentence against any piece of any passage.

    A verbatim quote short-circuits to 1.0. It is support by definition, and relying on
    cosine to discover that wastes the one case the measure should never get wrong.
    """
    units = _units(passages)
    embedder = TfidfEmbedder().fit(units + sentences)
    unit_vectors = embedder.encode(units)
    sentence_vectors = embedder.encode(sentences)
    scores = (sentence_vectors @ unit_vectors.T).max(axis=1)
    for position, sentence in enumerate(sentences):
        if _quoted(sentence, passages):
            scores[position] = 1.0
    return scores


def evaluate(
    answer: str,
    passages: list[Retrieved],
    threshold: float = SUPPORT_THRESHOLD,
    prior_passages: list[Retrieved] | None = None,
) -> Faithfulness:
    """Score `answer` against the passages it was supposed to be built from.

    With no passages, an answer with any content at all is unsupported by construction --
    which is the correct verdict, not a degenerate case to skip.

    `prior_passages` carries the evidence retrieved earlier in the same conversation, and
    it is what keeps this measure honest once the agent answers in threads. A customer
    asks "posso parcelar?", then "e em 6 vezes?"; the reply may legitimately restate the
    instalment count established one turn ago, from a passage this turn did not retrieve.
    Scoring against only the current turn would report that as a hallucination -- the
    measure would punish the system for remembering correctly.

    The conversation's evidence set is therefore the union of what it has retrieved so
    far. That widens what counts as supported, which is a real cost: an answer can now
    quote something from four turns back that the customer has moved on from. It is the
    right trade, because the alternative scores a correct answer as invented, and a
    metric that cannot tell memory from fabrication measures neither.
    """
    cited = citations_in(answer)
    evidence = list(passages) + list(prior_passages or [])
    sources = [hit.document_id for hit in evidence]
    invalid = [name for name in cited if name not in sources]

    sentences = sentences_in(answer)
    texts = [" ".join(hit.text.split()) for hit in evidence]

    if not sentences:
        return Faithfulness(1.0, 1.0, cited=cited, invalid_citations=invalid)
    if not texts:
        return Faithfulness(
            sentence_support=0.0,
            numeric_support=0.0 if numbers_in(answer) else 1.0,
            unsupported_sentences=sentences,
            unsupported_numbers=numbers_in(answer),
            invalid_citations=invalid,
            cited=cited,
        )

    scores = _support_scores(sentences, texts)
    unsupported_sentences = [
        sentence for sentence, score in zip(sentences, scores, strict=True) if score < threshold
    ]

    supported_numbers = set()
    for text in texts:
        supported_numbers.update(numbers_in(text))
    claimed = numbers_in(_CITATION_LINE.sub("", answer))
    unsupported_numbers = [number for number in claimed if number not in supported_numbers]

    return Faithfulness(
        sentence_support=float(scores.mean()),
        numeric_support=1.0 - (len(unsupported_numbers) / len(claimed)) if claimed else 1.0,
        unsupported_sentences=unsupported_sentences,
        unsupported_numbers=unsupported_numbers,
        invalid_citations=invalid,
        cited=cited,
    )
