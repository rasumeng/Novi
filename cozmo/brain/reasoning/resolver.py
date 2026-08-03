"""LayeredRetrievalResolver — top-down, relationship-constrained retrieval.

Phase E. Retrieval is not a single flat similarity search: the Brain resolves
context, loads the scenario, scores knowledge *within* that scenario's
neighborhood, and only when the sufficiency gate fails expands outward to
global knowledge, then raw conversations/memory.

    Brain.recall(query, context)
      1. resolve context      project → scenario        (Brain.resolve)
      2. load scenario        goal, status, summary, participants
      3. traverse edges       scenario → its knowledge  (derived_from / contains)
      4. score neighborhood   vector similarity within that subgraph
      5. sufficiency gate     conversations retrieved ONLY if steps 2-4 fail

This module is pure reasoning: it operates on Brain objects (Scenario,
RecallItem, RecallResult, QueryContext) and injected read callables only.
It never imports storage — the Brain (or the composition root) wires the
concrete stores behind those callables.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..types import QueryContext, RecallItem, RecallResult, Scenario

log = logging.getLogger("cozmo.brain.reasoning.resolver")

# Layers, in the order the resolver walks them (top-down).
_LAYER_ORDER = ("scenario", "knowledge", "conversation")


@dataclass(frozen=True)
class ResolvePlan:
    """What the resolver did, for tests and tracing.

    ``layers`` lists the layers actually consulted, in order.
    ``gate`` is the layer that satisfied sufficiency (or "conversation" when
    the gate failed and raw conversation/memory was used).
    """

    scenario_id: Optional[str]
    layers: tuple[str, ...]
    gate: str
    sufficiency: float
    scenario_items: int = 0
    scoped_knowledge: int = 0
    global_knowledge: int = 0
    conversation_items: int = 0


class LayeredRetrievalResolver:
    """Top-down layered retrieval with a sufficiency gate.

    Args:
        load_scenario: ``(scenario_id) -> Scenario | None``
        query_knowledge: ``(query, scenario_id | None, k, distance_threshold)
            -> list[dict]`` — scores knowledge within a scenario neighborhood
            when ``scenario_id`` is given, whole-graph otherwise.
        query_memory: ``(query, k, distance_threshold) -> list[dict]`` — raw
            conversation-derived retrieval used only when the gate fails.
        sufficiency: minimum best similarity score required to stop expanding.
        default_k: top-k fallback when the query context carries no limit.
    """

    def __init__(
        self,
        *,
        load_scenario: Callable[[str], Optional[Scenario]],
        query_knowledge: Callable[[str, Optional[str], int, Optional[float]], list[dict]],
        query_memory: Callable[[str, int, Optional[float]], list[dict]],
        sufficiency: float = 0.4,
        default_k: int = 5,
    ) -> None:
        self._load_scenario = load_scenario
        self._query_knowledge = query_knowledge
        self._query_memory = query_memory
        self._sufficiency = sufficiency
        self._default_k = default_k

    # ── public ──────────────────────────────────────────────────────────

    def recall(
        self,
        query: str,
        context: Optional[QueryContext] = None,
    ) -> RecallResult:
        """Walk the layers top-down; expand only when the gate demands it."""
        ctx = context or QueryContext()
        k = max(1, ctx.top_k or self._default_k)
        threshold = ctx.distance_threshold

        scenario_id = ctx.scenario_id
        scenario = self._load_scenario(scenario_id) if scenario_id else None
        plan = ResolvePlan(
            scenario_id=scenario_id,
            layers=(),
            gate="scenario",
            sufficiency=0.0,
        )

        items: list[RecallItem] = []
        if scenario is not None:
            summary = (scenario.summary or scenario.purpose or "").strip()
            if summary:
                items.append(
                    RecallItem(
                        text=summary,
                        score=1.0,
                        source="scenario",
                        metadata={"scenario_id": scenario.id, "kind": "scenario"},
                    )
                )
            plan = _replace_plan(
                plan, layers=("scenario",), scenario_items=1
            )

        scoped = self._query_knowledge(
            query, scenario_id=scenario_id, k=k, distance_threshold=threshold
        )
        best = _best_score(scoped)
        plan = _replace_plan(
            plan,
            layers=plan.layers + ("knowledge",),
            scoped_knowledge=len(scoped),
            sufficiency=best,
        )
        items.extend(_knowledge_items(scoped))
        gate = "knowledge" if best >= self._sufficiency else "knowledge_expand"

        if best < self._sufficiency:
            expanded = self._query_knowledge(
                query, scenario_id=None, k=k, distance_threshold=threshold
            )
            best_global = _best_score(expanded)
            plan = _replace_plan(
                plan,
                global_knowledge=len(expanded),
                sufficiency=best_global,
            )
            items.extend(_knowledge_items(expanded))
            gate = "knowledge" if best_global >= self._sufficiency else "conversation"

        if gate == "conversation":
            memory = self._safe_memory(query, k, threshold)
            plan = _replace_plan(
                plan,
                layers=plan.layers + ("conversation",),
                conversation_items=len(memory),
                gate="conversation",
            )
            items.extend(_memory_items(memory))
        else:
            plan = _replace_plan(plan, gate=gate)

        return RecallResult(
            query=query,
            items=tuple(_dedup_text(items)),
            metrics={
                "plan": plan,
                "layers": plan.layers,
                "gate": plan.gate,
                "sufficiency": plan.sufficiency,
                "scenario_id": plan.scenario_id,
            },
        )

    # ── helpers ─────────────────────────────────────────────────────────

    def _safe_memory(
        self, query: str, k: int, threshold: Optional[float]
    ) -> list[dict]:
        try:
            return list(self._query_memory(query, k, threshold))
        except Exception:
            log.warning("conversation/memory fallback failed", exc_info=True)
            return []


def _knowledge_items(rows: list[dict]) -> list[RecallItem]:
    return [
        RecallItem(
            text=r.get("text", ""),
            score=float(r.get("score", 0.0)),
            source="knowledge",
            metadata=dict(r.get("metadata", {})),
        )
        for r in rows
    ]


def _memory_items(rows: list[dict]) -> list[RecallItem]:
    return [
        RecallItem(
            text=str(r.get("text", "")),
            score=float(r.get("score", r.get("distance", 0.0))),
            source="memory",
            metadata=dict(r.get("metadata", {})),
        )
        for r in rows
    ]


def _best_score(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    return max(float(r.get("score", 0.0)) for r in rows)


def _dedup_text(items: list[RecallItem]) -> list[RecallItem]:
    """Drop duplicate content when the global-knowledge expansion overlaps the
    scenario-scoped neighborhood (first occurrence wins)."""
    seen: set[str] = set()
    out: list[RecallItem] = []
    for item in items:
        token = item.text.strip().lower()
        if token in seen:
            continue
        seen.add(token)
        out.append(item)
    return out


def _replace_plan(plan: ResolvePlan, **changes: Any) -> ResolvePlan:
    return ResolvePlan(**{**plan.__dict__, **changes})
