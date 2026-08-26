"""ScenarioRetrievalSource — layered scenario tier adapter.

Phase E. Retrieval is layered: identity → project → scenario → knowledge →
conversation. This adapter surfaces the scenario tier of a retrieval plan —
knowledge scoped to the active scenario's neighborhood.

It never touches storage directly: it composes an injected base
``RetrievalSource`` (the brain-wired knowledge source) with a scenario scope,
then tags every item with the scenario provenance. Selection, ranking, and
merging belong to the policy / ResultMerger, not here.
"""

from __future__ import annotations

from ..evidence import RetrievalQuality
from ..retrieval_budget import ContextAllocation
from .base import RetrievalResult, RetrievedItem


class ScenarioRetrievalSource:
    """Surfaces a scenario's knowledge neighborhood behind ``RetrievalSource``.

    Args:
        base: the underlying knowledge source to query.
        scenario_id: the active scenario to scope to.
    """

    id = "scenario"

    def __init__(self, base, scenario_id: str | None = None):
        self._base = base
        self._scenario_id = scenario_id

    def retrieve(
        self,
        query: str,
        budget: ContextAllocation,
    ) -> RetrievalResult:
        if self._scenario_id is None:
            return RetrievalResult(source=self.id, quality=RetrievalQuality.EMPTY)
        try:
            result = self._base.retrieve(query, budget)
        except Exception as e:
            return RetrievalResult(
                source=self.id, quality=RetrievalQuality.FAILED, error=str(e)
            )

        items = []
        for item in result.items:
            meta = dict(item.metadata)
            meta["scenario_id"] = self._scenario_id
            items.append(
                RetrievedItem(
                    id=item.id,
                    text=item.text,
                    source=self.id,
                    score=item.score,
                    metadata=meta,
                )
            )
        return RetrievalResult(
            source=self.id,
            items=items,
            quality=result.quality,
            error=result.error,
        )