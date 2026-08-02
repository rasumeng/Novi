"""ProjectRetrievalSource — adapter wrapping ``ProjectIndex.query``.

Phase 9 step 3. Wrapper only: owns store access, result translation, source
metadata, and error handling. No selection, no ranking, no merging.

Project-aware retrieval is deferred (PLAN.md 5.5 non-goals); this adapter
merely exposes the existing inline project-index query behind the contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..code_indexer import ProjectIndex

from ..evidence import RetrievalQuality
from ..retrieval_budget import ContextAllocation
from .base import RetrievedItem, RetrievalResult


class ProjectRetrievalSource:
    """Wraps ``ProjectIndex.query`` behind the ``RetrievalSource`` contract."""

    id = "project"

    def __init__(self, project_index: ProjectIndex):
        self._project = project_index

    def retrieve(
        self,
        query: str,
        budget: ContextAllocation,
    ) -> RetrievalResult:
        try:
            text = self._project.query(text=query, k=budget.max_results)
        except Exception as e:
            return RetrievalResult(
                source=self.id,
                quality=RetrievalQuality.FAILED,
                error=str(e),
            )

        if not text:
            return RetrievalResult(source=self.id, quality=RetrievalQuality.EMPTY)

        metadata: dict = {}
        root = getattr(self._project, "root", None)
        if root:
            metadata["project_root"] = str(root)

        return RetrievalResult(
            source=self.id,
            items=[
                RetrievedItem(
                    id="project",
                    text=text,
                    source=self.id,
                    metadata=metadata,
                )
            ],
            quality=RetrievalQuality.SUFFICIENT,
        )
