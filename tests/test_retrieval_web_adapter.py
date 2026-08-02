"""Tests for Phase 9 hardening V1/V2: adapter ownership boundaries.

Covers:
- ``WebRetrievalSource.collect`` bundle-form delegate (executor web path).
- ``RetrievalExecutor.execute_search`` routing through the injected web source
  (no direct ``EvidenceCollector`` instantiation in retrieval.py), preserving
  quality transitions, debug trace events, and reformulation retry.
- ``RetrievalExecutor.retrieve_knowledge`` routing through the injected
  knowledge source with byte-identical formatting to the legacy path.

All stores/collectors are mocked — no network, no index init.
"""

from __future__ import annotations

from unittest.mock import patch

from cozmo.runtime.evidence import EvidenceBundle, RetrievalQuality
from cozmo.runtime.retrieval import RetrievalExecutor
from cozmo.runtime.retrieval_budget import ContextAllocation
from cozmo.runtime.sources import KnowledgeRetrievalSource, WebRetrievalSource
from cozmo.runtime.trace import ExecutionTrace


class _FakeCollector:
    def __init__(self, bundles):
        self.bundles = list(bundles)
        self.calls = []

    def collect(self, query, min_sources=2):
        self.calls.append((query, min_sources))
        b = self.bundles.pop(0) if self.bundles else EvidenceBundle(query=query)
        b.query = query
        return b


class _FakeKnowledgeIndex:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, k=5, rerank=True):
        self.calls.append((query, k, rerank))
        return self.results


def _knowledge_result(text="knowledge chunk", score=0.95, path="guides/guide.md", title="Guide"):
    return {
        "id": "kb-1",
        "text": text,
        "score": score,
        "metadata": {"path": path, "title": title, "tags": ["tutorial"]},
    }


def _sufficient_bundle(text="result text", quality=RetrievalQuality.SUFFICIENT):
    return EvidenceBundle(
        query="q",
        results=[{"title": "x", "url": "https://example.com/x"}],
        merged_text=text,
        source_count=1,
        quality=quality,
    )


class TestWebCollectDelegate:
    def test_collect_forwards_min_sources(self):
        collector = _FakeCollector([_sufficient_bundle()])
        source = WebRetrievalSource(collector)
        bundle = source.collect("q", min_sources=2)
        assert collector.calls == [("q", 2)]
        assert bundle.merged_text == "result text"

    def test_collect_default_min_sources_two(self):
        collector = _FakeCollector([_sufficient_bundle()])
        source = WebRetrievalSource(collector)
        source.collect("q")
        assert collector.calls == [("q", 2)]


class TestExecuteSearchWebRouting:
    def test_default_source_uses_adapter_owned_collector(self):
        """Default executor web path flows through the adapter's collector:
        patching the class method still reaches the adapter-owned instance and
        merged_text survives the round trip untouched."""
        from cozmo.runtime.evidence import EvidenceCollector

        exe = RetrievalExecutor(debug_trace=True)

        def fake_collect(self, query, min_sources=2):
            return _sufficient_bundle(text="fake merged summary test query")

        with patch.object(EvidenceCollector, "collect", fake_collect):
            bundle = exe.execute_search("test query")
        assert bundle.merged_text == "fake merged summary test query"
        assert bundle.quality == RetrievalQuality.SUFFICIENT

    def test_injected_web_source_is_used(self):
        collector = _FakeCollector([_sufficient_bundle(text="test query result")])
        exe = RetrievalExecutor(
            debug_trace=True, web_source=WebRetrievalSource(collector)
        )
        bundle = exe.execute_search("test query")
        assert collector.calls == [("test query", 2)]
        assert bundle.merged_text == "test query result"
        assert bundle.quality == RetrievalQuality.SUFFICIENT

    def test_failed_transition_and_trace_event(self):
        exe = RetrievalExecutor(debug_trace=True)
        collector = _FakeCollector([
            EvidenceBundle(query="q", error="search api 400",
                           quality=RetrievalQuality.FAILED),
        ])
        exe = RetrievalExecutor(debug_trace=True, web_source=WebRetrievalSource(collector))
        trace = ExecutionTrace(user_input="q")
        bundle = exe.execute_search("q", trace=trace)
        assert bundle.quality == RetrievalQuality.FAILED
        assert bundle.error == "search api 400"
        assert trace.debug_events[0].data["status"] == "failed"
        assert trace.debug_events[0].data["error"] == "search api 400"

    def test_empty_transition_and_trace_event(self):
        collector = _FakeCollector([
            EvidenceBundle(query="q", quality=RetrievalQuality.EMPTY),
        ])
        exe = RetrievalExecutor(debug_trace=True, web_source=WebRetrievalSource(collector))
        trace = ExecutionTrace(user_input="q")
        bundle = exe.execute_search("q", trace=trace)
        assert bundle.quality == RetrievalQuality.EMPTY
        assert trace.debug_events[0].data["status"] == "empty"

    def test_low_relevance_reformulation_retry_via_adapter(self):
        collector = _FakeCollector([
            _sufficient_bundle(text="nothing relevant here", quality=RetrievalQuality.WEAK),
            _sufficient_bundle(text="foo bar guide content", quality=RetrievalQuality.SUFFICIENT),
        ])
        exe = RetrievalExecutor(debug_trace=True, web_source=WebRetrievalSource(collector))
        bundle = exe.execute_search("what is the foo bar")
        assert collector.calls == [("what is the foo bar", 2), ("foo bar", 1)]
        assert bundle.quality == RetrievalQuality.SUFFICIENT
        assert bundle.merged_text == "foo bar guide content"

    def test_blank_query_short_circuits_without_collector(self):
        collector = _FakeCollector([])
        exe = RetrievalExecutor(debug_trace=True, web_source=WebRetrievalSource(collector))
        bundle = exe.execute_search("   ")
        assert bundle.query == "   "
        assert collector.calls == []


