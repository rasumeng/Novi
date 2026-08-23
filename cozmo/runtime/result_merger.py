"""ResultMerger — deterministic cross-source merge, rank, and dedup.

Phase 9.5 step 1; extended in M5 into the single unified candidate pipeline:
filter → normalize → deduplicate (durable identity first) → rank → select.

Pure component: no runtime execution, no store access, no network. Consumes
per-source ``RetrievalResult`` objects and produces a frozen
``MergedRetrievalResult`` carrying a normalized cross-source ranking.

Ranking model (M5, documented):

    final = clamp( base + delta )

    base = α·source_prior(source) + β·positional_rank + γ·query_overlap

- ``source_prior`` — source-kind priority, default memory > identity >
  project > scenario > knowledge > web > file.
- ``positional_rank`` — within-source position (1 - index/k), scale-agnostic
  and robust to incomparable per-source similarity scores.
- ``query_overlap`` — fraction of query key terms present in the item text.

    delta = w_mem·memory_signal + w_aff·affinity_signal − w_hop·hop_penalty

- ``memory_signal`` — confidence × status weight (verified 1.0 / corroborated
  0.7 / candidate 0.4 / superseded filtered earlier). Rewards trustworthy
  structured-memory semantics without letting them dominate relevance.
- ``affinity_signal`` — 1.0 when ``scenario_affinity == "same"``, else 0.
  Same-scenario knowledge is preferred; cross-scenario stays neutral so
  explicitly-linked global knowledge is never discarded (spec M4 §4 / M5 §4).
- ``hop_penalty`` — max(0, hops−1) capped at 2, halved per hop: direct
  WikiLink neighbors compete freely, multi-hop neighbors are discounted.

The deltas are bounded adjustments on top of the Phase 9.5 formula (which the
existing test suite pins exactly): they can reorder near-ties and demote deep
graph hops, never outweigh a strong semantic hit. Weights are configurable via
``MergeWeights`` (base, must sum to 1) and ``RankAdjustments`` (deltas).
Scores clamp to [0,1]. Provenance (original score, merge score, source rank,
delta components, normalization method) is recorded in each item's metadata
so ranking is evaluable.

Deduplication (M5): primary key is durable identity — knowledge by Brain item
id (``metadata["item_id"]``, so all chunks of one note collapse into their
best-ranked representative), everything else by its deterministic result id
(chunk id / conversation id / turn id). Only candidates with no usable
identity fall back to the coordinator's term-overlap rule. Unrelated content
with similar text but different durable identities is kept apart.

Filtering (M5): superseded knowledge (``metadata["status"] == "superseded"``)
is dropped before scoring; historical items stay untouched in the Brain.

Context budget (``select()``): after ranking, walks items in rank order,
accumulating text until ``allocation.max_context_chars`` /
``allocation.max_results``, stopping early once the picked set covers ≥
``coverage_target`` of the query's key terms with at least ``min_items``
picked — minimum sufficient context, not maximum recall. Graph-expanded
candidates compete for budget like every other candidate.

Determinism: same inputs → identical output.
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

# Knowledge-status weights for the memory-semantics delta (M5). Superseded
# never reaches ranking — it is filtered before scoring.
_STATUS_WEIGHT = {
    "verified": 1.0,
    "corroborated": 0.7,
    "candidate": 0.4,
}

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

# Canonical origin per source when the item carries no explicit origin (M5
# candidate normalization). Only what the source actually is — never invented
# detail.
_SOURCE_ORIGIN = {
    "memory": "conversation",
    "project": "project",
    "scenario": "scenario",
    "identity": "identity",
    "knowledge": "semantic",
    "web": "web",
    "file": "file",
}


@dataclass(frozen=True)
class RankAdjustments:
    """Bounded post-base ranking deltas (M5). Each weight ∈ [0, 1].

    ``memory``     — confidence × status reward.
    ``affinity``   — same-scenario bonus (cross-scenario stays neutral).
    ``hop_penalty``— discount per WikiLink hop beyond the first, halved:
                     depth-1 → 0·w, depth-2 → 0.5·w, depth-3+ → capped at w.
    """

    memory: float = 0.05
    affinity: float = 0.06
    hop_penalty: float = 0.15

    def __post_init__(self) -> None:
        for name in ("memory", "affinity", "hop_penalty"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"RankAdjustments.{name} must be in [0,1], got {value}")


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
        adjustments: RankAdjustments | None = None,
    ):
        self._weights = weights or MergeWeights()
        self._prior = dict(_SOURCE_PRIOR)
        if source_prior:
            self._prior.update(source_prior)
        self._adjustments = adjustments or RankAdjustments()

    # ── public ──────────────────────────────────────────────────────────

    def merge(
        self,
        results: list[RetrievalResult],
        query: str,
        allocation: ContextAllocation,
    ) -> MergedRetrievalResult:
        """Merge per-source results into one ranked, deduplicated result.

        Pipeline: superseded filtering → scoring (base + deltas) → durable
        -identity-first deduplication → rank ordering.
        """
        candidates, filtered = self._filter(results)
        scored = self._score_items(candidates, query)
        merged, identity_removed = self._dedup(scored)
        items = tuple(sorted(merged, key=lambda it: it.score, reverse=True))
        metrics = self._metrics(results, len(scored), len(items))
        if filtered:
            metrics["filtered_superseded"] = filtered
        if identity_removed:
            metrics["identity_dedup_removed"] = identity_removed
        return MergedRetrievalResult(
            query=query,
            items=items,
            source_results=tuple(results),
            quality=self._quality(results, items),
            allocation_used=allocation,
            metrics=metrics,
        )

    def select(
        self,
        items: tuple[RetrievedItem, ...] | list[RetrievedItem],
        query: str,
        allocation: ContextAllocation,
        *,
        min_items: int = 2,
        coverage_target: float = 0.8,
    ) -> tuple[RetrievedItem, ...]:
        """Minimum-sufficient context selection over ranked items (M5 §10).

        Walks ``items`` in the given (rank) order, accumulating text until
        the allocation's char/item budget is reached. Stops early — before
        burning the whole budget — once at least ``min_items`` are picked and
        they cover ≥ ``coverage_target`` of the query's key terms. Items with
        no query-term coverage never satisfy the early stop on their own;
        with no key terms at all, sufficiency is unmeasurable and selection
        runs to ``min(max_results, budget)`` instead of guessing.

        Deterministic; returns a new tuple; never mutates inputs.
        """
        key_terms = self._query_terms(query)
        max_items = max(1, allocation.max_results)
        budget = max(0, allocation.max_context_chars)
        picked: list[RetrievedItem] = []
        chars = 0
        covered: set[str] = set()

        def sufficient() -> bool:
            if len(picked) < min_items or not key_terms:
                return False
            return len(covered) / len(key_terms) >= coverage_target

        for item in items:
            if len(picked) >= max_items:
                break
            cost = len(item.text or "")
            if picked and chars + cost > budget:
                break
            picked.append(item)
            chars += cost
            lower = (item.text or "").lower()
            covered.update(t for t in key_terms if t in lower)
            if sufficient():
                break

        return tuple(picked)

    # ── filtering ───────────────────────────────────────────────────────

    @staticmethod
    def _filter(
        results: list[RetrievalResult],
    ) -> tuple[list[RetrievalResult], int]:
        """Drop superseded knowledge candidates before ranking (M5).

        Filtering happens at the candidate boundary only — historical items
        stay untouched in the Brain (append-only semantics preserved).
        Returns (usable per-source results, superseded count).
        """
        kept: list[RetrievalResult] = []
        removed = 0
        for result in results:
            usable = [
                it for it in result.items
                if str(it.metadata.get("status", "")).lower() != "superseded"
            ]
            removed += len(result.items) - len(usable)
            if not usable:
                continue
            kept.append(replace(result, items=usable))
        return kept, removed

    # ── normalization ───────────────────────────────────────────────────

    def _score_items(
        self,
        results: list[RetrievalResult],
        query: str,
    ) -> list[RetrievedItem]:
        """Score every item: clamp(base + delta).

        base  = α·prior + β·positional + γ·overlap   (Phase 9.5, unchanged)
        delta = w_mem·memory + w_aff·affinity − w_hop·hops  (M5)

        Sources are processed in a deterministic order (priority descending,
        then original index) so canonical-item selection in dedup is stable
        regardless of the caller's result ordering. Each candidate's ``origin``
        is normalized from its source kind when absent (M5 §5).
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
                base = (
                    self._weights.alpha * prior
                    + self._weights.beta * positional
                    + self._weights.gamma * overlap
                )
                d_mem, d_aff, d_hop = self._deltas(item)
                score = self._clamp(base + d_mem + d_aff - d_hop)
                meta = dict(item.metadata)
                meta.setdefault(
                    "origin", _SOURCE_ORIGIN.get(result.source, "semantic")
                )
                meta["original_score"] = float(item.score)
                meta["merge_score"] = round(score, 6)
                meta["rank_base"] = round(base, 6)
                meta["delta_memory"] = round(d_mem, 6)
                meta["delta_affinity"] = round(d_aff, 6)
                meta["delta_hops"] = round(d_hop, 6)
                meta["source_rank"] = i + 1
                meta["source_prior"] = prior
                meta["normalization"] = _NORMALIZATION
                scored.append(replace(item, score=score, metadata=meta))
        return scored

    def _deltas(self, item: RetrievedItem) -> tuple[float, float, float]:
        """Bounded ranking deltas from candidate metadata (M5).

        Every component defaults to a neutral 0 when the metadata is absent —
        sources are never forced to manufacture semantics they do not have.
        """
        meta = item.metadata
        adj = self._adjustments

        confidence = meta.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        status_weight = _STATUS_WEIGHT.get(
            str(meta.get("status", "")).lower()
        )
        memory_signal = 0.0
        if confidence is not None or status_weight is not None:
            memory_signal = (confidence if confidence is not None else 0.0) * (
                status_weight if status_weight is not None else 1.0
            )
        d_mem = adj.memory * self._clamp(memory_signal)

        d_aff = (
            adj.affinity
            if str(meta.get("scenario_affinity", "")).lower() == "same"
            else 0.0
        )

        try:
            hops = int(meta.get("hops", 0))
        except (TypeError, ValueError):
            hops = 0
        hop_penalty = min(2, max(0, hops - 1)) / 2.0
        d_hop = adj.hop_penalty * hop_penalty

        return d_mem, d_aff, d_hop

    # ── deduplication ───────────────────────────────────────────────────

    def _dedup(self, scored: list[RetrievedItem]) -> tuple[list[RetrievedItem], int]:
        """Remove duplicates across sources, keeping attribution.

        M5 rule, strict identity-first (spec §6): candidates WITH a durable
        Brain identity (``metadata["item_id"]`` / ``kn-``-prefixed
        ``metadata["id"]``) are deduplicated solely by that identity.
        Similar text under different durable identities is related-but-
        distinct knowledge and is never collapsed (text similarity alone
        must not merge unrelated content).

        Candidates without a durable identity — legacy rows, web pages,
        project blobs — fall back to the Phase 9.5 term-overlap rule
        (mirrors RetrievalCoordinator._find_duplicate), which also merges
        the same deterministic chunk discovered through two paths. This
        keeps pre-M5 merge behavior byte-compatible for every input that
        lacks knowledge metadata.

        The surviving (canonical) item is the first encountered in
        deterministic source-priority order; later duplicates merge their
        source into the canonical item's ``attributed_sources`` metadata.
        Returns (kept items, duplicates removed count).
        """
        kept: list[RetrievedItem] = []
        by_identity: dict[str, int] = {}
        removed = 0
        for item in scored:
            key = self._identity_key(item)
            if key is not None:
                existing_index = by_identity.get(key)
                if existing_index is not None:
                    kept[existing_index] = self._attribute(kept[existing_index], item)
                    removed += 1
                    continue
                by_identity[key] = len(kept)
                kept.append(item)
                continue

            # No usable identity — content heuristic only.
            dup_index = self._find_duplicate(item, kept)
            if dup_index is None:
                kept.append(item)
                continue
            kept[dup_index] = self._attribute(kept[dup_index], item)
            removed += 1
        return kept, removed

    @staticmethod
    def _identity_key(item: RetrievedItem) -> Optional[str]:
        """Durable dedup identity for a candidate (M5 §6).

        Knowledge carries the Brain item id as ``metadata["item_id"]``
        (indexed chunks, graph neighbors) or as ``kn-``-prefixed
        ``metadata["id"]`` (layered-recall rows). Only that durable identity
        drives identity-based dedup; generic result ids are per-source labels,
        not durable identities. Returns None when no Brain identity exists.
        """
        durable = str(item.metadata.get("item_id") or "").strip()
        if not durable:
            meta_id = str(item.metadata.get("id") or "").strip()
            if meta_id.startswith("kn-"):
                durable = meta_id
        return f"know:{durable}" if durable else None

    @staticmethod
    def _attribute(existing: RetrievedItem, duplicate: RetrievedItem) -> RetrievedItem:
        """Merge ``duplicate``'s source attribution into ``existing``."""
        meta = dict(existing.metadata)
        attributed = set(meta.get("attributed_sources", []))
        attributed.add(existing.source)
        attributed.add(duplicate.source)
        meta["attributed_sources"] = sorted(attributed)
        return replace(existing, metadata=meta)

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
