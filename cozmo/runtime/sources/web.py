"""WebRetrievalSource — adapter wrapping ``EvidenceCollector.collect``.

Phase 9 step 3. Wrapper only: owns pipeline access, result translation, source
metadata, and error handling. No selection, no ranking, no merging. Quality
grades and errors pass through unchanged from the underlying bundle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..evidence import EvidenceBundle, EvidenceCollector

from ..evidence import RetrievalQuality
from ..retrieval_budget import ContextAllocation
from .base import RetrievedItem, RetrievalResult


class WebRetrievalSource:
    """Wraps ``EvidenceCollector.collect`` behind the ``RetrievalSource`` contract."""

    id = "web"

    def __init__(self, collector: Optional[EvidenceCollector] = None):
        from ..evidence import EvidenceCollector

        self._collector = collector or EvidenceCollector()

    def collect(
        self,
        query: str,
        min_sources: int = 2,
    ) -> EvidenceBundle:
        """Bundle-form pipeline access for the executor's legacy web path.

        The executor's ``execute_search`` operates on the full
        ``EvidenceBundle`` (merged summary, coverage, retry); that data is not
        representable in the generic ``RetrievalResult``. This delegate keeps
        the collector (and thus pipeline access) owned by this adapter while
        preserving ``execute_search`` semantics byte-for-byte.
        """
        from ..evidence import EvidenceBundle

        return self._collector.collect(query, min_sources=min_sources)

    def retrieve(
        self,
        query: str,
        budget: ContextAllocation,
    ) -> RetrievalResult:
        try:
            bundle = self._collector.collect(query, min_sources=2)
        except Exception as e:
            return RetrievalResult(
                source=self.id,
                quality=RetrievalQuality.FAILED,
                error=str(e),
            )

        items = [
            RetrievedItem(
                id=r.url,
                text=r.full_text or r.snippet,
                source=self.id,
                score=float(r.score or 0.0),
                metadata={
                    "title": r.title,
                    "url": r.url,
                    "source": r.source,
                    "freshness": r.freshness,
                },
            )
            for r in bundle.results
        ]
        return RetrievalResult(
            source=self.id,
            items=items,
            quality=bundle.quality,
            error=bundle.error,
        )
