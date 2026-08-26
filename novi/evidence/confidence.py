"""ConfidenceAssessor — aggregate evidence confidence scoring.

Combines per-fact confidence, source quality (authority + relevance), and a
conflict penalty into a single [0, 1] score. Low scores signal untrustworthy
evidence to downstream consumers.
"""

from __future__ import annotations

from .context import Conflict, Fact, Source

MAJOR = "MAJOR"


class ConfidenceAssessor:
    def __init__(
        self,
        fact_weight: float = 0.7,
        source_weight: float = 0.3,
        major_penalty: float = 0.2,
        minor_penalty: float = 0.1,
    ):
        self._fact_weight = fact_weight
        self._source_weight = source_weight
        self._major_penalty = major_penalty
        self._minor_penalty = minor_penalty

    def assess(
        self,
        facts: tuple[Fact, ...] | list[Fact],
        sources: tuple[Source, ...] | list[Source],
        conflicts: tuple[Conflict, ...] | list[Conflict] = (),
    ) -> float:
        facts = list(facts)
        sources = list(sources)

        fact_score = (
            sum(f.confidence for f in facts) / len(facts) if facts else 0.0
        )
        source_score = (
            sum((s.authority + s.relevance) / 2.0 for s in sources) / len(sources)
            if sources
            else 0.0
        )

        if facts and sources:
            score = self._fact_weight * fact_score + self._source_weight * source_score
        elif facts:
            score = fact_score
        elif sources:
            score = 0.2 * source_score  # extraction failed — low trust
        else:
            return 0.0

        for c in conflicts:
            score -= self._major_penalty if c.severity == MAJOR else self._minor_penalty

        return max(0.0, min(1.0, round(score, 3)))
