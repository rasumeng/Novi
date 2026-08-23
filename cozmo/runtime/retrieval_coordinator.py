"""RetrievalCoordinator — enforces budget, deduplicates, owns retrieval execution.

Separates:
  "Decide WHERE to retrieve"  (RetrievalPolicy)
from:
  "Execute retrieval"          (Runtime via RetrievalCoordinator)
from:
  "Synthesize from evidence"   (LLM)

The LLM does NOT decide "should I search again?" — the coordinator
intercepts web_search/web_fetch and blocks duplicates or budget overruns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

_SEARCH_TOOLS = frozenset({"web_search", "web_search_pipeline"})
_FETCH_TOOLS = frozenset({"web_fetch", "fetch_url", "webfetch"})
_WEB_TOOLS = _SEARCH_TOOLS | _FETCH_TOOLS

_SEARCH_STOPWORDS = frozenset({
    "what", "is", "the", "are", "how", "to", "in", "of", "for", "a", "an",
    "and", "or", "on", "at", "by", "with", "from", "do", "does", "can",
    "will", "would", "should", "could", "did", "has", "have", "had",
    "was", "were", "be", "been", "being", "get", "got", "am", "its",
    "it's", "its", "that", "this", "these", "those", "i", "my", "me",
    "you", "your", "we", "our", "they", "them", "their", "he", "she",
    "him", "her", "his", "tell", "give", "show", "find", "help",
    "when", "where", "why", "which", "who", "whom",
})


@dataclass
class RetrievalBudget:
    """Track web retrieval usage within one execution run."""
    max_web_searches: int = 1
    max_web_fetches: int = 1
    searches_used: int = 0
    fetches_used: int = 0

    @property
    def search_remaining(self) -> int:
        return max(0, self.max_web_searches - self.searches_used)

    @property
    def fetch_remaining(self) -> int:
        return max(0, self.max_web_fetches - self.fetches_used)

    @property
    def is_exhausted(self) -> bool:
        return self.search_remaining == 0 and self.fetch_remaining == 0


class RetrievalCoordinator:
    """Coordinates retrieval execution for one execution run.

    Intercepts web search/fetch tool calls to enforce budget and prevent
    duplicate queries. Maintains a cache of seen query → result mappings.
    """

    def __init__(self, budget: Optional[RetrievalBudget] = None):
        self.budget = budget or RetrievalBudget()
        self._search_cache: dict[str, str] = {}
        self._seen_queries: list[str] = []
        self.guidance_injected: bool = False

    # ── Classification helpers ────────────────────────────────────────────

    @staticmethod
    def is_search_tool(name: str) -> bool:
        return name in _SEARCH_TOOLS

    @staticmethod
    def is_fetch_tool(name: str) -> bool:
        return name in _FETCH_TOOLS

    @staticmethod
    def is_web_tool(name: str) -> bool:
        return name in _WEB_TOOLS

    # ── Term extraction (mirrors runtime._key_terms) ──────────────────────

    @staticmethod
    def _extract_terms(text: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z0-9]+", text.lower())
        return [t for t in tokens if t not in _SEARCH_STOPWORDS and len(t) > 1]

    # ── Query normalization ───────────────────────────────────────────────

    @staticmethod
    def _normalize_query(query: str) -> str:
        return query.lower().strip()

    # ── Duplicate detection ───────────────────────────────────────────────

    def _find_duplicate(self, query: str) -> Optional[str]:
        """Return cached result if `query` is a duplicate, else None.

        Uses two criteria (both must met):
        1. At least 2 distinct terms overlap
        2. Overlap ratio >= 0.5 (common / max of the two term sets)
        """
        normalized = self._normalize_query(query)

        if normalized in self._search_cache:
            return self._search_cache[normalized]

        query_terms = set(self._extract_terms(query))
        if not query_terms:
            return None

        for seen in self._seen_queries:
            seen_terms = set(self._extract_terms(seen))
            if not seen_terms:
                continue
            common = len(query_terms & seen_terms)
            if common < 2:
                continue
            overlap = common / max(len(query_terms), len(seen_terms))
            if overlap >= 0.5:
                return self._search_cache.get(self._normalize_query(seen))

        return None

    # ── Interception (called by runtime before tool execution) ────────────

    def intercept(self, name: str, args: dict) -> Optional[str]:
        """Return a replacement result string if the call should be blocked,
        or None to let the call proceed normally.

        Priority: duplicate detection > budget enforcement.
        """
        if name not in _WEB_TOOLS:
            return None

        if self.is_search_tool(name):
            query = args.get("query", "") if isinstance(args, dict) else str(args)
            cached = self._find_duplicate(query)
            if cached is not None:
                return (
                    f"[Previously searched. Same or similar query already retrieved. "
                    f"Results are in the grounding context above. Use them.]\n"
                    f"{cached}"
                )

        if self.is_search_tool(name):
            if not self.budget.search_remaining:
                return (
                    f"[Search budget used. Only {self.budget.max_web_searches} "
                    f"web search allowed per request. Use the evidence already retrieved.]"
                )

        if self.is_fetch_tool(name):
            if not self.budget.fetch_remaining:
                return (
                    f"[Fetch budget used. Only {self.budget.max_web_fetches} "
                    f"web fetch allowed per request. Use the evidence already retrieved.]"
                )

        return None

    # ── Direct-search accounting (Phase 8A) ──────────────────────────────

    def gate_search(self, query: str) -> bool:
        """Whether a DIRECT search path (not ToolExecutor-routed) may proceed.

        The research graph calls the retrieval pipeline directly instead of
        through ``ToolExecutor.execute``, so the Stage-1 intercept never sees
        it. This gate applies the same two rules here — budget enforcement,
        then duplicate detection — so every actual web search initiated by a
        graph is metered by this coordinator (the single budget authority).
        Returns True when the search may run.
        """
        if not self.budget.search_remaining:
            return False
        return self._find_duplicate(query) is None

    def record_search(self, query: str, result: str) -> None:
        """Account one completed direct search against the budget.

        Mirrors :meth:`record` for search tools but callable without a tool
        name/args envelope. Always increments ``searches_used``; only caches
        non-empty results so duplicate detection has material to work with.
        """
        self.budget.searches_used += 1
        normalized = self._normalize_query(query)
        self._seen_queries.append(normalized)
        if result:
            self._search_cache[normalized] = result

    # ── Recording (called by runtime after tool execution) ────────────────

    def record(self, name: str, args: dict, result: str):
        """Record a completed web tool call for budget/cache tracking."""
        if name not in _WEB_TOOLS:
            return

        if self.is_search_tool(name):
            query = args.get("query", "") if isinstance(args, dict) else str(args)
            normalized = self._normalize_query(query)
            self._search_cache[normalized] = result
            self._seen_queries.append(normalized)
            self.budget.searches_used += 1

        if self.is_fetch_tool(name):
            self.budget.fetches_used += 1

    # ── Cache seeding ─────────────────────────────────────────────────────

    def seed_cache(self, query: str, result: str):
        """Pre-populate cache with pre-loop retrieval results
        so the LLM's first web_search call is caught as duplicate."""
        if not query or not result:
            return
        normalized = self._normalize_query(query)
        if normalized not in self._search_cache:
            self._search_cache[normalized] = result
            self._seen_queries.append(normalized)
