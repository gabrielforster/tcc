"""Turning text into vectors, behind an interface so the choice stays reversible.

The default is the multilingual sentence-transformer, because it is the only embedder
whose vectors mean the same thing in every tenant: TF-IDF is fitted to whatever corpus it
is given, so its vectors cannot be stored once and compared later. TF-IDF stays as the
lexical baseline, and it was a deliberate starting point rather than a placeholder:

* it needs no API key, no model download and no GPU, so the pipeline runs in CI and on a
  fresh clone;
* character n-grams handle Portuguese morphology (parcelamento/parcelar/parcelas) without a
  stemmer, which matters because this corpus is Portuguese;
* it gives the paper a real lexical baseline to measure a neural retriever *against*, and
  a baseline is only worth having if it is allowed to win. A neural score on its own means
  little; the same score beside a lexical one over the same questions is a result -- and
  on this corpus the baseline is the one ahead, which is why the default above is a choice
  about storing vectors across tenants rather than a claim that it retrieves better.
  `docs/rag.md` owns the measurement and is the only place the figures live.

`SentenceTransformerEmbedder` is imported lazily so that importing this module stays
cheap, and the loaded model is cached per process: loading it costs seconds and hundreds
of megabytes, and an embedder is built per call site.
"""

from abc import ABC, abstractmethod
from functools import cache

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion

from collection.config import settings


class Embedder(ABC):
    name: str

    @abstractmethod
    def fit(self, texts: list[str]) -> "Embedder":
        """Learn whatever the representation needs from the corpus."""

    @abstractmethod
    def encode(self, texts: list[str]) -> np.ndarray:
        """Rows of unit-normalised vectors, so cosine similarity is a dot product."""


def _normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1.0, norms)


class TfidfEmbedder(Embedder):
    name = "tfidf"

    def __init__(self, max_features: int = 50_000) -> None:
        self.vectoriser = FeatureUnion(
            [
                (
                    "word",
                    TfidfVectorizer(
                        ngram_range=(1, 2), sublinear_tf=True, max_features=max_features
                    ),
                ),
                # Character n-grams cover Portuguese inflection without a stemmer.
                (
                    "char",
                    TfidfVectorizer(
                        analyzer="char_wb",
                        ngram_range=(3, 5),
                        sublinear_tf=True,
                        max_features=max_features,
                    ),
                ),
            ]
        )
        self._fitted = False

    def fit(self, texts: list[str]) -> "TfidfEmbedder":
        self.vectoriser.fit(texts)
        self._fitted = True
        return self

    def encode(self, texts: list[str]) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("Call fit() before encode(): TF-IDF is corpus-dependent.")
        return _normalise(np.asarray(self.vectoriser.transform(texts).todense(), dtype=float))


@cache
def _load_model(name: str):
    """One loaded model per name for the whole process, shared by every embedder."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(name)


class SentenceTransformerEmbedder(Embedder):
    """The platform embedder: one model for every tenant, so vectors are comparable."""

    name = "sentence-transformer"

    def __init__(self, model: str | None = None):
        self.model_name = model or settings.embedding_model

    def _load(self):
        return _load_model(self.model_name)

    def fit(self, texts: list[str]) -> "SentenceTransformerEmbedder":
        self._load()
        return self

    def encode(self, texts: list[str]) -> np.ndarray:
        vectors = self._load().encode(texts, convert_to_numpy=True, show_progress_bar=False)
        return _normalise(np.asarray(vectors, dtype=float))


EMBEDDERS: dict[str, type[Embedder]] = {
    "tfidf": TfidfEmbedder,
    "sentence-transformer": SentenceTransformerEmbedder,
}


def get_embedder(name: str = "sentence-transformer") -> Embedder:
    if name not in EMBEDDERS:
        raise ValueError(f"Unknown embedder: {name!r}. Available: {', '.join(EMBEDDERS)}")
    return EMBEDDERS[name]()
