"""Tests for RetrievalCoordinator budget, dedup, and strategy-aware execution."""

from __future__ import annotations

import pytest

from cozmo.runtime.retrieval_coordinator import (
    RetrievalBudget,
    RetrievalCoordinator,
)


class TestRetrievalBudget:
    def test_default_budget(self):
        b = RetrievalBudget()
        assert b.max_web_searches == 1
        assert b.max_web_fetches == 1
        assert b.searches_used == 0
        assert b.fetches_used == 0
        assert not b.is_exhausted

    def test_search_remaining(self):
        b = RetrievalBudget(max_web_searches=2, max_web_fetches=1)
        assert b.search_remaining == 2
        b.searches_used = 1
        assert b.search_remaining == 1

    def test_exhausted_when_both_depleted(self):
        b = RetrievalBudget(max_web_searches=1, max_web_fetches=1)
        b.searches_used = 1
        b.fetches_used = 0
        assert not b.is_exhausted  # fetches remain
        b.fetches_used = 1
        assert b.is_exhausted


class TestRetrievalCoordinatorToolClassification:
    def test_search_tools(self):
        c = RetrievalCoordinator()
        assert c.is_search_tool("web_search")
        assert c.is_search_tool("web_search_pipeline")
        assert not c.is_search_tool("search_web")  # stale ref removed
        assert not c.is_search_tool("web_fetch")

    def test_fetch_tools(self):
        c = RetrievalCoordinator()
        assert c.is_fetch_tool("web_fetch")
        assert c.is_fetch_tool("fetch_url")
        assert c.is_fetch_tool("webfetch")
        assert not c.is_fetch_tool("web_search")

    def test_web_tools(self):
        c = RetrievalCoordinator()
        assert c.is_web_tool("web_search")
        assert c.is_web_tool("web_fetch")
        assert not c.is_web_tool("read_file")
        assert not c.is_web_tool("bash")


class TestRetrievalCoordinatorDuplicateDetection:
    def test_exact_duplicate_blocked(self):
        coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=2))
        coord.record("web_search", {"query": "Wuthering Waves release date"},
                     "Version 2.4 releases August 15")
        result = coord.intercept("web_search", {"query": "Wuthering Waves release date"})
        assert result is not None
        assert "Previously searched" in result
        assert "Version 2.4" in result

    def test_semantic_duplicate_blocked(self):
        coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=2))
        coord.record("web_search", {"query": "Wuthering Waves release date"},
                     "Version 2.4 releases August 15")
        result = coord.intercept("web_search",
                                 {"query": "When does Wuthering Waves come out"})
        assert result is not None
        assert "Previously searched" in result

    def test_different_query_allowed_with_budget(self):
        coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=2))
        coord.record("web_search", {"query": "first search"}, "results")
        # Budget still has 1 remaining, different query allowed
        result = coord.intercept("web_search", {"query": "Genshin Impact update"})
        assert result is None

    def test_different_query_blocked_by_budget(self):
        coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=1))
        coord.record("web_search", {"query": "Wuthering Waves release date"},
                     "Version 2.4 releases August 15")
        result = coord.intercept("web_search", {"query": "Genshin Impact update"})
        assert "Search budget used" in result

    def test_non_search_tool_not_blocked(self):
        coord = RetrievalCoordinator()
        result = coord.intercept("read_file", {"path": "foo.py"})
        assert result is None


class TestRetrievalCoordinatorBudget:
    def test_search_budget_exhausted(self):
        coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=1, max_web_fetches=1))
        coord.record("web_search", {"query": "first search"}, "results A")
        result = coord.intercept("web_search", {"query": "second search"})
        assert result is not None
        assert "Search budget used" in result

    def test_fetch_budget_exhausted(self):
        coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=1, max_web_fetches=1))
        coord.record("web_fetch", {"url": "http://example.com"}, "content A")
        result = coord.intercept("web_fetch", {"url": "http://other.com"})
        assert result is not None
        assert "Fetch budget used" in result

    def test_search_within_budget_allowed(self):
        coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=2, max_web_fetches=1))
        coord.record("web_search", {"query": "first"}, "results A")
        result = coord.intercept("web_search", {"query": "second"})
        assert result is None

    def test_fetch_within_budget_allowed(self):
        coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=1, max_web_fetches=2))
        coord.record("web_fetch", {"url": "http://a.com"}, "content A")
        result = coord.intercept("web_fetch", {"url": "http://b.com"})
        assert result is None

    def test_budget_counts_after_record(self):
        coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=2, max_web_fetches=1))
        coord.record("web_search", {"query": "q1"}, "r1")
        assert coord.budget.searches_used == 1
        coord.record("web_search", {"query": "q2"}, "r2")
        assert coord.budget.searches_used == 2
        coord.record("web_fetch", {"url": "http://x.com"}, "cx")
        assert coord.budget.fetches_used == 1