class TestRetrieveKnowledgeRouting:
    def test_no_source_returns_empty(self):
        exe = RetrievalExecutor()
        assert exe.retrieve_knowledge("q") == ""

    def test_formats_via_injected_source(self):
        index = _FakeKnowledgeIndex([_knowledge_result()])
        exe = RetrievalExecutor(
            knowledge_source=KnowledgeRetrievalSource(index),
        )
        text = exe.retrieve_knowledge("query")
        assert text == "- **Guide** (guides/guide.md, score=0.95): knowledge chunk"

    def test_legacy_format_parity_with_multi_result(self):
        index = _FakeKnowledgeIndex([
            _knowledge_result(text="chunk one", score=0.9, path="a.md", title="A"),
            _knowledge_result(text="chunk two", score=0.8, path="b.md", title="B"),
        ])
        exe = RetrievalExecutor(knowledge_source=KnowledgeRetrievalSource(index))
        text = exe.retrieve_knowledge("query")
        assert text == (
            "- **A** (a.md, score=0.90): chunk one\n"
            "- **B** (b.md, score=0.80): chunk two"
        )

    def test_newline_replaced_and_truncated(self):
        long = "line one\nline two" + "x" * 500
        index = _FakeKnowledgeIndex([_knowledge_result(text=long)])
        exe = RetrievalExecutor(knowledge_source=KnowledgeRetrievalSource(index))
        text = exe.retrieve_knowledge("query")
        assert "\n" not in text
        assert text.startswith("- **Guide** (guides/guide.md, score=0.95): line one line two")
        assert len(text) < len(long)

    def test_k_capped_at_twenty(self):
        index = _FakeKnowledgeIndex([_knowledge_result()])
        exe = RetrievalExecutor(knowledge_source=KnowledgeRetrievalSource(index))
        exe.retrieve_knowledge("query", k=30)
        assert index.calls == [("query", 20, True)]

    def test_k_forwarded_uncapped_below_twenty(self):
        index = _FakeKnowledgeIndex([_knowledge_result()])
        exe = RetrievalExecutor(knowledge_source=KnowledgeRetrievalSource(index))
        exe.retrieve_knowledge("query", k=5)
        assert index.calls == [("query", 5, True)]

    def test_empty_index_returns_empty(self):
        exe = RetrievalExecutor(
            knowledge_source=KnowledgeRetrievalSource(_FakeKnowledgeIndex([])),
        )
        assert exe.retrieve_knowledge("query") == ""

    def test_index_error_returns_empty(self):
        class _BrokenIndex:
            def search(self, query, k=5, rerank=True):
                raise RuntimeError("kb down")

        exe = RetrievalExecutor(
            knowledge_source=KnowledgeRetrievalSource(_BrokenIndex()),
        )
        assert exe.retrieve_knowledge("query") == ""


class TestMemoryRoundTripHelper:
    def test_flat_shape_restored(self):
        from cozmo.webui_server import _memory_items_to_dicts
        from cozmo.runtime.sources import MemoryRetrievalSource

        class _FakeManager:
            def query(self, text, k=5, distance_threshold=0.5, memory_types=None):
                return [{
                    "id": "mem-1",
                    "text": "remembered",
                    "distance": 0.12,
                    "score": 0.88,
                    "metadata": {"type": "fact", "frequency": 2},
                }]

        result = MemoryRetrievalSource(_FakeManager()).retrieve(
            "q", ContextAllocation(max_results=5)
        )
        out = _memory_items_to_dicts(result)
        assert out == [{
            "id": "mem-1",
            "text": "remembered",
            "distance": 0.12,
            "score": 0.88,
            "metadata": {"type": "fact", "frequency": 2},
        }]

    def test_empty_result(self):
        from cozmo.webui_server import _memory_items_to_dicts
        from cozmo.runtime.sources import MemoryRetrievalSource

        class _FakeManager:
            def query(self, text, k=5, distance_threshold=0.5, memory_types=None):
                return []

        result = MemoryRetrievalSource(_FakeManager()).retrieve(
            "q", ContextAllocation(max_results=5)
        )
        assert _memory_items_to_dicts(result) == []
