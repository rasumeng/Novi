"""Evidence processing contracts (Phase 7).

Pure typed data contracts — immutable by construction. EvidenceProcessor
produces these; consumers never mutate them. All collections are tuples so
an EvidenceContext is fully hashable-safe and safe to share across subsystems.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable


@dataclass(frozen=True)
class Fact:
    """A single structured claim extracted from one or more sources."""

    statement: str
    confidence: float = 0.0
    sources: tuple[str, ...] = ()
    category: str = "fact"


@dataclass(frozen=True)
class Source:
    """Ranked source metadata attached to an EvidenceContext."""

    url: str = ""
    title: str = ""
    authority: float = 0.0
    relevance: float = 0.0
    freshness: datetime | None = None
    source_type: str = "web"


@dataclass(frozen=True)
class Conflict:
    """A detected disagreement between two or more facts."""

    statements: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    severity: str = "MINOR"  # MAJOR, MINOR
    resolution: str | None = None


@dataclass(frozen=True)
class EvidenceContext:
    """Structured evidence contract replacing flat ``merged_text``.

    ``fallback=True`` signals that extraction confidence was too low to trust
    structured facts — consumers should use ``summary`` (raw text) instead.
    """

    query: str
    facts: tuple[Fact, ...] = ()
    sources: tuple[Source, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    confidence: float = 0.0
    summary: str = ""
    fallback: bool = False


@dataclass(frozen=True)
class RankingConfig:
    """Weights for the pluggable SourceRanking scorers."""

    weights: dict[str, float] = field(
        default_factory=lambda: {
            "relevance": 1.0,
            "authority": 0.6,
            "freshness": 0.2,
            "source_type": 0.4,
            "consistency": 0.0,
        }
    )


@dataclass
class EvidenceConfig:
    """Processor configuration. Mutated only at construction time."""

    budget_chars: int = 4000
    min_fact_confidence: float = 0.5
    max_facts: int = 20
    ranking: RankingConfig | None = None
    extractor: Callable | None = None
    """Optional LLM classification hook:
    ``Callable[[list[str], str], list[float] | list[tuple[float, str]] | None]``
    ``(sentences, query) -> per-sentence (confidence[, category])`` or None
    to fall back to deterministic heuristics. Provider-agnostic by design."""

    def __post_init__(self) -> None:
        if self.ranking is None:
            self.ranking = RankingConfig()
