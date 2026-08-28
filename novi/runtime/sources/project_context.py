"""ProjectContextRetrievalSource — first-class Project sharedContext.

User-authored Project instructions are NOT learned memory. This source
returns the owning project's sharedContext verbatim, isolated by
project_id — never other projects' context or workspace content.

Vector retrieval is intentionally NOT used here. The text is small,
authored, and should be returned as-is when relevant, ranked and
budgeted by the Context Manager like any other source.
"""

from __future__ import annotations

from typing import Callable

from ..evidence import RetrievalQuality
from ..retrieval_budget import ContextAllocation
from .base import RetrievedItem, RetrievalResult


class ProjectContextRetrievalSource:
    """First-class Project context — isolated by project_id."""

    id = "project"

    def __init__(self, get_context: Callable[[str], str | None]):
        """
        Args:
            get_context: callable(project_id -> sharedContext or None).
                         Returns the raw sharedContext string for the
                         given project, or None if not found / empty.
        """
        self._get_context = get_context

    def retrieve(
        self,
        query: str,
        budget: ContextAllocation,
        *,
        project_id: str | None = None,
    ) -> RetrievalResult:
        # Isolation: no project_id → empty, never fallback to other projects
        if not project_id:
            return RetrievalResult(source=self.id, quality=RetrievalQuality.EMPTY)
        try:
            ctx = self._get_context(project_id)
        except Exception as e:
            return RetrievalResult(source=self.id, quality=RetrievalQuality.FAILED, error=str(e))
        if not ctx or not ctx.strip():
            return RetrievalResult(source=self.id, quality=RetrievalQuality.EMPTY)
        # Budget: respect max_context_chars if set (truncate, not reject)
        text = ctx.strip()
        max_chars = getattr(budget, "max_context_chars", 0) or 0
        if max_chars and len(text) > max_chars:
            text = text[:max_chars].rstrip() + "…"
        return RetrievalResult(
            source=self.id,
            items=[
                RetrievedItem(
                    id=f"project:{project_id}",
                    text=text,
                    source=self.id,
                    metadata={"project_id": project_id},
                )
            ],
            quality=RetrievalQuality.SUFFICIENT,
        )
