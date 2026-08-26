"""Reflection coordinator — bounded, deterministic, pure.

Phase F Step 3. Sits in front of ``reflect()``: picks a budgeted, oldest-first
set of candidate items, runs the existing corroboration/decide logic, and
returns a pure decision list the Brain applies. No storage imports, no side
effects, no background daemon (triggers are cheap predicates, the pass itself
is synchronous and serialized through the Brain).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Iterable, Optional

from ..types import KnowledgeItem, KnowledgeStatus
from . import promotion, verification

# Hard cap on items examined per pass — prevents unbounded scans. Oldest-first
# ordering makes the budget deterministic (§8.1, plan Step 3).
DEFAULT_BUDGET = 200

# Staleness horizon (design §6.2): a claim that has not been corroborated
# within this window may decay. Single constant, configurable — no adaptive
# heuristic.
DECAY_HORIZON_DAYS = 90

# Durable-tag exemption (design §7.2 rule 4): identity/preference/goal/skill
# items never decay — they are durable by policy.
_DURABLE_TAGS = frozenset({"preference", "goal", "skill", "identity"})

_PROCESSABLE = (KnowledgeStatus.CANDIDATE, KnowledgeStatus.CORROBORATED)


def pending_count(items: Iterable[KnowledgeItem]) -> int:
    """Number of items still eligible for promotion (not verified/superseded)."""
    return sum(1 for i in items if i.status in _PROCESSABLE)


def is_durable(item: KnowledgeItem) -> bool:
    """Durable by policy (design §7.2 rule 4): VERIFIED, or an identity/
    preference/goal/skill-tagged claim. Durable items never decay."""
    if item.status == KnowledgeStatus.VERIFIED:
        return True
    return any(t in _DURABLE_TAGS for t in item.tags)


def should_decay(
    item: KnowledgeItem,
    now: datetime,
    horizon_days: int = DECAY_HORIZON_DAYS,
) -> bool:
    """True when a claim is stale and non-durable → eligible for decay.

    Forgetting is priority reduction, never deletion: the item demotes to
    ``CANDIDATE`` (dropping out of default retrieval/projection) but remains
    queryable. Non-durable, uncorroborated claims older than the horizon
    qualify. VERIFIED and identity-tagged items never decay.
    """
    if is_durable(item):
        return False
    last = item.last_seen_at or item.created_at
    return last < (now - timedelta(days=horizon_days))


def decay_plan(
    items: Iterable[KnowledgeItem],
    now: datetime,
    horizon_days: int = DECAY_HORIZON_DAYS,
) -> list[KnowledgeItem]:
    """Pure list of items to demote to ``CANDIDATE`` (stale, non-durable)."""
    return [i for i in items if should_decay(i, now, horizon_days)]


def last_used(item: KnowledgeItem):
    return item.last_seen_at or item.created_at


def budgeted(
    items: Iterable[KnowledgeItem], budget: int = DEFAULT_BUDGET
) -> list[KnowledgeItem]:
    """Eligible items, oldest ``last_seen_at`` first, capped at ``budget``.

    Deterministic: same store state + budget ⇒ same subset. Non-processable
    items (verified/superseded) are excluded up front.
    """
    eligible = [i for i in items if i.status in _PROCESSABLE]
    eligible.sort(key=last_used)
    return eligible[:budget]


def should_reflect(
    pending: int,
    *,
    scenario_completed: bool = False,
    confirm_burst: bool = False,
    idle_pending: bool = False,
    on_demand: bool = False,
) -> bool:
    """Cheap gate: skip a costly pass when there is nothing to promote, or no
    trigger fired. A pass needs both pending work and at least one trigger.
    """
    if pending <= 0:
        return False
    return (
        scenario_completed
        or confirm_burst
        or idle_pending
        or on_demand
    )


def make_plan(
    items: Iterable[KnowledgeItem],
    *,
    find_related: Callable[[KnowledgeItem, list], Optional[KnowledgeItem]] | None = None,
    budget: int = DEFAULT_BUDGET,
) -> list[promotion.PromotionOutcome]:
    """Decide each budgeted candidate → a pure list of outcomes.

    ``find_related`` is the supersession-target selector, injected to avoid
    coupling to the Brain's private helper. The Brain applies the returned
    outcomes (status writes + edges) and builds the report.
    """
    chosen = budgeted(items, budget)
    all_items = list(items)
    outcomes: list[promotion.PromotionOutcome] = []
    for i, item in enumerate(chosen):
        count = verification.corroboration(chosen, i)
        confirmed = verification.is_confirm(item.content)
        existing = find_related(item, all_items) if find_related else None
        outcomes.append(
            promotion.decide(item, count, confirmed=confirmed, existing_verified=existing)
        )
    return outcomes