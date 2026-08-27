"""
Search Pipeline - provider-routed web search with fetch, clean, and rerank.

Pipeline:
1. Search - via WebSearchService (Brave or SearXNG, per configuration)
2. Fetch Full Pages - Get full article content
3. Clean Content - Extract main text, remove boilerplate
4. Rerank - Prioritize by freshness, authority, relevance

The runtime handles any downstream synthesis from the raw facts.
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..search import SearchProviderError, WebSearchService
from . import register_tool

log = logging.getLogger("novi.search")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str = ""
    freshness: str = ""
    score: float = 0.0
    full_text: str = ""


@dataclass
class SearchConfig:
    max_results: int = 10
    max_fetch: int = 3
    fetch_timeout: int = 15
    timelimit: str = None  # "d", "w", "m", "y" (normalized by the search layer)


def _get_config() -> SearchConfig:
    """Load search pipeline tuning from the configuration framework."""
    try:
        from ..configuration.bootstrap import get_configuration
        cfg = get_configuration()
        return SearchConfig(
            max_results=cfg.get("search.max_results", 10),
            max_fetch=cfg.get("search.max_fetch", 3),
            fetch_timeout=cfg.get("search.fetch_timeout", 15),
            timelimit=cfg.get("search.timelimit"),
        )
    except Exception as e:
        log.warning("Failed to load search config, using defaults: %s", e)
        return SearchConfig()


# ─── Phase 1: Provider-Routed Search ──────────────────────────────────────────


def _search_multi(query: str, config: SearchConfig) -> tuple[list[SearchResult], str | None]:
    """Search via the configured WebSearchService provider and deduplicate.

    Returns (results, error). Error is a user-safe message; provider failures
    are never hidden and never fall back to another backend.
    """
    if not query or not query.strip():
        return [], None

    try:
        response = WebSearchService().search_sync(
            query,
            max_results=config.max_results,
            time_range=config.timelimit,
        )
    except SearchProviderError as e:
        log.warning("web search failed via %s: %s", e.provider, e.message)
        return [], e.message
    except Exception as e:
        log.warning("web search failed: %s", e, exc_info=True)
        return [], f"Web search failed with an unexpected error."

    seen_urls = set()
    unique = []
    for r in response.results:
        if r.url in seen_urls:
            continue
        seen_urls.add(r.url)
        unique.append(SearchResult(
            title=r.title,
            url=r.url,
            snippet=r.snippet,
            source=response.provider or r.source,
            freshness=r.published_at or "",
        ))

    return unique[:config.max_results], None


# ─── Phase 3: Fetch Full Pages ────────────────────────────────────────────────

def _fetch_with_trafilatura(url: str, timeout: int = 15) -> str:
    """Fetch URL content using trafilatura for best extraction."""
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=True,
                favor_precision=False,
                favor_recall=True,
            )
            if text:
                return text[:8000]
    except Exception:
        pass
    return ""


def _fetch_with_fallback(url: str, timeout: int = 15) -> str:
    """Fetch URL with fallback to basic extraction."""
    text = _fetch_with_trafilatura(url, timeout)
    if text and len(text) > 200:
        return text

    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        return text[:8000] if text else ""
    except Exception:
        return ""


def fetch_pages(results: list[SearchResult], max_fetch: int = 3, timeout: int = 15) -> list[SearchResult]:
    """Fetch full page content for top results."""
    to_fetch = results[:max_fetch]

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_fetch_with_fallback, r.url, timeout): i
            for i, r in enumerate(to_fetch)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                text = future.result()
                to_fetch[idx].full_text = text
            except Exception as e:
                log.warning("Failed to fetch page %s: %s", to_fetch[idx].url, e)

    return results


# ─── Phase 4: Content Cleaning ────────────────────────────────────────────────

def clean_content(text: str) -> str:
    """Clean and structure content for LLM consumption."""
    if not text:
        return ""

    text = re.sub(r'\s+', ' ', text).strip()

    sentences = re.split(r'(?<=[.!?])\s+', text)
    important = []
    for s in sentences:
        if len(s) > 20:
            important.append(s)
        if len(important) >= 20:
            break

    return ' '.join(important)


# ─── Phase 5: Reranking ───────────────────────────────────────────────────────

def rerank_results(results: list[SearchResult], query: str) -> list[SearchResult]:
    """Rerank results by relevance, freshness, and authority."""
    query_words = set(query.lower().split())

    authority_domains = {
        "bbc.com", "reuters.com", "apnews.com", "cnn.com", "nytimes.com",
        "washingtonpost.com", "theguardian.com", "fifa.com", "uefa.com",
        "nba.com", "nfl.com", "mlb.com", "nhl.com", "espn.com",
        "github.com", "stackoverflow.com", "docs.python.org",
    }

    for r in results:
        score = 0.0

        title_words = set(r.title.lower().split())
        snippet_words = set(r.snippet.lower().split())
        overlap = len(query_words & (title_words | snippet_words))
        score += overlap * 2.0

        if r.freshness:
            try:
                from dateparser import parse as parse_date
                pub_date = parse_date(r.freshness)
                if pub_date:
                    days_old = (datetime.now() - pub_date).days
                    if days_old < 7:
                        score += 10.0
                    elif days_old < 30:
                        score += 5.0
                    elif days_old < 365:
                        score += 2.0
            except Exception:
                pass

        domain = ""
        try:
            from urllib.parse import urlparse
            domain = urlparse(r.url).netloc.lower()
        except Exception:
            pass

        for auth in authority_domains:
            if auth in domain:
                score += 5.0
                break

        if r.full_text and len(r.full_text) > 500:
            score += 3.0

        if r.snippet and len(r.snippet) > 100:
            score += 1.0

        r.score = score

    return sorted(results, key=lambda x: x.score, reverse=True)


# ─── Main Pipeline ─────────────────────────────────────────────────────────────

def run_search_pipeline(
    query: str,
    config: SearchConfig = None,
) -> dict:
    """
    Run the full search pipeline. Returns raw facts — no LLM synthesis.
    The caller (runtime) handles synthesis via its own generate() step.

    Returns dict with:
        - rewritten_query: The query used for search
        - results: List of SearchResult objects
        - facts: Raw fact string (snippets + full text)
        - sources: Formatted source list
    """
    if config is None:
        config = _get_config()

    rewritten = query

    results, search_err = _search_multi(rewritten, config)

    if search_err:
        return {
            "rewritten_query": rewritten,
            "results": [],
            "facts": f"Search service error: {search_err}",
            "sources": "",
            "search_error": search_err,
        }

    if not results:
        return {
            "rewritten_query": rewritten,
            "results": [],
            "facts": "No search results found.",
            "sources": "",
        }

    results = fetch_pages(results, config.max_fetch, config.fetch_timeout)

    results = rerank_results(results, query)

    for r in results:
        r.full_text = clean_content(r.full_text)

    parts = []
    for r in results[:5]:
        text = r.full_text[:2000] if r.full_text else r.snippet
        parts.append(f"[{r.title}] ({r.url}):\n{text}")
    facts = "\n\n".join(parts)

    sources = []
    for i, r in enumerate(results[:3], 1):
        sources.append(f"[{i}] {r.title}\n    {r.url}")

    return {
        "rewritten_query": rewritten,
        "results": results,
        "facts": facts,
        "sources": "\n".join(sources),
    }


# ─── Tool Registration ────────────────────────────────────────────────────────

@register_tool()
def web_search_pipeline(query: str, use_pipeline: bool = True) -> str:
    """
    Advanced web search with multi-source search.
    Returns raw facts — the runtime handles synthesis.

    Args:
        query: Search query
        use_pipeline: Use full pipeline (True) or simple search (False)

    Returns:
        Raw search results with sources
    """
    if not use_pipeline:
        config = _get_config()
        results, search_err = _search_multi(query, config)
        if search_err:
            return f"Search service error: {search_err}"
        if not results:
            return "No results found."
        lines = []
        for r in results[:5]:
            lines.append(f"- {r.title}: {r.snippet} ({r.url})")
        return "\n".join(lines)

    result = run_search_pipeline(query)

    output = f"**Facts:**\n{result['facts']}\n\n"
    if result["sources"]:
        output += f"**Sources:**\n{result['sources']}"
    return output
