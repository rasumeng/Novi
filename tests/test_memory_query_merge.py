"""MemoryManager.query — flat legacy read.

The knowledge-layer merge shim was removed: query() reads the flat memory
store only. Knowledge-layer retrieval is exercised through the resolver
(tests/test_resolver.py) and the Brain recall path (tests/test_brain.py).
"""

import pytest

from novi.memory.manager import MemoryManager
from novi.services.embedding import EmbeddingService


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


def make_manager(tmp_path):
    return MemoryManager(
        StubLLM(),
        persist_dir=str(tmp_path / "mem"),
        embed_model=FakeEmbed(),
        max_turns=100,
        max_short_term_pairs=10,
    )


def test_query_returns_flat_memory(tmp_path):
    mm = make_manager(tmp_path)
    mm.store_preference("language", "python")
    results = mm.query("python preference", k=5)
    assert results
    assert results[0]["metadata"].get("type") == "preference"


def test_query_respects_memory_types(tmp_path):
    mm = make_manager(tmp_path)
    mm.store_preference("language", "python")
    mm.store_fact("user is a backend engineer")
    results = mm.query("python", k=10, memory_types=["fact"])
    assert results
    assert all(r.get("metadata", {}).get("type") == "fact" for r in results)
