"""Tests for EvidenceCollector — structured evidence acquisition pipeline."""

from unittest.mock import patch

import pytest
from novi.tools.search_pipeline import SearchResult
from novi.runtime.evidence import EvidenceCollector, EvidenceBundle, _domain


def test_domain_extracts_netloc():
    assert _domain("https://www.youtube.com/watch?v=123") == "youtube.com"
    assert _domain("http://github.com/user/repo") == "github.com"
    assert _domain("https://en.wikipedia.org/wiki/Python") == "en.wikipedia.org"
    assert _domain("") == ""


class TestRankSources:
    """_rank_sources must penalize video platforms and boost text sources."""

    def make_result(self, url: str, title: str = "", snippet: str = "", score: float = 1.0):
        return SearchResult(title=title, url=url, snippet=snippet, score=score)

    def test_youtube_penalized(self):
        results = [
            self.make_result("https://youtube.com/watch?v=123", "Shindo Life PVE Video Guide", "best shindo life pve build video guide 2026"),
            self.make_result("https://example.com/article", "Shindo Life Best PVE Build", "complete shindo life pve build guide for beginners"),
        ]
        ranked = EvidenceCollector._rank_sources(results, "shindo life pve build")
        assert ranked[0].url == "https://example.com/article"
        assert ranked[1].url == "https://youtube.com/watch?v=123"

    def test_text_sources_boosted(self):
        results = [
            self.make_result("https://someblog.com/page", "Python Programming", "learn python programming step by step"),
            self.make_result("https://en.wikipedia.org/wiki/Python", "Python Wiki", "python is a high-level programming language"),
        ]
        ranked = EvidenceCollector._rank_sources(results, "python programming")
        assert ranked[0].url == "https://en.wikipedia.org/wiki/Python"

    def test_all_video_empty_text_sources(self):
        results = [
            self.make_result("https://youtube.com/watch?v=1", "Python Tutorial", "learn python in 10 minutes video"),
            self.make_result("https://youtu.be/abc", "Python Guide", "complete python guide 2026"),
        ]
        ranked = EvidenceCollector._rank_sources(results, "python")
        assert len(ranked) == 2

    def test_multiple_sources_ranked_correctly(self):
        results = [
            self.make_result("https://youtube.com/watch?v=1", "Game Guide Video", "best game build tips and tricks video"),
            self.make_result("https://ign.com/article", "IGN Best Build Guide", "complete best build guide for the game"),
            self.make_result("https://fandom.com/wiki/Game", "Game Fandom Wiki", "game wiki with complete build information"),
            self.make_result("https://unknown.net/post", "Game Blog", "some game build content and tips"),
        ]
        ranked = EvidenceCollector._rank_sources(results, "best game build guide")
        known_text = {"ign.com", "fandom.com"}
        # Top 2 should be the text sources (IGN and Fandom)
        top_domains = {_domain(r.url) for r in ranked[:2]}
        assert top_domains == known_text, f"Expected {known_text}, got {top_domains}"


class TestMerge:
    """_merge must produce structured evidence summary."""

    def make_result(self, url: str, title: str = "", snippet: str = "", full_text: str = ""):
        return SearchResult(title=title, url=url, snippet=snippet, full_text=full_text)

    def test_merge_single_text_source(self):
        results = [
            self.make_result("https://example.com/article", "Test Article", "A snippet", "Full content here")
        ]
        bundle = EvidenceCollector._merge("test query", results)
        assert bundle.query == "test query"
        assert bundle.source_count == 1
        assert bundle.has_video_sources is False
        assert "Test Article" in bundle.merged_text
        assert "Full content here" in bundle.merged_text
        assert "Evidence Summary" in bundle.merged_text

    def test_merge_multiple_sources(self):
        results = [
            self.make_result("https://site1.com/a", "Source 1", "snippet 1", "full text one"),
            self.make_result("https://site2.com/b", "Source 2", "snippet 2", "full text two"),
            self.make_result("https://site3.com/c", "Source 3", "snippet 3", "full text three"),
        ]
        bundle = EvidenceCollector._merge("multi source query", results)
        assert bundle.source_count == 3
        assert "Source 1" in bundle.merged_text
        assert "Source 2" in bundle.merged_text
        assert "Source 3" in bundle.merged_text
        assert "full text one" in bundle.merged_text
        assert "full text two" in bundle.merged_text
        assert "full text three" in bundle.merged_text

    def test_merge_detects_video(self):
        results = [
            self.make_result("https://youtube.com/watch?v=abc", "Video Guide", "watch this video"),
        ]
        bundle = EvidenceCollector._merge("video query", results)
        assert bundle.has_video_sources is True
        assert bundle.source_count == 0  # No text sources
        assert "Video Source" in bundle.merged_text

    def test_merge_text_preferred_over_video(self):
        results = [
            self.make_result("https://youtube.com/watch?v=1", "Video", "video content"),
            self.make_result("https://text.com/article", "Article", "text content", "full article here"),
        ]
        bundle = EvidenceCollector._merge("mixed query", results)
        assert bundle.source_count == 1  # Only text source counted
        assert "Source 1" in bundle.merged_text
        assert "Article" in bundle.merged_text
        assert "full article here" in bundle.merged_text

    def test_merge_empty_results(self):
        bundle = EvidenceCollector._merge("empty query", [])
        assert bundle.source_count == 0
        assert bundle.has_video_sources is False
        assert bundle.merged_text  # Should still have header


class TestEvidenceCollectorIntegration:
    """Integration tests for the full collect() method (no external calls needed)."""

    def test_collect_empty_query(self):
        """Empty query should return empty bundle, not crash."""
        collector = EvidenceCollector()
        bundle = collector.collect("")
        assert isinstance(bundle, EvidenceBundle)
        assert bundle.source_count == 0
        assert bundle.query == ""

    def test_collect_no_results(self):
        """Search returning no results yields an empty bundle."""
        collector = EvidenceCollector()
        with patch("novi.runtime.evidence._search_multi", return_value=([], None)):
            bundle = collector.collect("zxvzxcvasdfqwer")
        assert isinstance(bundle, EvidenceBundle)
        assert bundle.source_count == 0


class TestEvidenceBundle:
    """EvidenceBundle dataclass tests."""

    def test_default_construction(self):
        bundle = EvidenceBundle(query="test")
        assert bundle.query == "test"
        assert bundle.results == []
        assert bundle.merged_text == ""
        assert bundle.source_count == 0
        assert bundle.has_video_sources is False
        assert bundle.latency_ms == 0.0

    def test_error_field(self):
        bundle = EvidenceBundle(query="q", error="HTTP 400: Bad Request")
        assert bundle.error == "HTTP 400: Bad Request"

    def test_error_default_none(self):
        bundle = EvidenceBundle(query="q")
        assert bundle.error is None
