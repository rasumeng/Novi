"""EmbeddingService — centralized embedding and reranking.

Eliminates the 3-way duplication of SentenceTransformer construction
across memory/manager.py, memory/knowledge_index.py, and code_indexer.py.

Model config read from [embedding] and [reranker] config sections. Both
services are thin facades over the provider registry in
``embedding_providers`` — backend selection happens once, by config, and
the rest of the system never sees a provider.
"""

from __future__ import annotations

import logging
from typing import Optional

from .embedding_providers import (
    EMBEDDING_PROVIDERS,
    RERANKER_PROVIDERS,
    EmbeddingProvider,
    RerankerProvider,
)

log = logging.getLogger("novi.services.embedding")


class EmbeddingService:
    """Shared embedding facade.  Backend chosen by ``embedding.backend``.

    Usage:
        embedder = EmbeddingService(config)
        vec = embedder.encode("hello world")
        dim = embedder.dimension
    """

    def __init__(self, config: dict | None = None):
        self._config = config or {}
        self._provider: Optional[EmbeddingProvider] = None

    @property
    def provider(self) -> EmbeddingProvider:
        if self._provider is None:
            backend = self._config.get("embedding", {}).get(
                "backend", "ollama"
            )
            try:
                provider_cls = EMBEDDING_PROVIDERS[backend]
            except KeyError:
                raise ValueError(
                    f"unknown embedding backend '{backend}'; "
                    f"choose from {sorted(EMBEDDING_PROVIDERS)}"
                )
            log.info("embedding backend: %s", backend)
            self._provider = provider_cls(self._config)
        return self._provider

    @property
    def model_name(self) -> str:
        return self.provider.model_name

    def encode(self, text: str, normalize: bool = True) -> list[float]:
        return self.provider.encode(text, normalize=normalize)

    @property
    def dimension(self) -> int:
        return self.provider.dimension

    def clear(self):
        self._provider = None


class RerankerService:
    """Shared reranking facade.  Backend chosen by ``reranker.backend``.

    ``backend = "none"`` disables reranking without needing the HuggingFace
    cross-encoder.
    """

    def __init__(self, config: dict | None = None):
        self._config = config or {}
        self._provider: Optional[RerankerProvider] = None

    @property
    def provider(self) -> RerankerProvider:
        if self._provider is None:
            backend = self._config.get("reranker", {}).get(
                "backend", "sentence_transformers"
            )
            try:
                provider_cls = RERANKER_PROVIDERS[backend]
            except KeyError:
                raise ValueError(
                    f"unknown reranker backend '{backend}'; "
                    f"choose from {sorted(RERANKER_PROVIDERS)}"
                )
            log.info("reranker backend: %s", backend)
            self._provider = provider_cls(self._config)
        return self._provider

    @property
    def model_name(self) -> str:
        return self.provider.model_name

    def rerank(self, query: str, results: list[dict], k: int = 5) -> list[dict]:
        return self.provider.rerank(query, results, k=k)

    def clear(self):
        self._provider = None
