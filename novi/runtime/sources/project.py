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
    """Wraps ``ProjectIndex.query`` behind the ``RetrievalSource`` contract.

    Accepts either a ``ProjectIndex`` or a ``Brain`` (Architecture Rule #6):
    when a Brain is wired it owns the project index, and the adapter asks the
    Brain for context. Both produce the identical project string, so prompt
    context remains byte-for-byte.
    """

    id = "project"

    def __init__(self, project_index: "ProjectIndex"):
        self._project = project_index

    def retrieve(
        self,
        query: str,
        budget: ContextAllocation,
    ) -> RetrievalResult:
        try:
            text = self._query(query, budget.max_results)
        except Exception as e:
            return RetrievalResult(
                source=self.id,
                quality=RetrievalQuality.FAILED,
                error=str(e),
            )

        if not text:
            return RetrievalResult(source=self.id, quality=RetrievalQuality.EMPTY)

        metadata: dict = {}
        root = self._project_root()
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

    def _query(self, text: str, k: int) -> str:
        """Delegate to the wrapped store or the Brain's internal project."""
        from ...brain import Brain

        if isinstance(self._project, Brain):
            return self._project.retrieve_project(text, k=k)
        return self._project.query(text=text, k=k)

    def _project_root(self):
        from ...brain import Brain

        if isinstance(self._project, Brain):
            return self._project.project_root
        return getattr(self._project, "root", None)
