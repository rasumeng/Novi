"""Retrieval tiering — pure, lexicographic (§5 Phase F Step 5).

Replaces "most similar" with "important, verified, scenario-relevant." A pure
function reorders KnowledgeHit lists by the fixed priority hierarchy, treating
recency as a tiebreak only:

    importance bucket → confidence(status) bucket → scenario relevance
         → recency tiebreak

Bucketing is deterministic, no learned model. ``superseded`` items are
excluded unless explicitly requested.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from ..types import KnowledgeHit, KnowledgeStatus

# Shared importance buckets (mirror of projection._IMPORTANCE_*). One constants
# location for retrieval + projection.
_IMPORTANCE_HIGH = 0.66
_IMPORTANCE_MED = 0.33

# Durable-tag exemption (mirror of reflection._DURABLE_TAGS): decay only applies
# to non-identity, uncorroborated claims (design §7.2 rule 4).
_DURABLE_TAGS = frozenset({"preference", "goal", "skill", "identity"})

# Staleness horizon for the read-time archive filter (design §6.6 attributes
# this toggle to a threshold past which a decayed item is excluded).
DECAY_HORIZON_DAYS = 90


def bucket_importance(importance: float) -> int:
    if importance >= _IMPORTANCE_HIGH:
        return 2
    if importance >= _IMPORTANCE_MED:
        return 1
    return 0


def bucket_confidence(status: KnowledgeStatus) -> int:
    if status == KnowledgeStatus.VERIFIED:
        return 2
    if status == KnowledgeStatus.CORROBORATED:
        return 1
    return 0


def _last_used(item):
    return item.last_seen_at or item.created_at


def tier_key(hit: KnowledgeHit, active_scenario_ids: frozenset | set = ()) -> tuple:
    """Lexicographic sort key: importance → confidence → scenario → recency."""
    item = hit.item
    relevance = 0 if item.scenario_id in active_scenario_ids else 1
    recency = _last_used(item).timestamp() * -1 if _last_used(item) else 0.0
    return (
        -bucket_importance(item.importance),
        -bucket_confidence(item.status),
        relevance,
        recency,
    )


def tier_hits(
    hits: Iterable[KnowledgeHit],
    active_scenario_ids: frozenset | set = (),
    include_superseded: bool = False,
    include_archived: bool = False,
    now: datetime | None = None,
) -> list[KnowledgeHit]:
    """Order hits by the §5 hierarchy.

    Excludes ``SUPERSEDED`` items by default (preserved history, out of
    retrieval); set ``include_superseded`` to let them through. When ``now`` is
    given, decayed (stale, non-durable ``candidate``) items are archived out of
    default retrieval unless ``include_archived`` is set — read-path exclusion
    only, never a DELETE. Stable sort, so equal-tier hits keep their similarity
    order from the store.
    """
    active = set(active_scenario_ids)
    eligible = [
        h
        for h in hits
        if (include_superseded or h.item.status != KnowledgeStatus.SUPERSEDED)
        and (include_archived or not _is_archived(h.item, now))
    ]
    return sorted(eligible, key=lambda h: tier_key(h, active))


def _is_archived(item, now: datetime | None) -> bool:
    """Read-time archive check: a stale, non-durable candidate is out of default
    retrieval, but remains queryable on request."""
    if now is None:
        return False
    if item.status != KnowledgeStatus.CANDIDATE:
        return False
    if any(t in _DURABLE_TAGS for t in item.tags):
        return False
    last = item.last_seen_at or item.created_at
    return last < (now - timedelta(days=DECAY_HORIZON_DAYS))