"""Memory & knowledge correctness regression tests (Pre-Phase 9 sprint).

Covers:
  - Knowledge re-index idempotency (deterministic chunk ids, no duplicates)
  - Stale chunk removal on file change
  - Legacy uuid rows cleaned up on re-index
  - Oversized-paragraph chunking (force-split)
  - MemoryManager lifecycle respects configured limits
"""

import time
from uuid import uuid4

import pytest

from cozmo.memory.knowledge_index import KnowledgeIndex, _chunk_with_overlap
from cozmo.memory.manager import MemoryManager
from cozmo.services.embedding import EmbeddingService


class FakeEmbed(EmbeddingService):
    """EmbeddingService stand-in that avoids loading a real model."""

    def __init__(self, dim: int = 384):
        super().__init__({"embedding": {"model": "fake-embed"}})
        self._dim = dim

    @property
    def model_name(self) -> str:
        return "fake-embed"

    def encode(self, text: str, normalize: bool = True) -> list[float]:
        return [0.01] * self._dim

    @property
    def dimension(self) -> int:
        return self._dim


def _make_index(tmp_path, content: str) -> tuple[KnowledgeIndex, object]:
    kd = tmp_path / "knowledge"
    kd.mkdir(exist_ok=True)
    f = kd / "a.md"
    f.write_text(content, encoding="utf-8")
    ki = KnowledgeIndex(
        knowledge_dir=kd,
        persist_dir=str(tmp_path / "idx"),
        embed_model=FakeEmbed(),
    )
    ki.index_all(force=True)
    return ki, f


def test_reindex_is_idempotent(tmp_path):
    ki, _ = _make_index(tmp_path, "Para one.\n\nPara two.\n\nPara three.")
    n1 = ki.count()
    assert n1 >= 1

    ki.index_all(force=True)
    n2 = ki.count()
    assert n2 == n1, f"re-index created duplicates: {n1} -> {n2}"


def test_deterministic_chunk_ids(tmp_path):
    ki, _ = _make_index(tmp_path, "Para one.\n\nPara two.")
    ids = sorted(r["id"] for r in ki.store.list_all(limit=100))
    assert all("::" in i for i in ids), f"non-deterministic ids: {ids}"
    assert ids == sorted(f"a.md::{i}" for i in range(len(ids)))


def test_stale_chunks_removed_on_file_change(tmp_path):
    long_para = "word " * 120  # ~600 chars per paragraph
    ki, f = _make_index(tmp_path, f"{long_para}\n\n{long_para}\n\n{long_para}")
    n1 = ki.count()
    assert n1 >= 2, f"expected multi-chunk file, got {n1}"

    time.sleep(0.01)
    f.write_text(long_para, encoding="utf-8")
    ki.index_all()  # mtime changed -> re-indexes changed file only

    n2 = ki.count()
    assert n2 < n1, f"stale chunks not removed: {n1} -> {n2}"
    ids = [r["id"] for r in ki.store.list_all(limit=100)]
    chunk_nums = [int(i.split("::")[1]) for i in ids]
    assert max(chunk_nums) == 0, f"old chunks still present: {ids}"


def test_legacy_uuid_rows_removed_on_reindex(tmp_path):
    ki, f = _make_index(tmp_path, "Hello world knowledge.")
    ki.store.add_texts(
        ["stale legacy chunk"],
        [{"path": "a.md", "type": "knowledge", "embed_model": "fake-embed"}],
        ids=[str(uuid4())],
    )
    assert ki.count() == 2

    ki.index_file(f)
    after = ki.count()
    assert after == 1, f"legacy row survived re-index: count={after}"
    ids = [r["id"] for r in ki.store.list_all(limit=100)]
    assert all("::" in i for i in ids), f"legacy uuid ids remain: {ids}"


def test_oversized_paragraph_force_split():
    big = "x" * 2500
    chunks = _chunk_with_overlap(big, max_chars=1000, overlap_chars=150)
    assert len(chunks) == 3
    assert all(len(c) <= 1000 for c in chunks)


class StubLLM:
    def __init__(self):
        self.invocations = []

    def invoke(self, prompt: str) -> str:
        self.invocations.append(prompt)
        return "Summary of conversation."


def test_short_term_pairs_respected(tmp_path):
    mm = MemoryManager(
        StubLLM(),
        persist_dir=str(tmp_path / "mem"),
        embed_model=FakeEmbed(),
        max_turns=100,
        max_short_term_pairs=2,
    )
    for i in range(4):
        mm.add_interaction(f"u{i}", f"a{i}")
    assert len(mm.short_term) == 2
    assert mm.turn_count == 4
    assert mm.store.count() == 0  # max_turns=100, no summarize yet


def test_summarize_at_max_turns(tmp_path):
    mm = MemoryManager(
        StubLLM(),
        persist_dir=str(tmp_path / "mem"),
        embed_model=FakeEmbed(),
        max_turns=3,
        max_short_term_pairs=10,
    )
    for i in range(2):
        mm.add_interaction(f"u{i}", f"a{i}")
    assert mm.store.count() == 0
    assert mm.turn_count == 2

    mm.add_interaction("u2", "a2")  # 3rd turn triggers summarize
    assert mm.store.count() == 1
    assert mm.turn_count == 0
    assert len(mm.short_term) == 1
