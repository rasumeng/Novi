"""
MemoryManager — persistent long-term memory with OKF classification and importance scoring.

Architecture:
  Conversation → OKF Classifier → SentenceTransformer → LanceDB
                                                              │
                                   ┌──────────────────────────┘
                                   ▼
                               Vector / Hybrid Search
                                   │
                                   ▼
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

    The conversation→memory write pipeline is owned by Brain extraction; this
    class retains only the flat persistent-memory read/write API used by the
    no-brain runtime/WebUI fallbacks.
    """

    def __init__(
        self,
        llm,
        persist_dir: str,
        embed_model: str | EmbeddingService | None = None,
        max_turns: int = 5,
        max_short_term_pairs: int = 10,
    ):
        self.llm = llm
        self.short_term: list[dict] = []
        self.max_turns = max_turns
        self.max_short_term_pairs = max_short_term_pairs
        self.turn_count = 0

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
        """Query the flat memory store using importance-weighted hybrid search.

        Args:
            text: Query text
            k: Max results
            distance_threshold: Max cosine distance (lower = stricter)
            memory_types: Filter by type(s), e.g. ["preference", "fact"]
        """
        if memory_types:
            results = self.store.hybrid_search(text, k=k * 2, distance_threshold=distance_threshold)
            results = [r for r in results if r.get("metadata", {}).get("type") in memory_types]
        else:
            results = self.store.search_with_importance(text, k=k, distance_threshold=distance_threshold)

        for item in results:
            if "id" in item:
                self.store.increment_frequency(item["id"])
        return results[:k]

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
