"""SourceRanking — configurable, pluggable source ranking.

Default scorers read the structured ``Source`` fields (authority, relevance,
freshness, source_type). New scorers register globally via ``register_scorer``.
Ranking is deterministic given (sources, config).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from .context import RankingConfig, Source

log = logging.getLogger("cozmo.evidence.ranking")

# Registry of named scorer functions: fn(Source, RankingConfig) -> float
_SCORERS: dict[str, Callable[[Source, RankingConfig], float]] = {}

# source_type -> boost (relative authority of the source category)
_SOURCE_TYPE_BOOST = {
    "documentation": 0.9,
    "reference": 0.8,
    "forum": 0.5,
    "code": 0.6,
    "news": 0.4,
    "web": 0.3,
    "video": 0.1,
}


def _scorer_authority(source: Source, config: RankingConfig) -> float:
    return max(0.0, min(1.0, source.authority))


def _scorer_relevance(source: Source, config: RankingConfig) -> float:
    return max(0.0, min(1.0, source.relevance))


def _scorer_freshness(source: Source, config: RankingConfig) -> float:
    if source.freshness is None:
        return 0.0
    try:
        age_days = max(0, (datetime.now(timezone.utc) - source.freshness).total_seconds() / 86400)
    except Exception:  # noqa: BLE001 — never let a date glitch break ranking
        return 0.0
    return max(0.0, min(1.0, 1.0 - age_days / 365.0))


def _scorer_source_type(source: Source, config: RankingConfig) -> float:
    return _SOURCE_TYPE_BOOST.get(source.source_type, 0.3)


def _scorer_consistency(source: Source, config: RankingConfig) -> float:
    # Consistency is conflict-derived and computed at the EvidenceContext
    # level, not per-source here. Defaults to neutral.
    return 0.0


class SourceRanking:
    """Pluggable ranking over ``Source`` objects with weighted scorers."""

    def __init__(self) -> None:
        # Register defaults once
        if not _SCORERS:
            self.register_scorer("authority", _scorer_authority)
            self.register_scorer("relevance", _scorer_relevance)
            self.register_scorer("freshness", _scorer_freshness)
            self.register_scorer("source_type", _scorer_source_type)
            self.register_scorer("consistency", _scorer_consistency)

    def register_scorer(self, name: str, fn: Callable[[Source, RankingConfig], float]) -> None:
        """Register (or replace) a named scorer function."""
        if not name or name in {"weights"}:
            raise ValueError(f"invalid scorer name: {name!r}")
        _SCORERS[name] = fn

    def rank(self, sources: list[Source], config: RankingConfig) -> list[Source]:
        """Return sources sorted by weighted score, highest first."""
        if not sources:
            return []
        weights = config.weights or {}
        scored = []
        for s in sources:
            total = sum(
                weights.get(name, 0.0) * fn(s, config)
                for name, fn in _SCORERS.items()
            )
            scored.append((total, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored]
