"""Turning text into vectors, behind an interface so the choice stays reversible.

The default is TF-IDF over character n-grams plus words. That is a deliberate starting
point rather than a placeholder:

* it needs no API key, no model download and no GPU, so the pipeline runs in CI and on a
  fresh clone;
* character n-grams handle Portuguese morphology (parcelamento/parcelar/parcelas) without a
  stemmer, which matters because this corpus is Portuguese;
* it gives the paper a real lexical baseline to measure a neural retriever *against*.
  Reporting "embeddings scored 0.82" means little; "embeddings scored 0.82 against a
  TF-IDF baseline at 0.71 on the same questions" is a result.

`SentenceTransformerEmbedder` is the drop-in for that comparison and is imported lazily, so
the heavy dependency only exists for whoever asks for it.
"""

from abc import ABC, abstractmethod

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion


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


class SentenceTransformerEmbedder(Embedder):
    """Neural embeddings, for the comparison against the lexical baseline."""

    name = "sentence-transformer"

    def __init__(self, model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"):
        self.model_name = model
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as error:
                raise ImportError(
                    "Neural embeddings need sentence-transformers, which is not installed "
                    "by default because it pulls in torch. Install it explicitly to run "
                    "the comparison: uv pip install sentence-transformers"
                ) from error
            self._model = SentenceTransformer(self.model_name)
        return self._model

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


def get_embedder(name: str = "tfidf") -> Embedder:
    if name not in EMBEDDERS:
        raise ValueError(f"Unknown embedder: {name!r}. Available: {', '.join(EMBEDDERS)}")
    return EMBEDDERS[name]()