class TestRetrievalCoordinatorCacheSeeding:
    def test_seed_cache_prevents_duplicate_search(self):
        coord = RetrievalCoordinator()
        coord.seed_cache("What is the next Wuthering Waves update",
                         "Version 2.4 releases August 15")
        result = coord.intercept("web_search",
                                 {"query": "Wuthering Waves update next"})
        assert result is not None
        assert "Version 2.4" in result

    def test_seed_cache_empty_query_noop(self):
        coord = RetrievalCoordinator()
        coord.seed_cache("", "some content")
        assert len(coord._seen_queries) == 0

    def test_seed_cache_empty_result_noop(self):
        coord = RetrievalCoordinator()
        coord.seed_cache("test query", "")
        assert len(coord._seen_queries) == 0


class TestRetrievalCoordinatorKNOWLEDGE_THEN_WEB:
    """Simulate full Wuthering Waves scenario: KB empty → 1 web search → 1 fetch → done."""

    def test_exactly_one_search_one_fetch(self):
        coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=1, max_web_fetches=1))

        # KB empty (simulated)
        assert coord.budget.searches_used == 0

        # First web search — allowed
        blocked = coord.intercept("web_search", {"query": "Wuthering Waves update"})
        assert blocked is None
        coord.record("web_search", {"query": "Wuthering Waves update"}, "results...")
        assert coord.budget.searches_used == 1

        # Second web search — blocked (budget exhausted + duplicate)
        blocked = coord.intercept("web_search", {"query": "Wuthering Waves patch notes"})
        assert blocked is not None

        # First fetch — allowed
        blocked = coord.intercept("web_fetch", {"url": "http://example.com/news"})
        assert blocked is None
        coord.record("web_fetch", {"url": "http://example.com/news"}, "fetched content")
        assert coord.budget.fetches_used == 1

        # Second fetch — blocked
        blocked = coord.intercept("web_fetch", {"url": "http://example.com/other"})
        assert blocked is not None

    def test_kb_sufficient_no_extra_searches(self):
        """When KB already has results, further searches are blocked as duplicates."""
        coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=1, max_web_fetches=0))
        coord.seed_cache("Python decorators", "Python decorators are functions that modify...")

        blocked = coord.intercept("web_search", {"query": "Python decorators explained"})
        assert blocked is not None
        assert "Previously searched" in blocked

    def test_knowledge_only_budget_no_web(self):
        """KNOWLEDGE_ONLY strategy should use 0 search/fetch budget."""
        coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=0, max_web_fetches=0))

        blocked = coord.intercept("web_search", {"query": "anything"})
        assert blocked is not None
        assert "Search budget used" in blocked

        blocked = coord.intercept("web_fetch", {"url": "http://example.com"})
        assert blocked is not None
        assert "Fetch budget used" in blocked


class TestRetrievalCoordinatorEdgeCases:
    def test_empty_query_does_not_crash(self):
        coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=0))
        result = coord.intercept("web_search", {"query": ""})
        assert result is not None
        assert "Search budget used" in result

    def test_empty_query_with_budget_returns_none(self):
        coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=1))
        result = coord.intercept("web_search", {"query": ""})
        assert result is None

    def test_non_dict_args(self):
        coord = RetrievalCoordinator()
        coord.record("web_search", "raw string", "result")
        assert coord.budget.searches_used == 1

    def test_record_non_web_tool(self):
        coord = RetrievalCoordinator()
        coord.record("read_file", {"path": "x"}, "content")
        assert coord.budget.searches_used == 0
        assert coord.budget.fetches_used == 0

    def test_extract_terms(self):
        terms = RetrievalCoordinator._extract_terms("What is the latest version of Python?")
        assert "latest" in terms
        assert "version" in terms
        assert "python" in terms
        assert "what" not in terms
        assert "is" not in terms
