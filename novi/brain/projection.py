"""Personal context projection — derived, read-only (Phase F Step 4).

Answers "what does Novi know about me" by grouping the existing identity-tagged
items by category and ranking them with the §5 lexicographic hierarchy:

    importance → confidence(status) → scenario relevance → recency tiebreak.

Pure: takes KnowledgeItem objects (no storage imports). Never invents
attributes — it groups stated items only, exposes each item's status and
confidence, and returns an empty projection when nothing identity-tagged exists.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from .types import KnowledgeItem, KnowledgeStatus

# Category → identity tags. An item is grouped under the category whose tag it
# carries. Anything untagged (e.g. composite summaries) is never projected as a
# personal attribute.
_CATEGORY_TAGS: dict[str, tuple[str, ...]] = {
    "preference": ("preference",),
    "goal": ("goal",),
    "skill": ("skill",),
    "project": ("project",),
    "event": ("event",),
    "relationship": ("relationship",),
    "identity": ("identity",),
}

# Importance buckets used to tier before confidence/recency (design §5.1).
_IMPORTANCE_HIGH = 0.66
_IMPORTANCE_MED = 0.33


def category_of(item: KnowledgeItem) -> Optional[str]:
    """Return the projected category for an identity-tagged item, else None."""
    for category, tags in _CATEGORY_TAGS.items():
        if any(t in item.tags for t in tags):
            return category
    return None


def _importance_tier(importance: float) -> int:
    if importance >= _IMPORTANCE_HIGH:
        return 2
    if importance >= _IMPORTANCE_MED:
        return 1
    return 0


def _confidence_tier(status: KnowledgeStatus) -> int:
    if status == KnowledgeStatus.VERIFIED:
        return 2
    if status == KnowledgeStatus.CORROBORATED:
        return 1
    return 0


def _last_used(item: KnowledgeItem) -> datetime:
    return item.last_seen_at or item.created_at


def _rank(item: KnowledgeItem, active: set) -> tuple:
    return (
        -_importance_tier(item.importance),
        -_confidence_tier(item.status),
        0 if item.scenario_id in active else 1,
        -_last_used(item).timestamp(),
    )


def _evidence_label(item: KnowledgeItem) -> str:
    if item.status == KnowledgeStatus.VERIFIED:
        return "verified"
    if item.status == KnowledgeStatus.CORROBORATED:
        return "corroborated"
    return "candidate"


def project(
    items: Iterable[KnowledgeItem],
    active_scenario_ids: Iterable[str] = (),
) -> dict:
    """Group identity-tagged items by category, ranked by §5 hierarchy.

    Excludes ``SUPERSEDED`` items. Returns a per-category mapping of ranked
    entries. Each entry mirrors the stated item (id, content, confidence,
    status, importance, last_seen_at, scenario_id) plus a transparent
    ``evidence`` label — never a synthesized attribute.
    """
    active = set(active_scenario_ids)
    buckets: dict[str, list[KnowledgeItem]] = {}
    for item in items:
        if item.status == KnowledgeStatus.SUPERSEDED:
            continue
        category = category_of(item)
        if category is None:
            continue
        buckets.setdefault(category, []).append(item)

    result: dict[str, list[dict]] = {}
    for category, bucket in buckets.items():
        ranked = sorted(bucket, key=lambda i: _rank(i, active))
        result[category] = [
            {
                "id": item.id,
                "content": item.content,
                "confidence": item.confidence,
                "status": item.status.value,
                "importance": item.importance,
                "last_seen_at": _last_used(item).isoformat(),
                "scenario_id": item.scenario_id,
                "active": item.scenario_id in active,
                "evidence": _evidence_label(item),
            }
            for item in ranked
        ]
    return result