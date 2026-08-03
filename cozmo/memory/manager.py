"""
MemoryManager — persistent memory with OKF classification and importance scoring.

Replaces ChromaDB with LanceDB + Sentence Transformers.

Architecture:
  Conversation → LLM Summary → OKF Classifier → SentenceTransformer → LanceDB
                                                                         │
                                   ┌─────────────────────────────────────┤
                                   ▼          ▼          ▼              ▼
                               Semantic   Metadata   Hybrid       Structured
                               Vector     Filter     Keyword      Queries
                                   │          │          │              │
                                   ▼          ▼          ▼              ▼
                               Ranking (importance × recency × frequency)
                                   │
                                   ▼
                            Context injection into prompt
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..services import EmbeddingService
from .. import config as cozmo_config
from .lancedb_store import LanceStore

log = logging.getLogger("cozmo.memory.manager")


def _norm_text(text: str) -> str:
    """Normalized dedup key for merged query results."""
    return " ".join((text or "").strip().lower().split())

_memory_manager: "MemoryManager | None" = None


def set_memory_manager(manager: "MemoryManager | None"):
    """Register the active MemoryManager for tool access (mirrors KnowledgeIndex pattern)."""
    global _memory_manager
    _memory_manager = manager


def get_memory_manager() -> "MemoryManager | None":
    return _memory_manager


SUMMARIZE_PROMPT = """Condense the following conversation into 2-3 sentences.
Capture key facts, user preferences, and any actionable items.
Do not include greetings or small talk.

Conversation:
{text}

