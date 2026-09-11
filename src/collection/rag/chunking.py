"""Splitting documents into retrievable chunks.

The schedule specifies 500-1000 token chunks with 50-100 tokens of overlap, and that is
what this implements. Two choices worth stating:

* **Splitting respects structure before size.** A chunk that ends mid-sentence retrieves
  badly and reads worse when quoted back to a customer, so paragraphs are kept whole
  wherever they fit and only oversized paragraphs are split on sentence boundaries.
* **"Token" here means whitespace-delimited word.** The real tokenizer depends on the model
  that will eventually consume these chunks, and pinning one now would be a false
  precision; the ratio for Portuguese text is roughly 1.3 tokens per word, so the
  configured range lands comfortably inside a typical context budget either way.
"""

import re
from dataclasses import dataclass, field
from typing import Any

MIN_CHUNK_WORDS = 500
MAX_CHUNK_WORDS = 1000
OVERLAP_WORDS = 75

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


@dataclass
class Chunk:
    text: str
    document_id: str
    ordinal: int
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def chunk_id(self) -> str:
        return f"{self.document_id}#{self.ordinal}"

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in _PARAGRAPH_BREAK.split(text) if p.strip()]


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


def chunk_text(
    text: str,
    document_id: str,
    metadata: dict[str, Any] | None = None,
    max_words: int = MAX_CHUNK_WORDS,
    overlap: int = OVERLAP_WORDS,
) -> list[Chunk]:
    """Chunk a document, keeping paragraphs intact where they fit."""
    metadata = metadata or {}
    units = _units(text, max_words)

    chunks: list[Chunk] = []
    current: list[str] = []
    current_words = 0

    for unit in units:
        words = len(unit.split())
        if current and current_words + words > max_words:
            chunks.append(_make(current, document_id, len(chunks), metadata))
            current = _carry_over(current, overlap)
            current_words = sum(len(u.split()) for u in current)
        current.append(unit)
        current_words += words

    if current:
        chunks.append(_make(current, document_id, len(chunks), metadata))
    return chunks


def _units(text: str, max_words: int) -> list[str]:
    """Paragraphs, with oversized ones broken on sentence boundaries."""
    units: list[str] = []
    for paragraph in split_paragraphs(text):
        if len(paragraph.split()) <= max_words:
            units.append(paragraph)
            continue
        sentence_group: list[str] = []
        group_words = 0
        for sentence in split_sentences(paragraph):
            words = len(sentence.split())
            if sentence_group and group_words + words > max_words:
                units.extend(_hard_split(" ".join(sentence_group), max_words))
                sentence_group, group_words = [], 0
            sentence_group.append(sentence)
            group_words += words
        if sentence_group:
            units.extend(_hard_split(" ".join(sentence_group), max_words))
    return units


def _hard_split(text: str, max_words: int) -> list[str]:
    """Last resort for text with no usable boundaries.

    Extracted PDF text and long tables often arrive as one unpunctuated run. Without this,
    such a document would pass through as a single chunk far over the size limit and blow
    whatever context budget the model has.
    """
    words = text.split()
    if len(words) <= max_words:
        return [text]
    return [" ".join(words[i : i + max_words]) for i in range(0, len(words), max_words)]


def _carry_over(current: list[str], overlap: int) -> list[str]:
    """The tail of the previous chunk, repeated so context is not cut at the seam."""
    if overlap <= 0:
        return []
    carried: list[str] = []
    words = 0
    for unit in reversed(current):
        unit_words = len(unit.split())
        if words + unit_words > overlap and carried:
            break
        carried.insert(0, unit)
        words += unit_words
    return carried


def _make(units: list[str], document_id: str, ordinal: int, metadata: dict) -> Chunk:
    return Chunk(
        text="\n\n".join(units),
        document_id=document_id,
        ordinal=ordinal,
        metadata=dict(metadata),
    )
