"""WorkspaceRetrievalSource — FILE source, metadata/path + content search.

Beta: no vector required. Extensible: add vector column later without
changing the interface — same retrieve(query, budget, project_id) contract.

Returns RetrievedItems with snippet + path citation, budgeted.
"""

from __future__ import annotations

from ..evidence import RetrievalQuality
from ..retrieval_budget import ContextAllocation
from .base import RetrievedItem, RetrievalResult


class WorkspaceRetrievalSource:
    """Workspace = FILE source — isolated by project_id, READ only."""

    id = "file"

    def __init__(self, workspace_service):
        self._svc = workspace_service

    def retrieve(
        self,
        query: str,
        budget: ContextAllocation,
        *,
        project_id: str | None = None,
    ) -> RetrievalResult:
        if not project_id:
            return RetrievalResult(source=self.id, quality=RetrievalQuality.EMPTY)
        try:
            hits = self._svc.search(project_id, query, k=min(budget.max_results or 5, 5))
        except Exception as e:
            return RetrievalResult(source=self.id, quality=RetrievalQuality.FAILED, error=str(e))
        if not hits:
            return RetrievalResult(source=self.id, quality=RetrievalQuality.EMPTY)
        # Budget: max_context_chars and max_results
        max_chars = getattr(budget, "max_context_chars", 0) or 6000
        items = []
        total_chars = 0
        for h in hits[: budget.max_results or 3]:
            snippet = h.get("snippet", "") or f"Matched file: {h['path']}"
            # try read full file for context if snippet empty, but budget
            if not snippet and self._svc:
                text = self._svc.read(project_id, h["path"], max_chars=1500)
                snippet = (text or "")[:500]
            if total_chars + len(snippet) > max_chars:
                break
            total_chars += len(snippet)
            items.append(
                RetrievedItem(
                    id=f"workspace:{h['path']}",
                    text=snippet,
                    source=self.id,
                    metadata={"path": h["path"], "project_id": project_id, "score": h.get("score", 0)},
                )
            )
        if not items:
            return RetrievalResult(source=self.id, quality=RetrievalQuality.EMPTY)
        return RetrievalResult(source=self.id, items=items, quality=RetrievalQuality.SUFFICIENT)
