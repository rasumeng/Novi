"""Core retrieval contracts for the Phase 9 unified retrieval layer.

Establishes the shared contracts only — no implementations, no consumers.
Later Phase 9 migration steps wire concrete sources (memory, knowledge,
project, file, web) behind these contracts.

Design (docs/phase9-blueprint.md section 4):

- ``RetrievalSource`` — Protocol every knowledge source implements.
- ``RetrievedItem``  — single retrieved unit (memory, chunk, page, ...).
- ``RetrievalResult`` — one source's response to a ``retrieve()`` call.

A source owns store access, query shaping, and per-source relevance only.
Budgeting, cross-source ranking, merging, and context allocation belong to the
retrieval policy, not to sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..evidence import RetrievalQuality
from ..retrieval_budget import ContextAllocation


@runtime_checkable
class RetrievalSource(Protocol):
    """Common retrieval contract. Every source implements this.

    ``id`` identifies the source kind (e.g. "memory", "knowledge", "project",
    "file", "web"). Sources are stateless wrappers over their storage; they do
    not make selection, ranking, or budget decisions.
    """

    id: str

    def retrieve(
        self,
        query: str,
        budget: ContextAllocation,
    ) -> RetrievalResult:
        """Return this source's best items for ``query`` within ``budget``."""
        ...


@dataclass
class RetrievedItem:
    """Single unit of retrieved information from one source.

    ``score`` is normalized to ``0.0``-``1.0`` so results are comparable across
    heterogeneous sources. ``metadata`` carries source-specific detail
    (path/url/title/type/timestamp) without leaking storage formats.
    """

    id: str
    text: str
    source: str
    score: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class RetrievalResult:
    """One source's response to a ``retrieve()`` call.

    ``quality`` reuses the existing retrieval quality grades
    (``cozmo.runtime.evidence.RetrievalQuality``). ``error`` carries a
    source-typed failure description when retrieval could not complete.
    """

    source: str
    items: list[RetrievedItem] = field(default_factory=list)
    score: float = 0.0
    quality: RetrievalQuality = RetrievalQuality.EMPTY
    error: str | None = None


@dataclass(frozen=True)
class MergedRetrievalResult:
    """Cross-source merge of one retrieval plan's results.

    Produced by ``ResultMerger`` (cozmo.runtime.result_merger). Frozen so it
    can be shared across subsystems and consumed by the ContextRenderer and
    EvidenceProcessor without aliasing. ``items`` carry the normalized
    cross-source ranking; ``metrics`` exposes evaluable signals (per-source
    contribution, dedup counts, normalization method).
    """

    query: str
    items: tuple[RetrievedItem, ...] = ()
    source_results: tuple[RetrievalResult, ...] = ()
    quality: RetrievalQuality = RetrievalQuality.EMPTY
    allocation_used: ContextAllocation = field(default_factory=ContextAllocation)
    metrics: dict = field(default_factory=dict)
