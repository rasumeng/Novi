"""Phase C — MemoryManager.query merge shim tests.

query() must stay byte-identical when no knowledge store is injected, and
merge legacy flat rows + knowledge-layer items when one is. Only consumers of
query() are Brain.recall and MemoryRetrievalSource — this shim keeps both
seeing the new layer without touching the retrieval adapters.
"""

import pytest

from cozmo.brain.storage.knowledge_store import KnowledgeStore
from cozmo.brain.types import KnowledgeForm, KnowledgeItem, KnowledgeStatus
from cozmo.memory.manager import MemoryManager
from cozmo.services.embedding import EmbeddingService


class FakeEmbed(EmbeddingService):
    def __init__(self, dim: int = 64):
        super().__init__({"embedding": {"model": "fake-embed"}})
        self._dim = dim

    @property
    def model_name(self):
        return "fake-embed"

    def encode(self, text, normalize=True):
        return [0.01] * self._dim

    @property
    def dimension(self):
        return self._dim


class StubLLM:
    def invoke(self, prompt):
        return "summary"


def make_manager(tmp_path, with_knowledge=True):
    mm = MemoryManager(
        StubLLM(),
        persist_dir=str(tmp_path / "mem"),
        embed_model=FakeEmbed(),
        max_turns=100,
        max_short_term_pairs=10,
        knowledge_store=(
            KnowledgeStore(persist_dir=tmp_path / "brain", embed_model=FakeEmbed())
            if with_knowledge
            else None
        ),
    )
    return mm


def add_knowledge_item(store, **overrides):
    fields = dict(
        id="kn-1",
        form=KnowledgeForm.ATOMIC,
        content="The user prefers Python over Java for backend work.",
        confidence=0.9,
        status=KnowledgeStatus.CANDIDATE,
        tags=("preference",),
        sources=("conv-1",),
    )
    fields.update(overrides)
    store.add(KnowledgeItem(**fields))


def test_without_knowledge_store_is_legacy(tmp_path):
    mm = make_manager(tmp_path, with_knowledge=False)
    mm.store_preference("language", "python")
    results = mm.query("python preference", k=5)
    assert results
    assert results[0]["metadata"].get("type") == "preference"


def test_merged_returns_legacy_and_knowledge(tmp_path):
    mm = make_manager(tmp_path)
    mm.store_preference("language", "python")
    add_knowledge_item(mm.knowledge_store)
    results = mm.query("python preference", k=10)
    texts = [r["text"] for r in results]
    assert any("User preference" in t for t in texts)
    assert any("prefers Python over Java" in t for t in texts)


def test_merged_dedupes_by_text(tmp_path):
    mm = make_manager(tmp_path)
    add_knowledge_item(mm.knowledge_store, content="User preference: language = python")
    results = mm.query("python", k=10)
    normalized = {" ".join(r["text"].lower().split()) for r in results}
    assert len(normalized) == 1


def test_merged_respects_memory_types(tmp_path):
    mm = make_manager(tmp_path)
    mm.store_preference("language", "python")
    add_knowledge_item(mm.knowledge_store)
    results = mm.query("python", k=10, memory_types=["preference"])
    assert len(results) == 2
    results = mm.query("python", k=10, memory_types=["conversation"])
    assert results == []


def test_merged_knowledge_tag_filter_compat(tmp_path):
    mm = make_manager(tmp_path)
    mm.store_preference("language", "python")
    add_knowledge_item(mm.knowledge_store, tags=("fact",))
    results = mm.query("python", k=10, memory_types=["fact"])
    texts = [r["text"] for r in results]
    assert any("prefers Python" in t for t in texts)
    assert all("User preference" not in t for t in texts)


def test_merged_ranked_by_score(tmp_path):
    mm = make_manager(tmp_path)
    add_knowledge_item(mm.knowledge_store, id="kn-a")
    mm.store_preference("language", "python")
    results = mm.query("python", k=10)
    scores = [r.get("score", 0.0) for r in results]
    assert scores == sorted(scores, reverse=True)


def test_knowledge_rows_carry_metadata(tmp_path):
    mm = make_manager(tmp_path)
    add_knowledge_item(mm.knowledge_store)
    results = mm.query("python", k=10)
    assert any(
        r.get("metadata", {}).get("form") == "atomic" for r in results
    )
