"""ResultMerger — deterministic cross-source merge, rank, and dedup.

Phase 9.5 step 1. Pure component: no runtime execution, no store access, no
network. Consumes per-source ``RetrievalResult`` objects and produces a single
frozen ``MergedRetrievalResult`` carrying a normalized cross-source ranking.

Normalization (docs/phase9.5-blueprint.md section 2.3):

    final = α·source_prior(source) + β·positional_rank + γ·query_overlap

- ``source_prior`` — source-kind priority, default memory > project >
  knowledge > web.
- ``positional_rank`` — within-source position (1 - index/k), scale-agnostic
  and robust to incomparable per-source similarity scores.
- ``query_overlap`` — fraction of query key terms present in the item text.

Weights are configurable via ``MergeWeights`` (must sum to 1). Scores clamp to
[0,1]. Provenance (original score, merge score, source rank, normalization
method) is recorded in each item's metadata so ranking is evaluable.

Duplicates across sources are removed deterministically, mirroring the
retrieval coordinator's term-overlap rule (retrieval_coordinator.py); the
surviving item preserves attribution for every source that contributed the
same content.

Determinism: same inputs → identical ``MergedRetrievalResult``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Optional

from .evidence import RetrievalQuality
from .retrieval_budget import ContextAllocation
from .sources.base import MergedRetrievalResult, RetrievedItem, RetrievalResult

_WORD = re.compile(r"[a-z0-9]+")

# Stopwords used for both query-key-term extraction and dedup content tokens.
# Mirrors the retrieval coordinator's term set (retrieval_coordinator.py:24).
_STOPWORDS = frozenset({
    "what", "is", "the", "are", "how", "to", "in", "of", "for", "a", "an",
    "and", "or", "on", "at", "by", "with", "from", "do", "does", "can",
    "will", "would", "should", "could", "did", "has", "have", "had",
    "was", "were", "be", "been", "being", "get", "got", "am", "its",
    "it's", "its", "that", "this", "these", "those", "i", "my", "me",
    "you", "your", "we", "our", "they", "them", "their", "he", "she",
    "him", "her", "his", "tell", "give", "show", "find", "help",
    "when", "where", "why", "which", "who", "whom",
})

# Dedup similarity rule (mirrors RetrievalCoordinator._find_duplicate).
_MIN_SHARED_TERMS = 2
_DEDUP_OVERLAP_RATIO = 0.5

# Normalization method recorded on every merged item.
_NORMALIZATION = "weighted_source_position_overlap"

# Default source-kind priority. Layered tiers (Phase E) sit between project
# and knowledge, mirroring identity → project → scenario → knowledge.
_SOURCE_PRIOR = {
    "memory": 1.0,
    "identity": 0.9,
    "project": 0.8,
    "scenario": 0.7,
    "knowledge": 0.6,
    "web": 0.4,
    "file": 0.2,
}
_DEFAULT_PRIOR = 0.3


@dataclass(frozen=True)
class MergeWeights:
    """Weighting for cross-source score normalization. Must sum to 1.

    Defaults bias toward source priority and positional rank over raw query
    overlap; tune per evaluation (Phase 8/9.5 eval gates).
    """

    alpha: float = 0.4
    beta: float = 0.4
    gamma: float = 0.2

    def __post_init__(self) -> None:
        total = self.alpha + self.beta + self.gamma
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"MergeWeights must sum to 1, got {total}")


class ResultMerger:
    """Deterministic multi-source merge, rank, and dedup.

    Pure: no I/O, no stores, no network. Same inputs → same output. The
    executor (or any consumer) supplies per-source results; this component
    never retrieves.
    """

    def __init__(
        self,
        weights: MergeWeights | None = None,
        source_prior: Optional[dict[str, float]] = None,
    ):
        self._weights = weights or MergeWeights()
        self._prior = dict(_SOURCE_PRIOR)
        if source_prior:
            self._prior.update(source_prior)

    # ── public ──────────────────────────────────────────────────────────

    def merge(
        self,
        results: list[RetrievalResult],
        query: str,
        allocation: ContextAllocation,
    ) -> MergedRetrievalResult:
        """Merge per-source results into one ranked, deduplicated result."""
        scored = self._score_items(results, query)
        merged = self._dedup(scored)
        items = tuple(sorted(merged, key=lambda it: it.score, reverse=True))
        return MergedRetrievalResult(
            query=query,
            items=items,
            source_results=tuple(results),
            quality=self._quality(results, items),
            allocation_used=allocation,
            metrics=self._metrics(results, len(scored), len(items)),
        )

    # ── normalization ───────────────────────────────────────────────────

    def _score_items(
        self,
        results: list[RetrievalResult],
        query: str,
    ) -> list[RetrievedItem]:
        """Score every item: α·prior + β·positional + γ·overlap.

        Sources are processed in a deterministic order (priority descending,
        then original index) so canonical-item selection in dedup is stable
        regardless of the caller's result ordering.
        """
        key_terms = self._query_terms(query)
        ordered = sorted(
            enumerate(results),
            key=lambda t: (-self._prior.get(t[1].source, _DEFAULT_PRIOR), t[0]),
        )
        scored: list[RetrievedItem] = []
        for _, result in ordered:
            k = max(1, len(result.items))
            prior = self._prior.get(result.source, _DEFAULT_PRIOR)
            for i, item in enumerate(result.items):
                positional = 1.0 - (i / k)
                overlap = self._overlap(item.text, key_terms)
                raw = (
                    self._weights.alpha * prior
                    + self._weights.beta * positional
                    + self._weights.gamma * overlap
                )
                score = self._clamp(raw)
                meta = dict(item.metadata)
                meta["original_score"] = float(item.score)
                meta["merge_score"] = round(score, 6)
                meta["source_rank"] = i + 1
                meta["source_prior"] = prior
                meta["normalization"] = _NORMALIZATION
                scored.append(replace(item, score=score, metadata=meta))
        return scored

    # ── deduplication ───────────────────────────────────────────────────

    def _dedup(self, scored: list[RetrievedItem]) -> list[RetrievedItem]:
        """Remove near-identical content across sources, keeping attribution.

        The surviving (canonical) item is the first encountered in
        deterministic source-priority order; later duplicates merge their
        source into the canonical item's ``attributed_sources`` metadata.
        """
        kept: list[RetrievedItem] = []
        for item in scored:
            dup_index = self._find_duplicate(item, kept)
            if dup_index is None:
                kept.append(item)
                continue
            existing = kept[dup_index]
            meta = dict(existing.metadata)
            attributed = set(meta.get("attributed_sources", []))
            attributed.add(existing.source)
            attributed.add(item.source)
            meta["attributed_sources"] = sorted(attributed)
            kept[dup_index] = replace(existing, metadata=meta)
        return kept

    @staticmethod
    def _find_duplicate(
        item: RetrievedItem,
        kept: list[RetrievedItem],
    ) -> Optional[int]:
        """Return the kept index whose content matches ``item``, else None.

        Mirrors RetrievalCoordinator._find_duplicate: at least 2 shared terms
        and an overlap ratio >= 0.5 against the larger token set.
        """
        terms = _content_terms(item.text)
        if not terms:
            return None
        for i, other in enumerate(kept):
            other_terms = _content_terms(other.text)
            if not other_terms:
                continue
            common = len(terms & other_terms)
            if common < _MIN_SHARED_TERMS:
                continue
            overlap = common / max(len(terms), len(other_terms))
            if overlap >= _DEDUP_OVERLAP_RATIO:
                return i
        return None

    # ── quality ─────────────────────────────────────────────────────────

    @staticmethod
    def _quality(
        results: list[RetrievalResult],
        items: tuple[RetrievedItem, ...],
    ) -> RetrievalQuality:
        """Grade the merge: SUFFICIENT / FAILED / EMPTY.

        - usable items → SUFFICIENT
        - no items, every consulted source failed → FAILED
        - otherwise → EMPTY
        """
        if items:
            return RetrievalQuality.SUFFICIENT
        if results and all(r.quality == RetrievalQuality.FAILED for r in results):
            return RetrievalQuality.FAILED
        return RetrievalQuality.EMPTY

    # ── metrics ─────────────────────────────────────────────────────────

    @staticmethod
    def _metrics(
        results: list[RetrievalResult],
        before_dedup: int,
        after_dedup: int,
    ) -> dict:
        return {
            "sources_consulted": sorted({r.source for r in results}),
            "sources_with_results": sorted({r.source for r in results if r.items}),
            "items_total_before_dedup": before_dedup,
            "items_after_dedup": after_dedup,
            "dedup_removed": before_dedup - after_dedup,
            "normalization": _NORMALIZATION,
        }

    # ── term helpers (mirror RetrievalExecutor.extract_key_terms) ───────

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        tokens = _WORD.findall(query.lower())
        return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]

    @staticmethod
    def _overlap(text: str, key_terms: list[str]) -> float:
        """Fraction of query key terms present in the item text."""
        if not key_terms:
            return 1.0
        lower = text.lower()
        hits = sum(1 for t in key_terms if t in lower)
        return hits / len(key_terms)

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, value))


def _content_terms(text: str) -> set[str]:
    """Stopword-filtered content tokens for dedup similarity."""
    tokens = _WORD.findall(text.lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 1}