Summary:"""

MEMORY_TYPES = {
    "conversation": "Conversation summaries from chat sessions",
    "preference": "User preferences and settings",
    "project": "Project context and file structure",
    "fact": "Learned facts and references",
    "learning": "Skills and knowledge acquired",
    "reference": "Reference material and documentation",
}


class MemoryManager:
    """Persistent memory with hybrid search, OKF classification, and importance scoring.

    Phase C: this becomes a compatibility shim. The conversation→memory write
    pipeline (_summarize_and_store) is replaced by Brain extraction; query()
    merges legacy flat rows with knowledge-layer items when a knowledge_store
    is injected. Internals survive only for the brain=None runtime fallback
    and are removed in Phase G.
    """

    def __init__(
        self,
        llm,
        persist_dir: str,
        embed_model: str | EmbeddingService | None = None,
        max_turns: int = 5,
        max_short_term_pairs: int = 10,
        knowledge_store=None,
    ):
        self.llm = llm
        self.short_term: list[dict] = []
        self.max_turns = max_turns
        self.max_short_term_pairs = max_short_term_pairs
        self.turn_count = 0
        self.knowledge_store = knowledge_store

        if isinstance(embed_model, EmbeddingService):
            embed_service = embed_model
        else:
            cfg = cozmo_config.load()
            model_name = embed_model or cfg.get("embedding", {}).get("model", "all-MiniLM-L6-v2")
            embed_service = EmbeddingService({"embedding": {"model": model_name}})

        def embed(text: str) -> list[float]:
            return embed_service.encode(text, normalize=True)

        embed_dim = embed_service.dimension
        self._embedder = embed_service

        self.store = LanceStore(
            uri=Path(persist_dir) / "lancedb",
            table_name="cozmo_memories",
            embed_func=embed,
            embed_dim=embed_dim,
            embed_model=embed_service.model_name,
            vector_index=cozmo_config.load().get("vector_index", {}).get("enabled", True),
        )

    def add_interaction(self, user: str, assistant: str):
        self.short_term.append({"user": user, "assistant": assistant})
        if len(self.short_term) > self.max_short_term_pairs:
            self.short_term = self.short_term[-self.max_short_term_pairs:]
        self.turn_count += 1
        if self.turn_count >= self.max_turns:
            self._summarize_and_store()

    def _summarize_and_store(self):
        text = "\n".join(
            f"User: {m['user']}\nCozmo: {m['assistant']}"
            for m in self.short_term
        )
        summary = self.llm.invoke(SUMMARIZE_PROMPT.format(text=text))

        memory_type = self._classify(summary)

        meta = {
            "timestamp": datetime.now().isoformat(),
            "turns": self.turn_count,
            "type": memory_type,
            "frequency": 1,
            "title": summary.split(".")[0][:80],
            "embed_model": self.store.embed_model,
        }
        self.store.add_texts([summary], [meta])
        self.short_term = self.short_term[-1:]
        self.turn_count = 0

    def _classify(self, text: str) -> str:
        """OKF-style classification: determine memory type from content."""
        t = text.lower()
        if any(w in t for w in ("prefer", "like", "dislike", "favorite", "love", "hate")):
            return "preference"
        if any(w in t for w in ("project", "repository", "codebase", "file", "directory")):
            return "project"
        if any(w in t for w in ("learn", "understand", "know", "concept", "how to")):
            return "learning"
        if any(w in t for w in ("reference", "document", "guide", "manual", "spec")):
            return "reference"
        if any(w in t for w in ("fact", "remember", "important", "note")):
            return "fact"
        return "conversation"

    def store_preference(self, key: str, value: str):
        text = f"User preference: {key} = {value}"
        meta = {
            "timestamp": datetime.now().isoformat(),
            "type": "preference",
            "key": key,
            "frequency": 1,
            "title": f"preference: {key}",
            "embed_model": self.store.embed_model,
        }
        self.store.add_texts([text], [meta])

    def store_project_context(self, context: str):
        meta = {
            "timestamp": datetime.now().isoformat(),
            "type": "project",
            "frequency": 1,
            "embed_model": self.store.embed_model,
        }
        self.store.add_texts([context], [meta])

    def store_fact(self, fact: str, tags: Optional[list[str]] = None):
        title = fact.split(".")[0][:80]
        meta = {
            "timestamp": datetime.now().isoformat(),
            "type": "fact",
            "tags": tags or [],
            "frequency": 1,
            "title": title,
            "embed_model": self.store.embed_model,
        }
        self.store.add_texts([fact], [meta])

    def query(
        self,
        text: str,
        k: int = 5,
        distance_threshold: Optional[float] = 0.5,
        memory_types: Optional[list[str]] = None,
    ) -> list[dict]:
        """Query memory using importance-weighted hybrid search.

        Phase C: when a knowledge_store is injected, merges legacy flat rows
        with knowledge-layer items; otherwise byte-identical to legacy.

        Args:
            text: Query text
            k: Max results
            distance_threshold: Max cosine distance (lower = stricter)
            memory_types: Filter by type(s), e.g. ["preference", "fact"]
        """
        if self.knowledge_store is not None:
            return self._query_merged(text, k, distance_threshold, memory_types)
        return self._query_legacy(text, k, distance_threshold, memory_types)

    def _query_legacy(
        self,
        text: str,
        k: int,
        distance_threshold: Optional[float],
        memory_types: Optional[list[str]],
    ) -> list[dict]:
        if memory_types:
            type_filter = " OR ".join(f"metadata LIKE '%\"type\": \"{t}\"%'" for t in memory_types)
            results = self.store.hybrid_search(text, k=k * 2, distance_threshold=distance_threshold)
            results = [r for r in results if r.get("metadata", {}).get("type") in memory_types]
        else:
            results = self.store.search_with_importance(text, k=k, distance_threshold=distance_threshold)

        for item in results:
            if "id" in item:
                self.store.increment_frequency(item["id"])
        return results[:k]

    def _query_merged(
        self,
        text: str,
        k: int,
        distance_threshold: Optional[float],
        memory_types: Optional[list[str]],
    ) -> list[dict]:
        """Phase C shim: legacy flat rows + knowledge-layer items, deduped.

        Knowledge rows carry tags (preference/fact/learning/project/conversation
        ...). When memory_types is set, items match when their tags overlap the
        requested types; scenario-summary composite items are tagged
        "conversation" so legacy type-filtered retrieval still finds them.
        """
        legacy = self._query_legacy(
            text, k=k * 2, distance_threshold=distance_threshold, memory_types=memory_types
        )
        knowledge = self.knowledge_store.query(
            text, k=k * 2, distance_threshold=distance_threshold
        ) or []

        wanted = set(memory_types or ())
        normalized = {}
        for item in knowledge:
            if wanted:
                tags = set(item.get("metadata", {}).get("tags", ()))
                if not (wanted & tags):
                    continue
            key = _norm_text(item.get("text", ""))
            score = item.get("score")
            if score is None:
                score = 1.0 - item.get("distance", 1.0)
            normalized[key] = item | {"score": score}

        for item in legacy:
            key = _norm_text(item.get("text", ""))
            score = item.get("score")
            if score is None:
                score = 1.0 - item.get("distance", 1.0)
            existing = normalized.get(key)
            if existing is None or score > existing.get("score", -1.0):
                normalized[key] = item | {"score": score}
            if "id" in item:
                self.store.increment_frequency(item["id"])

        ranked = sorted(normalized.values(), key=lambda r: r.get("score", 0.0), reverse=True)
        return ranked[:k]

    def consolidate(self) -> int:
        """Find and merge similar/duplicate memories. Returns count of merges."""
        all_memories = self.store.list_all(limit=500)
        if len(all_memories) < 2:
            return 0

        merged = 0
        seen = set()
        for i, mem_a in enumerate(all_memories):
            if mem_a["id"] in seen:
                continue
            for j in range(i + 1, len(all_memories)):
                mem_b = all_memories[j]
                if mem_b["id"] in seen:
                    continue
                words_a = set(mem_a["text"].lower().split())
                words_b = set(mem_b["text"].lower().split())
                overlap = len(words_a & words_b) / max(len(words_a | words_b), 1)
                if overlap > 0.7:
                    freq_a = mem_a.get("metadata", {}).get("frequency", 1)
                    freq_b = mem_b.get("metadata", {}).get("frequency", 1)
                    if freq_a >= freq_b:
                        seen.add(mem_b["id"])
                    else:
                        seen.add(mem_a["id"])
                        break

        for id_ in seen:
            self.store.delete(id_)
            merged += 1

        return merged

    def list_all(self, limit: int = 100) -> list[dict]:
        return self.store.list_all(limit=limit)

    def count(self) -> int:
        return self.store.count()

    def delete(self, id_: str) -> bool:
        return self.store.delete(id_)

    def query_sql(self, sql: str) -> list[dict]:
        return self.store.query_sql(sql)
