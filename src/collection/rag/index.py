"""The vector index and retrieval.

In-memory and exact: the corpus here is a company's collection policies, scripts and FAQ --
hundreds of chunks, not millions -- so an approximate index would add operational weight
and a recall penalty in exchange for speed nobody needs. Exact cosine similarity over a
normalised matrix is one dot product, and it removes a whole class of "did the index lose
this document" question from the evaluation.

Swapping in pgvector or Qdrant later means implementing `search`; the schema (chunk id,
text, metadata, vector) is already what those stores expect.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from collection.rag.chunking import Chunk, chunk_text
from collection.rag.documents import Document
from collection.rag.embeddings import Embedder, get_embedder


@dataclass
class Retrieved:
    chunk: Chunk
    score: float

    @property
    def document_id(self) -> str:
        """File-level id: CSV rows report the table they came from."""
        return self.chunk.metadata.get("parent_id", self.chunk.document_id)

    @property
    def text(self) -> str:
        return self.chunk.text


class VectorIndex:
    def __init__(self, embedder: Embedder | None = None) -> None:
        self.embedder = embedder or get_embedder("tfidf")
        self.chunks: list[Chunk] = []
        self._vectors: np.ndarray | None = None

    # -------------------------------------------------------------------- building
    def add_documents(self, documents: list[Document], **chunking: Any) -> "VectorIndex":
        for document in documents:
            self.chunks.extend(
                chunk_text(
                    document.text,
                    document_id=document.document_id,
                    metadata={**document.metadata, "parent_id": document.root_id},
                    **chunking,
                )
            )
        return self

    def build(self) -> "VectorIndex":
        if not self.chunks:
            raise ValueError("Nothing to index: add documents first.")
        texts = [chunk.text for chunk in self.chunks]
        self.embedder.fit(texts)
        self._vectors = self.embedder.encode(texts)
        return self

    # ------------------------------------------------------------------- searching
    def search(self, query: str, top_k: int = 5) -> list[Retrieved]:
        if self._vectors is None:
            raise RuntimeError("Index not built: call build() first.")
        query_vector = self.embedder.encode([query])[0]
        # Vectors are unit-normalised, so the dot product is the cosine similarity.
        scores = self._vectors @ query_vector
        best = np.argsort(scores)[::-1][:top_k]
        return [Retrieved(chunk=self.chunks[i], score=float(scores[i])) for i in best]

    def search_documents(self, query: str, top_k: int = 5) -> list[str]:
        """Distinct document ids behind the top chunks, best first."""
        seen: list[str] = []
        for hit in self.search(query, top_k=top_k * 3):
            if hit.document_id not in seen:
                seen.append(hit.document_id)
            if len(seen) == top_k:
                break
        return seen

    # ----------------------------------------------------------------- persistence
    def save(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {"embedder": self.embedder, "chunks": self.chunks, "vectors": self._vectors}, path
        )
        return path

    @classmethod
    def load(cls, path: Path | str) -> "VectorIndex":
        payload = joblib.load(Path(path))
        index = cls(embedder=payload["embedder"])
        index.chunks = payload["chunks"]
        index._vectors = payload["vectors"]
        return index

    def __len__(self) -> int:
        return len(self.chunks)

    def stats(self) -> dict[str, Any]:
        words = [chunk.word_count for chunk in self.chunks]
        return {
            "documents": len(
                {chunk.metadata.get("parent_id", chunk.document_id) for chunk in self.chunks}
            ),
            "chunks": len(self.chunks),
            "embedder": self.embedder.name,
            "dimensions": 0 if self._vectors is None else int(self._vectors.shape[1]),
            "mean_words_per_chunk": round(float(np.mean(words)), 1) if words else 0.0,
            "max_words_per_chunk": int(max(words)) if words else 0,
        }
