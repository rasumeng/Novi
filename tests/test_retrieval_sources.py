"""Tests for Phase 9 retrieval source adapters.

Covers protocol compliance, RetrievalResult translation, wrapper parity with
the underlying implementations, and source-specific error propagation.

All stores are mocked — no network, no filesystem, no ProjectIndex init.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cozmo.runtime.evidence import EvidenceBundle, RetrievalQuality
from cozmo.runtime.retrieval_budget import ContextAllocation
from cozmo.runtime.sources import (
    FileRetrievalSource,
    IdentityRetrievalSource,
    KnowledgeRetrievalSource,
    MemoryRetrievalSource,
    ProjectRetrievalSource,
    RetrievalResult,
    RetrievalSource,
    RetrievedItem,
    ScenarioRetrievalSource,
    WebRetrievalSource,
)
from cozmo.tools.search_pipeline import SearchResult

BUDGET = ContextAllocation()


def _memory_result(item_id="mem-1", text="remembered fact", score=0.9, distance=0.1):
    return {
        "id": item_id,
        "text": text,
        "score": score,
        "distance": distance,
        "metadata": {"type": "fact", "timestamp": "2026-01-01T00:00:00", "frequency": 3},
    }


def _knowledge_result(item_id="kb-1", text="knowledge chunk"):
    return {
        "id": item_id,
        "text": text,
        "score": 0.95,
        "metadata": {"path": "guides/guide.md", "title": "Guide", "tags": ["tutorial"]},
    }


class _FakeMemoryManager:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def query(self, text, k=5, distance_threshold=0.5, memory_types=None):
        self.calls.append((text, k, distance_threshold, memory_types))
        return self.results


class _FakeKnowledgeIndex:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, k=5, rerank=True):
        self.calls.append((query, k, rerank))
        return self.results


class _FakeProjectIndex:
    def __init__(self, text):
        self.text = text
        self.calls = []
        self.root = "/fake/project"

    def query(self, text, k=5):
        self.calls.append((text, k))
        return self.text


class _FakeCollector:
    def __init__(self, bundle):
        self.bundle = bundle
        self.calls = []

    def collect(self, query, min_sources=2):
        self.calls.append((query, min_sources))
        return self.bundle


class TestProtocolCompliance:
    def test_all_adapters_are_retrieval_sources(self):
        sources = [
            MemoryRetrievalSource(_FakeMemoryManager([])),
            KnowledgeRetrievalSource(_FakeKnowledgeIndex([])),
            WebRetrievalSource(_FakeCollector(EvidenceBundle(query="q"))),
            ProjectRetrievalSource(_FakeProjectIndex("")),
            FileRetrievalSource(),
        ]
        for source in sources:
            assert isinstance(source, RetrievalSource)
            assert callable(source.retrieve)
            assert isinstance(source.id, str)

    @pytest.mark.parametrize(
        "source,expected_id",
        [
            (MemoryRetrievalSource(_FakeMemoryManager([])), "memory"),
            (KnowledgeRetrievalSource(_FakeKnowledgeIndex([])), "knowledge"),
            (WebRetrievalSource(_FakeCollector(EvidenceBundle(query="q"))), "web"),
            (ProjectRetrievalSource(_FakeProjectIndex("")), "project"),
            (FileRetrievalSource(), "file"),
        ],
    )
    def test_source_ids(self, source, expected_id):
        assert source.id == expected_id


class TestMemoryRetrievalSource:
    def test_wraps_memory_query_with_budget_k(self):
        store = _FakeMemoryManager([_memory_result()])
        source = MemoryRetrievalSource(store)
        result = source.retrieve("query", BUDGET)
        assert store.calls == [("query", BUDGET.max_results, 0.5, None)]
        assert result.source == "memory"
        assert result.quality == RetrievalQuality.SUFFICIENT
        assert len(result.items) == 1

    def test_item_translation_parity(self):
        store = _FakeMemoryManager([_memory_result()])
        source = MemoryRetrievalSource(store)
        result = source.retrieve("query", BUDGET)
        item = result.items[0]
        assert item.id == "mem-1"
        assert item.text == "remembered fact"
        assert item.source == "memory"
        assert item.score == 0.9
        assert item.metadata["type"] == "fact"

    def test_score_fallback_uses_distance(self):
        raw = _memory_result()
        del raw["score"]
        raw["distance"] = 0.2
        source = MemoryRetrievalSource(_FakeMemoryManager([raw]))
        result = source.retrieve("query", BUDGET)
        assert result.items[0].score == pytest.approx(0.8)

    def test_constructor_config_forwarded(self):
        store = _FakeMemoryManager([_memory_result()])
        source = MemoryRetrievalSource(
            store,
            memory_types=["fact", "preference"],
            distance_threshold=0.7,
        )
        source.retrieve("query", BUDGET)
        assert store.calls == [("query", BUDGET.max_results, 0.7, ["fact", "preference"])]

    def test_distance_preserved_in_metadata(self):
        raw = _memory_result(distance=0.25)
        source = MemoryRetrievalSource(_FakeMemoryManager([raw]))
        result = source.retrieve("query", BUDGET)
        assert result.items[0].metadata["distance"] == 0.25

    def test_empty_results(self):
        source = MemoryRetrievalSource(_FakeMemoryManager([]))
        result = source.retrieve("query", BUDGET)
        assert result.quality == RetrievalQuality.EMPTY
        assert result.items == []

    def test_store_error_propagates(self):
        store = MagicMock()
        store.query.side_effect = RuntimeError("memory store down")
        result = MemoryRetrievalSource(store).retrieve("query", BUDGET)
        assert result.quality == RetrievalQuality.FAILED
        assert result.error == "memory store down"
        assert result.items == []


class TestKnowledgeRetrievalSource:
    def test_wraps_search_with_budget_k(self):
        store = _FakeKnowledgeIndex([_knowledge_result()])
        source = KnowledgeRetrievalSource(store)
        result = source.retrieve("query", BUDGET)
        assert store.calls == [("query", BUDGET.max_results, True)]
        assert result.source == "knowledge"
        assert result.quality == RetrievalQuality.SUFFICIENT

    def test_item_translation_parity(self):
        source = KnowledgeRetrievalSource(_FakeKnowledgeIndex([_knowledge_result()]))
        result = source.retrieve("query", BUDGET)
        item = result.items[0]
        assert item.id == "kb-1"
        assert item.text == "knowledge chunk"
        assert item.source == "knowledge"
        assert item.score == 0.95
        assert item.metadata["path"] == "guides/guide.md"
        assert item.metadata["title"] == "Guide"

    def test_brain_backed_routes_through_brain_index(self):
        from cozmo.brain import Brain

        class _BrainIndex:
            def __init__(self, rows):
                self.rows = rows
                self.calls = []
            def search(self, query, k=5, rerank=True):
                self.calls.append((query, k, rerank))
                return self.rows
        idx = _BrainIndex([_knowledge_result()])
        brain = Brain(memory=MagicMock(), knowledge_index=idx)
        source = KnowledgeRetrievalSource(brain)
        result = source.retrieve("query", BUDGET)
        assert idx.calls == [("query", BUDGET.max_results, True)]
        assert result.source == "knowledge"
        assert result.items[0].text == "knowledge chunk"

    def test_empty_results(self):
        source = KnowledgeRetrievalSource(_FakeKnowledgeIndex([]))
        result = source.retrieve("query", BUDGET)
        assert result.quality == RetrievalQuality.EMPTY
        assert result.items == []

    def test_store_error_propagates(self):
        store = MagicMock()
        store.search.side_effect = RuntimeError("kb unavailable")
        result = KnowledgeRetrievalSource(store).retrieve("query", BUDGET)
        assert result.quality == RetrievalQuality.FAILED
        assert result.error == "kb unavailable"


class TestWebRetrievalSource:
    def _bundle(self, quality=RetrievalQuality.SUFFICIENT):
        results = [
            SearchResult(
                title="Page A",
                url="https://example.com/a",
                snippet="snippet a",
                source="searxng",
                freshness="today",
                score=0.8,
                full_text="full text a",
            )
        ]
        return EvidenceBundle(query="q", results=results, quality=quality)

    def test_wraps_collector(self):
        collector = _FakeCollector(self._bundle())
        source = WebRetrievalSource(collector)
        result = source.retrieve("query", BUDGET)
        assert collector.calls == [("query", 2)]
        assert result.source == "web"
        assert result.quality == RetrievalQuality.SUFFICIENT
        assert result.error is None

    def test_item_translation_parity(self):
        source = WebRetrievalSource(_FakeCollector(self._bundle()))
        result = source.retrieve("query", BUDGET)
        item = result.items[0]
        assert item.id == "https://example.com/a"
        assert item.text == "full text a"
        assert item.source == "web"
        assert item.score == 0.8
        assert item.metadata["title"] == "Page A"
        assert item.metadata["url"] == "https://example.com/a"

    def test_quality_and_error_pass_through(self):
        bundle = EvidenceBundle(
            query="q",
            error="search api 400",
            quality=RetrievalQuality.FAILED,
        )
        result = WebRetrievalSource(_FakeCollector(bundle)).retrieve("query", BUDGET)
        assert result.quality == RetrievalQuality.FAILED
        assert result.error == "search api 400"
        assert result.items == []

    def test_weak_quality_passes_through(self):
        result = WebRetrievalSource(_FakeCollector(self._bundle(RetrievalQuality.WEAK))).retrieve("query", BUDGET)
        assert result.quality == RetrievalQuality.WEAK

    def test_collector_error_propagates(self):
        collector = MagicMock()
        collector.collect.side_effect = ConnectionError("no network")
        result = WebRetrievalSource(collector).retrieve("query", BUDGET)
        assert result.quality == RetrievalQuality.FAILED
        assert result.error == "no network"


class TestProjectRetrievalSource:
    def test_wraps_project_query_with_budget_k(self):
        store = _FakeProjectIndex("src/foo.py: def foo()")
        source = ProjectRetrievalSource(store)
        result = source.retrieve("query", BUDGET)
        assert store.calls == [("query", BUDGET.max_results)]
        assert result.source == "project"
        assert result.quality == RetrievalQuality.SUFFICIENT

    def test_brain_backed_routes_through_brain_project(self):
        from cozmo.brain import Brain

        class _BrainProject:
            def __init__(self, root, text):
                self.root = root
                self.text = text
                self.calls = []
            def query(self, text, k=5):
                self.calls.append((text, k))
                return self.text
        idx = _BrainProject("/proj", "src/foo.py: def foo()")
        brain = Brain(memory=MagicMock(), project_index=idx)
        source = ProjectRetrievalSource(brain)
        result = source.retrieve("query", BUDGET)
        assert idx.calls == [("query", BUDGET.max_results)]
        assert result.source == "project"
        assert result.items[0].text == "src/foo.py: def foo()"
        assert result.items[0].metadata == {"project_root": "/proj"}

    def test_item_translation_parity(self):
        store = _FakeProjectIndex("src/foo.py: def foo()")
        source = ProjectRetrievalSource(store)
        result = source.retrieve("query", BUDGET)
        item = result.items[0]
        assert item.id == "project"
        assert item.text == "src/foo.py: def foo()"
        assert item.metadata == {"project_root": "/fake/project"}

    def test_empty_text(self):
        source = ProjectRetrievalSource(_FakeProjectIndex(""))
        result = source.retrieve("query", BUDGET)
        assert result.quality == RetrievalQuality.EMPTY
        assert result.items == []

    def test_store_error_propagates(self):
        store = MagicMock()
        store.query.side_effect = RuntimeError("index missing")
        result = ProjectRetrievalSource(store).retrieve("query", BUDGET)
        assert result.quality == RetrievalQuality.FAILED
        assert result.error == "index missing"


class TestFileRetrievalSource:
    def test_noop_always_empty(self):
        source = FileRetrievalSource()
        result = source.retrieve("query", BUDGET)
        assert result.source == "file"
        assert result.quality == RetrievalQuality.EMPTY
        assert result.items == []
        assert result.error is None


class _FakeSource:
    """Implements the RetrievalSource contract from a fixed result."""

    id = "base"

    def __init__(self, items, fail=False):
        self.items = items
        self.fail = fail
        self.calls = []

    def retrieve(self, query, budget):
        self.calls.append((query, budget))
        if self.fail:
            raise RuntimeError("base down")
        return RetrievalResult(
            source="base",
            items=self.items,
            quality=RetrievalQuality.SUFFICIENT if self.items else RetrievalQuality.EMPTY,
        )


class TestScenarioRetrievalSource:
    def test_tags_items_with_scenario(self):
        base = _FakeSource(
            [RetrievedItem(id="k1", text="build uses uv", source="base", score=0.9)]
        )
        source = ScenarioRetrievalSource(base, scenario_id="scn-7")
        result = source.retrieve("build", BUDGET)
        assert result.source == "scenario"
        assert len(result.items) == 1
        assert result.items[0].source == "scenario"
        assert result.items[0].metadata["scenario_id"] == "scn-7"

    def test_empty_when_no_scenario(self):
        source = ScenarioRetrievalSource(_FakeSource([]), scenario_id=None)
        result = source.retrieve("q", BUDGET)
        assert result.quality == RetrievalQuality.EMPTY
        assert result.items == []

    def test_error_propagates(self):
        source = ScenarioRetrievalSource(_FakeSource([], fail=True), scenario_id="scn-1")
        result = source.retrieve("q", BUDGET)
        assert result.quality == RetrievalQuality.FAILED
        assert result.error == "base down"


class TestIdentityRetrievalSource:
    def test_keeps_only_identity_tagged_items(self):
        base = _FakeSource(
            [
                RetrievedItem(  # identity — preference
                    "1", "prefers python", "identity", 0.9, {"tags": ["preference"]}
                ),
                RetrievedItem(
                    "2", "compiler failed on prod", "identity", 0.8, {"tags": ["fact"]}
                ),
            ]
        )
        source = IdentityRetrievalSource(base)
        result = source.retrieve("q", BUDGET)
        assert result.source == "identity"
        assert [i.id for i in result.items] == ["1"]

    def test_empty_when_no_identity_items(self):
        source = IdentityRetrievalSource(
            _FakeSource(
                [RetrievedItem("2", "compiler failed", "identity", 0.8, {"tags": ["fact"]})]
            )
        )
        result = source.retrieve("q", BUDGET)
        assert result.quality == RetrievalQuality.EMPTY

    def test_error_propagates(self):
        source = IdentityRetrievalSource(_FakeSource([], fail=True))
        result = source.retrieve("q", BUDGET)
        assert result.quality == RetrievalQuality.FAILED

    def test_returns_retrieval_result(self):
        assert isinstance(FileRetrievalSource().retrieve("query", BUDGET), RetrievalResult)
