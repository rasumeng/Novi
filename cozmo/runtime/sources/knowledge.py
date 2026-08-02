"""KnowledgeRetrievalSource — adapter wrapping ``KnowledgeIndex.search``.

Phase 9 step 3. Wrapper only: owns store access, result translation, source
metadata, and error handling. No selection, no ranking, no merging.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..memory.knowledge_index import KnowledgeIndex

from ..evidence import RetrievalQuality
from ..retrieval_budget import ContextAllocation
from .base import RetrievedItem, RetrievalResult


class KnowledgeRetrievalSource:
    """Wraps ``KnowledgeIndex.search`` behind the ``RetrievalSource`` contract."""

    id = "knowledge"

    def __init__(self, knowledge_index: KnowledgeIndex):
        self._index = knowledge_index

    def retrieve(
        self,
        query: str,
        budget: ContextAllocation,
    ) -> RetrievalResult:
        try:
            results = self._index.search(
                query,
                k=budget.max_results,
                rerank=True,
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
            items.append(
                RetrievedItem(
                    id=str(r.get("id", "")),
                    text=r.get("text", ""),
                    source=self.id,
                    score=float(score),
                    metadata=dict(r.get("metadata", {})),
                )
            )
        return RetrievalResult(
            source=self.id,
            items=items,
            quality=RetrievalQuality.SUFFICIENT,
        )
