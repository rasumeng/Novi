"""KnowledgeRetrievalSource — adapter wrapping ``KnowledgeIndex.search``.

Phase 9 step 3. Wrapper only: owns store access, result translation, source
metadata, and error handling. No selection, no ranking, no merging.

M4: when the wrapped store is a Brain (Architecture Rule #6), the source can
additionally expand weak semantic results through the Brain's WikiLink
neighborhood (``Brain.neighborhood`` + ``Brain.knowledge_items``). Expansion is
gated by the same sufficiency threshold the layered resolver uses: strong
semantic retrieval never triggers graph reads. Expanded neighbors enter the
existing ``RetrievedItem`` stream tagged ``metadata["origin"] = "wikilink"``
(semantic items carry no ``origin``), deduplicated by durable Brain item id.
Plain ``KnowledgeIndex`` stores have no relationship graph, so expansion is
unavailable and behavior is unchanged for them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..memory.knowledge_index import KnowledgeIndex

from ..evidence import RetrievalQuality
from ..retrieval_budget import ContextAllocation
from .base import RetrievedItem, RetrievalResult

log = logging.getLogger("cozmo.runtime.sources.knowledge")

# Durable-id metadata key written by KnowledgeIndex.index_file (M4 bridge).
_ITEM_ID_KEY = "item_id"


class KnowledgeRetrievalSource:
    """Wraps ``KnowledgeIndex.search`` behind the ``RetrievalSource`` contract.

    Accepts either a ``KnowledgeIndex`` or a ``Brain`` (Architecture Rule #6):
    when a Brain is wired it owns the knowledge index, and the adapter asks the
    Brain for context. Both return the identical row shape, so translation is
    byte-for-byte.

    Args:
        knowledge_index: the wrapped ``KnowledgeIndex`` or ``Brain``.
        expand_related: enable gated WikiLink neighborhood expansion
            (Brain-backed sources only; default on).
        sufficiency_threshold: best-score gate below which graph expansion
            runs (mirrors LayeredRetrievalResolver.sufficiency).
        expansion_depth: maximum hops from a seed result.
        max_neighbors: hard cap on added neighbors per retrieve() call.
    """

    id = "knowledge"

    def __init__(
        self,
        knowledge_index: "KnowledgeIndex",
        *,
        expand_related: bool = True,
        sufficiency_threshold: float = 0.4,
        expansion_depth: int = 1,
        max_neighbors: int = 8,
    ):
        self._index = knowledge_index
        self._expand_related = expand_related
        self._sufficiency = sufficiency_threshold
        self._depth = max(1, expansion_depth)
        self._max_neighbors = max(1, max_neighbors)

    def retrieve(
        self,
        query: str,
        budget: ContextAllocation,
    ) -> RetrievalResult:
        try:
            results = self._search(query, budget.max_results)
        except Exception as e:
            return RetrievalResult(
                source=self.id,
                quality=RetrievalQuality.FAILED,
                error=str(e),
            )

        if not results:
            return RetrievalResult(source=self.id, quality=RetrievalQuality.EMPTY)

        items = []
        for r in results:
            score = r.get("score")
            if score is None:
                score = 1.0 - r.get("distance", 0.5)
            items.append(
                RetrievedItem(
                    id=str(r.get("id", "")),
                    text=r.get("text", ""),
                    source=self.id,
                    score=float(score),
                    metadata=dict(r.get("metadata", {})),
                )
            )

        items = self._maybe_expand(items)

        return RetrievalResult(
            source=self.id,
            items=items,
            quality=RetrievalQuality.SUFFICIENT,
        )

    # ── M4: WikiLink neighborhood expansion ────────────────────────────

    def _maybe_expand(self, items: list[RetrievedItem]) -> list[RetrievedItem]:
        """Append graph neighbors when semantic retrieval is insufficient.

        Never raises and never removes or reorders semantic results: any
        expansion failure logs and returns the semantic items untouched.
        """
        if not (self._expand_related and self._is_brain_backed()):
            return items
        best = max(item.score for item in items)
        if best >= self._sufficiency:
            return items

        try:
            from ...brain.reasoning.expansion import ExpansionConfig, traverse

            config = ExpansionConfig(
                depth=self._depth, max_neighbors=self._max_neighbors
            )
            seeds, seen_ids, seen_texts = [], set(), set()
            for item in items:
                durable = str(item.metadata.get(_ITEM_ID_KEY) or "")
                token = _norm_text(item.text)
                if durable and durable not in seen_ids:
                    seen_ids.add(durable)
                    seeds.append(durable)
                if token:
                    seen_texts.add(token)

            neighbors = traverse(seeds, self._index.neighborhood, config=config)
            if not neighbors:
                return items

            fetched = {
                item.id: item
                for item in self._index.knowledge_items(
                    [n.item_id for n in neighbors]
                )
            }

            seed_scores: dict[str, float] = {}
            for item in items:
                durable = str(item.metadata.get(_ITEM_ID_KEY) or "")
                if durable:
                    seed_scores[durable] = item.score

            added: list[RetrievedItem] = []
            for n in neighbors:
                known = fetched.get(n.item_id)
                if known is None:
                    continue
                if n.item_id in seen_ids:
                    continue
                token = _norm_text(getattr(known, "content", ""))
                if token and token in seen_texts:
                    continue
                parent_score = seed_scores.get(n.parent_id, best)
                score = parent_score * config.hop_decay
                seed_scores[n.item_id] = score
                seen_ids.add(n.item_id)
                if token:
                    seen_texts.add(token)
                added.append(self._neighbor_item(known, score, n))
            return items + added
        except Exception:
            log.warning("wikilink expansion failed; returning semantic only",
                        exc_info=True)
            return items

    @staticmethod
    def _neighbor_item(known, score: float, neighbor) -> RetrievedItem:
        """Translate a fetched neighbor into the existing RetrievedItem shape."""
        tags = list(getattr(known, "tags", ()) or ())
        return RetrievedItem(
            id=str(known.id),
            text=getattr(known, "content", ""),
            source="knowledge",
            score=float(score),
            metadata={
                "type": "knowledge",
                "tags": tags,
                "scenario_id": getattr(known, "scenario_id", None),
                "origin": "wikilink",
                "hops": neighbor.hops,
                "via": neighbor.via,
            },
        )

    def _is_brain_backed(self) -> bool:
        from ...brain import Brain

        return isinstance(self._index, Brain)

    # ── existing behavior ──────────────────────────────────────────────

    def _search(self, query: str, k: int) -> list:
        """Delegate to the wrapped store or the Brain's internal index."""
        from ...brain import Brain

        if isinstance(self._index, Brain):
            return self._index.retrieve_knowledge(query, k=k)
        return self._index.search(query, k=k, rerank=True)


def _norm_text(text: str) -> str:
    """Content-level dedup token (same normalization the resolver uses)."""
    return " ".join(str(text or "").strip().lower().split())
