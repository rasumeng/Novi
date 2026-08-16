"""Embedding providers — pluggable backends behind one interface.

``EmbeddingService`` and ``RerankerService`` no longer hardcode a backend.
They pick a provider from the config ``[embedding.backend]`` / ``[reranker.backend]``
and delegate. This removes the hard dependency on Hugging Face SentenceTransformers:
the default embedding backend is now local Ollama (``nomic-embed-text``), while the
SentenceTransformers backend remains available for users who want it.

Every provider exports the same contract so callers stay backend-agnostic:
    encode(text, normalize=True) -> list[float]
    dimension -> int
    model_name -> str
"""

from __future__ import annotations

import json
import logging
import math
from abc import ABC, abstractmethod
from urllib.error import URLError
from urllib.request import Request, urlopen

log = logging.getLogger("cozmo.services.embedding")

DEFAULT_OLLAMA_URL = "http://localhost:11434"
_FALLBACK_DIMENSION = 768  # conservative default when the model does not report a dimension


class EmbeddingProvider(ABC):
    """Interface: one encode contract, many backends."""

    @abstractmethod
    def encode(self, text: str, normalize: bool = True) -> list[float]: ...

    @property
    @abstractmethod
    def dimension(self) -> int: ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    def clear(self) -> None:
        """Release any cached resources (no-op for stateless providers)."""

    def __init__(self, config: dict | None = None):
        self._config = config or {}


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class SentenceTransformerEmbeddingProvider(EmbeddingProvider):
    """Legacy HuggingFace backend. Loads a local SentenceTransformer model.

    Kept for backward compatibility; no longer the default. Requires the
    ``sentence_transformers`` package and downloads weights from the HF Hub
    on first use.
    """

    name = "sentence_transformers"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self._model = None

    @property
    def model_name(self) -> str:
        return self._config.get("embedding", {}).get("model", "")

    @property
    def model(self):
        if self._model is None:
            if not self.model_name:
                raise ValueError(
                    "No embedding model configured. Set embedding.model (e.g. via "
                    "Model settings or the Configuration Framework) before use."
                )
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def encode(self, text: str, normalize: bool = True) -> list[float]:
        return self.model.encode(text, normalize_embeddings=normalize).tolist()

    @property
    def dimension(self) -> int:
        return self.model.get_sentence_embedding_dimension() or 384

    def clear(self) -> None:
        self._model = None


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Local Ollama backend (default). Calls ``/api/embeddings``.

    Uses ``nomic-embed-text`` by default (768 dims). No HF Hub access, no
    model download — whatever model is configured must already exist in Ollama.
    """

    name = "ollama"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self._url = (
            self._config.get("embedding", {}).get("url")
            or self._config.get("ollama", {}).get("url")
            or DEFAULT_OLLAMA_URL
        ).rstrip("/")
        self._model = self._config.get("embedding", {}).get("model", "")

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return int(
            self._config.get("embedding", {}).get("dimension")
            or _FALLBACK_DIMENSION
        )

    def _embed(self, text: str) -> list[float]:
        payload = json.dumps({"model": self._model, "prompt": text}).encode(
            "utf-8"
        )
        req = Request(
            f"{self._url}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return list(data.get("embedding", []))

    def encode(self, text: str, normalize: bool = True) -> list[float]:
        vec = self._embed(text)
        return _normalize(vec) if normalize else vec


# ── reranker providers ─────────────────────────────────────────────────

class RerankerProvider(ABC):
    """Interface: rerank(query, results, k) -> list[dict] sorted by relevance."""

    @abstractmethod
    def rerank(self, query: str, results: list[dict], k: int = 5) -> list[dict]: ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    def clear(self) -> None: ...

    def __init__(self, config: dict | None = None):
        self._config = config or {}


class SentenceTransformerRerankerProvider(RerankerProvider):
    """Cross-encoder reranker (HuggingFace). Kept optional/off by default."""

    name = "sentence_transformers"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self._model = None

    @property
    def model_name(self) -> str:
        return self._config.get("reranker", {}).get(
            "model", "cross-encoder/ms-marco-MiniLM-L-6-v2"
        )

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
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

    def clear(self) -> None:
        self._model = None


class NullRerankerProvider(RerankerProvider):
    """Disable reranking entirely — bypasses the HF cross-encoder."""

    name = "none"

    @property
    def model_name(self) -> str:
        return "none"

    def rerank(self, query: str, results: list[dict], k: int = 5) -> list[dict]:
        return results[:k]


# ── registries ────────────────────────────────────────────────────────

EMBEDDING_PROVIDERS: dict[str, type[EmbeddingProvider]] = {
    SentenceTransformerEmbeddingProvider.name: SentenceTransformerEmbeddingProvider,
    OllamaEmbeddingProvider.name: OllamaEmbeddingProvider,
}

RERANKER_PROVIDERS: dict[str, type[RerankerProvider]] = {
    SentenceTransformerRerankerProvider.name: SentenceTransformerRerankerProvider,
    NullRerankerProvider.name: NullRerankerProvider,
}