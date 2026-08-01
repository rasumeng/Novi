"""EmbeddingService — centralized embedding and reranking.

Eliminates the 3-way duplication of SentenceTransformer construction
across memory/manager.py, memory/knowledge_index.py, and code_indexer.py.

Model config read from [embedding] and [reranker] config sections.
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("cozmo.services.embedding")


class EmbeddingService:
    """Shared embedding model.  Loaded once, reused everywhere.

    Usage:
        embedder = EmbeddingService(config)
        vec = embedder.encode("hello world")
        dim = embedder.dimension
    """

    def __init__(self, config: dict | None = None):
        self._config = config or {}
        self._model = None

    @property
    def model_name(self) -> str:
        return self._config.get("embedding", {}).get("model", "all-MiniLM-L6-v2")

    @property
    def model(self):
        if self._model is None:
            model_name = self.model_name
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(model_name)
        return self._model

    def encode(self, text: str, normalize: bool = True) -> list[float]:
        return self.model.encode(text, normalize_embeddings=normalize).tolist()

    @property
    def dimension(self) -> int:
        return self.model.get_sentence_embedding_dimension() or 384

    def clear(self):
        self._model = None


class RerankerService:
    """Shared cross-encoder reranker.  Loaded once, reused everywhere."""

    def __init__(self, config: dict | None = None):
        self._config = config or {}
        self._model = None

    @property
    def model_name(self) -> str:
        return self._config.get("reranker", {}).get("model", "cross-encoder/ms-marco-MiniLM-L-6-v2")

    @property
    def model(self):
        if self._model is None:
            model_name = self.model_name
            from sentence_transformers import CrossEncoder
            log.info("Loading reranker model: %s", model_name)
            self._model = CrossEncoder(model_name)
        return self._model

    def rerank(self, query: str, results: list[dict], k: int = 5) -> list[dict]:
        if not results:
            return results
        try:
            pairs = [(query, r.get("text", "")) for r in results]
            scores = self.model.predict(pairs)
            for i, s in enumerate(scores):
                results[i]["score"] = round(float(s), 4)
            results.sort(key=lambda x: x.get("score", 0), reverse=True)
            return results[:k]
        except Exception as e:
            log.warning("reranker failed: %s", e)
            return results[:k]

    def clear(self):
        self._model = None
