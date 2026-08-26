"""UnifiedRetriever — M5 composition root for candidate assembly.

One pipeline for the retrieval sources that share candidate semantics:

    parallel candidate discovery  →  normalization  →  deduplication
    →  status filtering  →  unified ranking  →  context-budget selection

The component is pure orchestration: it owns no stores, runs no queries of
its own, and never ranks anything itself. Sources are injected as
``(name, RetrievalSource)`` bindings; merging/ranking/dedup/budget live in
the (single) ``ResultMerger``; the sufficiency gate lives where it was built
(M4) — inside the Brain-backed knowledge source, which expands through the
WikiLink neighborhood only when semantic scores fail the shared sufficiency
threshold. This module observes and records which gate branch fired instead
of re-implementing the threshold.

Source participation (spec M5 §11):

- ``knowledge``   — semantic chunks + gated WikiLink expansion (origin
  ``semantic`` / ``wikilink``).
- ``memory``      — conversation-derived context (origin ``conversation``).
- ``project``     — project file context (origin ``project``).
- ``scenario`` / ``identity`` — tier adapters may be bound like any source.
- ``web``         — deliberately NOT part of this pool: web/evidence results
  have fundamentally different semantics (freshness, external provenance,
  their own quality grading) and remain served by the evidence pipeline.
  Documented integration point for a later stage, not flattened in here.
- ``file``        — passive placeholder today; excluded on the same grounds.

No Brain wired? Callers simply bind fewer sources — every binding degrades
independently (a failed/empty source contributes nothing and never breaks
the run).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from typing import Iterable

from .retrieval_budget import ContextAllocation
from .result_merger import RankAdjustments, ResultMerger
from .sources.base import MergedRetrievalResult, RetrievedItem, RetrievalResult

log = logging.getLogger("novi.runtime.unified_retrieval")

# Sources whose candidates join the unified pool, in canonical consult order.
CANONICAL_ORDER = ("identity", "scenario", "knowledge", "memory", "project")


@dataclass(frozen=True)
class SourceBinding:
    """One retrieval source participating in a unified query."""

    name: str
    source: object  # RetrievalSource protocol

    def retrieve(self, query: str, allocation: ContextAllocation) -> RetrievalResult:
        return self.source.retrieve(query, allocation)


@dataclass(frozen=True)
class UnifiedOutcome:
    """Everything a consumer needs: ranked pool + minimum-sufficient pick."""

    merged: MergedRetrievalResult
    selected: tuple[RetrievedItem, ...] = ()
    metrics: dict = field(default_factory=dict)


class UnifiedRetriever:
    """Compose bound sources into one minimum-sufficient ranked context."""

    def __init__(
        self,
        *,
        merger: ResultMerger | None = None,
        adjustments: RankAdjustments | None = None,
    ):
        self._merger = merger or (
            ResultMerger(adjustments=adjustments) if adjustments else ResultMerger()
        )

    def retrieve(
        self,
        query: str,
        allocation: ContextAllocation,
        bindings: Iterable[SourceBinding],
    ) -> UnifiedOutcome:
        ordered = sorted(bindings, key=lambda b: _canonical_index(b.name))

        t0 = time.perf_counter()
        results: list[RetrievalResult] = []
        per_source: dict[str, dict] = {}
        for binding in ordered:
            ts = time.perf_counter()
            try:
                result = binding.retrieve(query, allocation)
            except Exception as e:
                log.warning("source %s failed: %s", binding.name, e)
                result = RetrievalResult(source=binding.name, error=str(e))
            latency_ms = round((time.perf_counter() - ts) * 1000.0, 3)
            results.append(result)
            per_source[binding.name] = {
                "quality": getattr(result.quality, "value", str(result.quality)),
                "items": len(result.items),
                "latency_ms": latency_ms,
            }
        discovery_ms = round((time.perf_counter() - t0) * 1000.0, 3)

        t1 = time.perf_counter()
        merged = self._merger.merge(results, query, allocation)
        ranking_ms = round((time.perf_counter() - t1) * 1000.0, 3)

        t2 = time.perf_counter()
        selected = self._merger.select(merged.items, query, allocation)
        selection_ms = round((time.perf_counter() - t2) * 1000.0, 3)

        # Attribution: how many post-dedup survivors each source can claim.
        contribution: dict[str, int] = {
            name: 0 for name in per_source
        }
        for item in merged.items:
            sources = set(item.metadata.get("attributed_sources", []))
            sources.add(item.source)
            for name in sources:
                if name in contribution:
                    contribution[name] += 1

        graph_candidates = sum(
            1
            for r in results
            for it in r.items
            if it.metadata.get("origin") == "wikilink"
        )
        best_semantic = max(
            (
                float(it.metadata.get("original_score", it.score))
                for r in results
                if r.source == "knowledge"
                for it in r.items
                if it.metadata.get("origin") != "wikilink"
            ),
            default=0.0,
        )

        extra = {
            "discovery_latency_ms": discovery_ms,
            "ranking_latency_ms": ranking_ms,
            "selection_latency_ms": selection_ms,
            "per_source": per_source,
            "source_contribution": contribution,
            "graph_candidates": graph_candidates,
            "gate": "expanded_graph" if graph_candidates else "sufficient_semantic",
            "best_semantic_score": round(best_semantic, 6),
            "selected_items": len(selected),
            "selected_chars": sum(len(it.text or "") for it in selected),
            "context_coverage": _coverage(selected, query),
        }
        metrics = {**merged.metrics, **extra}
        merged = replace(merged, metrics=metrics)
        return UnifiedOutcome(merged=merged, selected=selected)


def _canonical_index(name: str) -> int:
    try:
        return CANONICAL_ORDER.index(name)
    except ValueError:
        return len(CANONICAL_ORDER)


def _coverage(items: tuple[RetrievedItem, ...], query: str) -> float:
    """Fraction of query key terms present in the selected text (evaluable)."""
    terms = ResultMerger._query_terms(query)
    if not terms:
        return 1.0
    lower = " ".join((it.text or "").lower() for it in items)
    hits = sum(1 for t in terms if t in lower)
    return round(hits / len(terms), 4)
