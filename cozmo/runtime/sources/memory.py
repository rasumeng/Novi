"""MemoryRetrievalSource — adapter wrapping ``MemoryManager.query``.

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
    """Wraps ``MemoryManager.query`` behind the ``RetrievalSource`` contract.

    Args:
        memory_manager: Underlying memory store.
        memory_types: Optional type filter forwarded to ``query`` (e.g.
            intent-derived). None queries all types.
        distance_threshold: Max cosine distance forwarded to ``query``.
    """

    id = "memory"

    def __init__(
        self,
        memory_manager: MemoryManager,
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
            results = self._memory.query(
                text=query,
                k=budget.max_results,
                distance_threshold=self._distance_threshold,
                memory_types=self._memory_types,
            )
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
