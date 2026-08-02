"""FileRetrievalSource — NoOp placeholder adapter.

Full-text file indexing is deferred (PLAN.md 5.5 non-goals). This source
exposes the contract so sources can join/leave uniformly; it always returns an
empty result. Workspace file access remains explicit via tools
(read_file/grep/glob), not semantic retrieval.
"""

from __future__ import annotations

from ..evidence import RetrievalQuality
from ..retrieval_budget import ContextAllocation
from .base import RetrievalResult


class FileRetrievalSource:
    """NoOp retrieval source. Contract placeholder only."""

    id = "file"

    def retrieve(
        self,
        query: str,
        budget: ContextAllocation,
    ) -> RetrievalResult:
        return RetrievalResult(source=self.id, quality=RetrievalQuality.EMPTY)
