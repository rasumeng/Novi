"""IdentityRetrievalSource — layered identity tier adapter.

Phase E. Retrieval is layered: identity → project → scenario → knowledge →
conversation. This adapter surfaces the identity tier — confirmed, high-level
traits about the user (preferences, goals, skills) — as a retrieval source.

It composes an injected base ``RetrievalSource`` (the brain-wired
identity/knowledge source) and relies on that source's metadata tags to pick
identity-bearing items. It never touches storage directly; selection, ranking,
and merging belong to the policy / ResultMerger.
"""

from __future__ import annotations

from ..evidence import RetrievalQuality
from ..retrieval_budget import ContextAllocation
from .base import RetrievalResult, RetrievedItem

# Soft tags that mark an item as part of the accumulated Identity layer.
_IDENTITY_TAGS = {"preference", "goal", "skill", "identity"}


class IdentityRetrievalSource:
    """Surfaces the user's accumulated identity behind ``RetrievalSource``.

    Args:
        base: the underlying knowledge/memory source to query.
    """

    id = "identity"

    def __init__(self, base):
        self._base = base

    def retrieve(
        self,
        query: str,
        budget: ContextAllocation,
    ) -> RetrievalResult:
        try:
            result = self._base.retrieve(query, budget)
        except Exception as e:
            return RetrievalResult(
                source=self.id, quality=RetrievalQuality.FAILED, error=str(e)
            )

        items = [
            RetrievedItem(
                id=item.id,
                text=item.text,
                source=self.id,
                score=item.score,
                metadata=dict(item.metadata),
            )
            for item in result.items
            if self._is_identity(item.metadata)
        ]
        if not items:
            return RetrievalResult(source=self.id, quality=RetrievalQuality.EMPTY)
        return RetrievalResult(
            source=self.id,
            items=items,
            quality=RetrievalQuality.SUFFICIENT,
        )

    @staticmethod
    def _is_identity(metadata: dict) -> bool:
        tags = metadata.get("tags")
        if not tags:
            return False
        return bool(_IDENTITY_TAGS.intersection(tags))