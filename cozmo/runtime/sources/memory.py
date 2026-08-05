"""MemoryRetrievalSource — adapter wrapping the Brain's flat memory read.

Phase 9 step 3. Wrapper only: owns store access, result translation, source
metadata, and error handling. No selection, no ranking, no merging.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..memory.manager import MemoryManager

from ..evidence import RetrievalQuality
from ..retrieval_budget import ContextAllocation
from .base import RetrievedItem, RetrievalResult


class MemoryRetrievalSource:
    """Wraps flat memory retrieval behind the ``RetrievalSource`` contract.

    Accepts either a ``MemoryManager`` or a ``Brain`` (Architecture Rule #4):
    when a Brain is wired it owns memory, and the adapter asks the Brain via
    ``recall`` (layered resolver) and translates RecallItems back to flat rows.
    Both return the identical row shape, so translation is byte-for-byte.
    ``Brain.retrieve_memory_rows`` is retained only as the direct-call
    compatibility adapter for callers that bypass the source contract.

    Args:
        memory_manager: Underlying memory store (or Brain).
        memory_types: Optional type filter forwarded to the store (e.g.
            intent-derived). None queries all types.
        distance_threshold: Max cosine distance forwarded to the store.
    """

    id = "memory"

    def __init__(
        self,
        memory_manager: "MemoryManager",
        memory_types: Optional[list[str]] = None,
        distance_threshold: Optional[float] = 0.5,
    ):
        self._memory = memory_manager
        self._memory_types = memory_types
        self._distance_threshold = distance_threshold

    def retrieve(
        self,
        query: str,
        budget: ContextAllocation,
    ) -> RetrievalResult:
        try:
            results = self._query(query, budget.max_results)
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
            metadata = dict(r.get("metadata", {}))
            metadata["distance"] = r.get("distance", 0.5)
            items.append(
                RetrievedItem(
                    id=str(r.get("id", "")),
                    text=r.get("text", ""),
                    source=self.id,
                    score=float(score),
                    metadata=metadata,
                )
            )
        return RetrievalResult(
            source=self.id,
            items=items,
            quality=RetrievalQuality.SUFFICIENT,
        )

    def _query(self, text: str, k: int) -> list:
        """Delegate to the wrapped store or the Brain's layered recall.

        When a Brain is wired it owns memory; the adapter asks the Brain via
        ``recall`` (which walks the layered resolver) and translates each
        RecallItem back to a flat row so consumers are unchanged. Legacy
        MemoryManager remains the no-brain fallback.
        """
        from ...brain import Brain
        from ...brain.types import QueryContext

        if isinstance(self._memory, Brain):
            result = self._memory.recall(
                text,
                QueryContext(
                    top_k=k,
                    distance_threshold=self._distance_threshold,
                    memory_types=self._memory_types,
                ),
            )
            return [_recall_item_to_row(item) for item in result.items]
        return self._memory.query(
            text=text,
            k=k,
            distance_threshold=self._distance_threshold,
            memory_types=self._memory_types,
        )


def _recall_item_to_row(item) -> dict:
    """Translate a Brain RecallItem back to a flat memory row.

    ``Brain.recall`` returns RecallItems whose metadata already carries the
    store's flat keys for conversation-derived rows and adds ``kind``/``id``/
    ``scenario_id``/``tags`` for knowledge rows. Normalize to the row shape the
    runtime formatter and legacy consumers expect (id/text/score/distance/
    metadata).
    """
    meta = dict(item.metadata)
    if "distance" not in meta:
        meta["distance"] = max(0.0, 1.0 - float(item.score))
    return {
        "id": str(meta.get("id", "")),
        "text": item.text,
        "score": float(item.score),
        "distance": float(meta.get("distance", 0.5)),
        "metadata": meta,
    }
