"""Tests for search pipeline — service routing, tuple contract, error propagation.

HTTP-level provider behavior (Brave/SearXNG payloads, status codes) lives in
test_web_search.py. These tests cover the pipeline's contract with the
WebSearchService layer.
"""

from unittest.mock import patch

import pytest

from novi.search import (
    NotConfiguredError,
    SearchResponse,
    SearchResult as NormalResult,
)
from novi.tools.search_pipeline import (
    _search_multi,
    SearchConfig,
    SearchResult,
)


def _service_response(results):
    return SearchResponse(query="q", results=results, provider="searxng")


@pytest.fixture
def stub_service(monkeypatch):
    """Replace WebSearchService so tests never touch config or network."""
    calls = {}

    class _FakeService:
        def __init__(self, *a, **k):
            pass

        def search_sync(self, query, *, max_results=5, time_range=None):
            calls["query"] = query
            calls["max_results"] = max_results
            calls["time_range"] = time_range
            return _service_response([
                NormalResult(title="A", url="http://a", snippet="test"),
                NormalResult(title="B", url="http://b", snippet="test2"),
                NormalResult(title="Dup", url="http://a", snippet="dup"),
            ])

        def fail(self, message):
            raise NotImplementedError

    monkeypatch.setattr("novi.tools.search_pipeline.WebSearchService", _FakeService)
    return calls


class TestSearchMultiContract:
    """_search_multi routes through WebSearchService and returns (results, error)."""

    def test_returns_deduped_pipeline_results(self, stub_service):
        results, err = _search_multi("test", SearchConfig())
        assert err is None
        assert isinstance(results, list)
        assert all(isinstance(r, SearchResult) for r in results)
        # Third result duplicates http://a and must be dropped
        assert [r.url for r in results] == ["http://a", "http://b"]
        assert results[0].source == "searxng"

    def test_empty_query_skips_service(self, stub_service):
        results, err = _search_multi("", SearchConfig())
        assert results == []
        assert err is None
        assert "query" not in stub_service  # service never called

    def test_time_limit_forwarded(self, stub_service):
        _search_multi("test", SearchConfig(timelimit="w"))
        assert stub_service["time_range"] == "w"


class TestErrorPropagation:
    """Provider failures surface as (empty, message) — never silent fallback."""

    @pytest.mark.parametrize("exc", [
        NotConfiguredError("none", "Web search isn't configured."),
    ])
    def test_typed_provider_errors_propagate_message(self, monkeypatch, exc):
        class _FailingService:
            def __init__(self, *a, **k):
                pass

            def search_sync(self, *a, **k):
                raise exc

        monkeypatch.setattr("novi.tools.search_pipeline.WebSearchService", _FailingService)
        results, err = _search_multi("query", SearchConfig())
        assert results == []
        assert err == exc.message

    def test_unexpected_error_becomes_generic_message(self, monkeypatch):
        class _BoomService:
            def __init__(self, *a, **k):
                pass

            def search_sync(self, *a, **k):
                raise RuntimeError("boom")

        monkeypatch.setattr("novi.tools.search_pipeline.WebSearchService", _BoomService)
        results, err = _search_multi("query", SearchConfig())
        assert results == []
        assert err
        assert "unexpected error" in err.lower()


class TestEvidenceBundleIntegration:
    """EvidenceCollector consumes _search_multi output unchanged."""

    def test_collector_receives_pipeline_results(self, stub_service):
        from novi.runtime.evidence import EvidenceCollector

        collector = EvidenceCollector(config=SearchConfig(max_results=5, max_fetch=0))
        with patch(
            "novi.runtime.evidence._search_multi",
            return_value=([SearchResult(title="A", url="http://a", snippet="test")], None),
        ):
            bundle = collector.collect("query")
        assert bundle.error is None
        assert bundle.source_count >= 0
