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
    """Wraps ``KnowledgeIndex.search`` behind the ``RetrievalSource`` contract.

    Accepts either a ``KnowledgeIndex`` or a ``Brain`` (Architecture Rule #6):
    when a Brain is wired it owns the knowledge index, and the adapter asks the
    Brain for context. Both return the identical row shape, so translation is
    byte-for-byte.
    """

    id = "knowledge"

    def __init__(self, knowledge_index: "KnowledgeIndex"):
        self._index = knowledge_index

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
        return RetrievalResult(
            source=self.id,
            items=items,
            quality=RetrievalQuality.SUFFICIENT,
        )

    def _search(self, query: str, k: int) -> list:
        """Delegate to the wrapped store or the Brain's internal index."""
        from ...brain import Brain

        if isinstance(self._index, Brain):
            return self._index.retrieve_knowledge(query, k=k)
        return self._index.search(query, k=k, rerank=True)
