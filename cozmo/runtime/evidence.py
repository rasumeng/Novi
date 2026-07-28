"""EvidenceCollector — structured evidence acquisition pipeline.

Transforms raw search results into a ranked, fetched, and merged evidence
summary. Replaces the flat-string grounding approach with structured evidence
that the model can reliably consume.

Pipeline:
  query → search → rank/filter → fetch → merge → EvidenceBundle
                                       ↓
                                  sufficient? → yes → return
                                       ↓ no
                                  reformulate → retry
"""

from __future__ import annotations

import enum
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from ..tools.search_pipeline import (
    SearchConfig,
    SearchResult,
    _search_multi,
    fetch_pages,
    clean_content,
    rerank_results,
)

log = logging.getLogger("cozmo.evidence")


class RetrievalQuality(enum.Enum):
    """Quality grade for a retrieval attempt.

    Computed after search + relevance check. Used by runtime to decide
    whether pre-loop capability upgrades or mid-loop recovery are needed.
    """
    SUFFICIENT = "sufficient"
    WEAK = "weak"
    EMPTY = "empty"
    FAILED = "failed"

# YouTube domains to deprioritize
_VIDEO_DOMAINS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "youtu.be", "yewtu.be", "invidious.snopyta.org",
}

# High-quality textual source domains
_TEXT_DOMAINS = {
    "wikipedia.org", "en.wikipedia.org", "github.com",
    "stackoverflow.com", "docs.python.org", "mdn.dev",
    "developer.mozilla.org", "readthedocs.io", "wiki.gg",
    "fandom.com", "ign.com", "polygon.com", "kotaku.com",
    "rockpapershotgun.com", "pcgamer.com", "eurogamer.net",
    "gamespot.com", "gamefaqs.com", "neoseeker.com",
}


@dataclass
class EvidenceBundle:
    """Collected evidence from one search query, structured for model consumption."""

    query: str
    results: list[SearchResult] = field(default_factory=list)
    merged_text: str = ""
    source_count: int = 0
    has_video_sources: bool = False
    latency_ms: float = 0.0
    error: str | None = None
    """Set when the search API itself failed (HTTP error, connection error).
       None means no search error — results may still be empty."""
    quality: RetrievalQuality = RetrievalQuality.EMPTY
    """Quality grade for this retrieval attempt. Default EMPTY until computed."""


class EvidenceCollector:
    """Collects, ranks, fetches, and merges evidence for a query."""

    def __init__(self, config: SearchConfig | None = None):
        self._config = config or SearchConfig()

    def collect(self, query: str, min_sources: int = 2) -> EvidenceBundle:
        """Run the full evidence pipeline.

        1. Search with structured results
        2. Rank (prioritize text, demote video)
        3. Fetch full page content
        4. Merge into structured summary

        Returns EvidenceBundle even on failure (with empty results).
        """
        t0 = datetime.now()

        # Phase 1: Search
        results, search_err = _search_multi(query, self._config)
        if search_err:
            return EvidenceBundle(
                query=query,
                latency_ms=0.0,
                error=search_err,
                quality=RetrievalQuality.FAILED,
            )
        if not results:
            return EvidenceBundle(
                query=query,
                latency_ms=0.0,
                quality=RetrievalQuality.EMPTY,
            )

        # Phase 2: Rank and filter
        results = self._rank_sources(results, query)
        top_results = results[:self._config.max_results]

        # Phase 3: Fetch full pages
        top_results = fetch_pages(
            top_results,
            max_fetch=self._config.max_fetch,
            timeout=self._config.fetch_timeout,
        )

        # Clean fetched content
        for r in top_results:
            if r.full_text:
                r.full_text = clean_content(r.full_text)

        # Phase 4: Merge into evidence summary
        bundle = self._merge(query, top_results)

        latency = (datetime.now() - t0).total_seconds() * 1000
        bundle.latency_ms = round(latency, 2)

        return bundle

    # ── Ranking ─────────────────────────────────────────────────────────

    @staticmethod
    def _rank_sources(results: list[SearchResult], query: str) -> list[SearchResult]:
        """Rank results: boost text sources, penalize video-only content.

        Wraps rerank_results from search_pipeline, then applies our own
        domain-based adjustments.
        """
        results = rerank_results(results, query)

        for r in results:
            domain = _domain(r.url)
            # Penalize video platforms
            if domain in _VIDEO_DOMAINS:
                r.score *= 0.3
            # Boost known text sources
            elif domain in _TEXT_DOMAINS:
                r.score *= 1.5

        return sorted(results, key=lambda x: x.score, reverse=True)

    # ── Merge ───────────────────────────────────────────────────────────

    @staticmethod
    def _merge(query: str, results: list[SearchResult]) -> EvidenceBundle:
        """Merge ranked+fetched results into a structured evidence summary.

        Produces merged_text suitable for injection into system prompt as
        grounding context.
        """
        has_video = any(_domain(r.url) in _VIDEO_DOMAINS for r in results)
        text_results = [r for r in results if _domain(r.url) not in _VIDEO_DOMAINS]

        parts = [f"**Evidence Summary**\nQuery: {query}\n"]

        for i, r in enumerate(text_results[:5], 1):
            content = r.full_text[:3000] if r.full_text else r.snippet[:1000]
            parts.append(
                f"\n**Source {i}** ({r.title})\n"
                f"URL: {r.url}\n"
                f"{content}\n"
            )

        # Include top video source if nothing else
        if not text_results and has_video:
            for r in results[:1]:
                parts.append(
                    f"\n**Video Source** ({r.title})\n"
                    f"URL: {r.url}\n"
                    f"Snippet: {r.snippet}\n"
                )

        merged = "\n---\n".join(parts)

        return EvidenceBundle(
            query=query,
            results=results,
            merged_text=merged,
            source_count=len(text_results),
            has_video_sources=has_video,
        )


def _domain(url: str) -> str:
    """Extract netloc from URL, stripping www."""
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc.removeprefix("www.")
    except Exception:
        return ""
