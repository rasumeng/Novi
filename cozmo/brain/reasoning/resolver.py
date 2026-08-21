"""LayeredRetrievalResolver — top-down, relationship-constrained retrieval.

Phase E. Retrieval is not a single flat similarity search: the Brain resolves
context, loads the scenario, scores knowledge *within* that scenario's
neighborhood, and only when the sufficiency gate fails expands outward to
global knowledge, then WikiLink-relationship neighbors (M4), then raw
conversations/memory.

    Brain.recall(query, context)
      1. resolve context      project → scenario        (Brain.resolve)
      2. load scenario        goal, status, summary, participants
      3. traverse edges       scenario → its knowledge  (derived_from)
      4. score neighborhood   vector similarity within that subgraph
      5. sufficiency gate     global knowledge, then WikiLink neighbors,
                              then conversations — each ONLY on gate failure

M4 graph expansion: when both semantic stages fail the sufficiency gate, the
knowledge hits retrieved so far seed a bounded, deterministic WikiLink
neighborhood walk (``reasoning.expansion``). Discovered neighbors enter the
same ``RecallItem`` stream tagged ``origin="wikilink"``; when they yield at
least one new item the conversation/memory fallback is skipped. When the
expansion discovers nothing (no edges, dangling links, deleted targets,
callables unwired) behavior is byte-identical to pre-M4.

Scope semantics: expansion runs only after the gate has already failed twice,
the same regime as the existing global-knowledge expansion — cross-scenario
neighbors are therefore reachable exactly when global expansion would be.
Sufficient scoped retrieval never triggers any graph read.

This module is pure reasoning: it operates on Brain objects (Scenario,
RecallItem, RecallResult, QueryContext) and injected read callables only.
It never imports storage — the Brain (or the composition root) wires the
concrete stores behind those callables.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..types import KnowledgeHit, QueryContext, RecallItem, RecallResult, Scenario
from .expansion import ExpansionConfig, GraphNeighbor, traverse

log = logging.getLogger("cozmo.brain.reasoning.resolver")

# Layers, in the order the resolver walks them (top-down). "graph" sits between
# knowledge expansion and the conversation fallback (M4).
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
    graph_items: int = 0


class LayeredRetrievalResolver:
    """Top-down layered retrieval with a sufficiency gate.

    Args:
        load_scenario: ``(scenario_id) -> Scenario | None``
        query_knowledge: ``(query, scenario_id | None, k, distance_threshold)
            -> list[KnowledgeHit]`` — scores knowledge within a scenario
            neighborhood when ``scenario_id`` is given, whole-graph otherwise.
        query_memory: ``(query, k, distance_threshold) -> list[dict]`` — raw
            conversation-derived retrieval used only when the gate fails.
        neighborhood: ``(item_id) -> {"references": (...), "backlinks": (...)} | None``
            — WikiLink edge reader (M4). ``None`` disables graph expansion.
        fetch_knowledge: ``(item_ids) -> list[KnowledgeHit]`` — resolves
            durable neighbor ids to items (M4). Missing ids are skipped by the
            provider. ``None`` disables graph expansion.
        expansion: bounds for the neighborhood walk (depth / max neighbors /
            hop decay).
        sufficiency: minimum best similarity score required to stop expanding.
        default_k: top-k fallback when the query context carries no limit.
    """

    def __init__(
        self,
        *,
        load_scenario: Callable[[str], Optional[Scenario]],
        query_knowledge: Callable[[str, Optional[str], int, Optional[float]], list[KnowledgeHit]],
        query_memory: Callable[[str, int, Optional[float]], list[dict]],
        neighborhood: Optional[Callable[[str], Optional[dict]]] = None,
        fetch_knowledge: Optional[Callable[[list[str]], list[KnowledgeHit]]] = None,
        expansion: ExpansionConfig = ExpansionConfig(),
        sufficiency: float = 0.4,
        default_k: int = 5,
        tiered: bool = False,
    ) -> None:
        self._load_scenario = load_scenario
        self._query_knowledge = query_knowledge
        self._query_memory = query_memory
        self._neighborhood = neighborhood
        self._fetch_knowledge = fetch_knowledge
        self._expansion = expansion
        self._sufficiency = sufficiency
        self._default_k = default_k
        self._tiered = tiered

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
        knowledge_hits: list[KnowledgeHit] = []
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
        scoped = self._tier(scoped, scenario_id)
        best = _best_score(scoped)
        plan = _replace_plan(
            plan,
            layers=plan.layers + ("knowledge",),
            scoped_knowledge=len(scoped),
            sufficiency=best,
        )
        items.extend(_knowledge_items(scoped))
        knowledge_hits.extend(scoped)
        gate = "knowledge" if best >= self._sufficiency else "knowledge_expand"

        if best < self._sufficiency:
            expanded = self._query_knowledge(
                query, scenario_id=None, k=k, distance_threshold=threshold
            )
            expanded = self._tier(expanded, scenario_id)
            best_global = _best_score(expanded)
            plan = _replace_plan(
                plan,
                global_knowledge=len(expanded),
                sufficiency=best_global,
            )
            items.extend(_knowledge_items(expanded))
            knowledge_hits.extend(expanded)
            gate = "knowledge" if best_global >= self._sufficiency else "conversation"

        if gate == "conversation":
            graph = self._expand_graph(knowledge_hits, scenario_id)
            if graph:
                # M4: WikiLink neighbors satisfied the query — conversation
                # memory stays untouched. Zero discoveries fall through,
                # preserving pre-M4 behavior exactly.
                plan = _replace_plan(
                    plan,
                    layers=plan.layers + ("graph",),
                    graph_items=len(graph),
                    gate="graph",
                )
                items.extend(graph)
            else:
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

    def _expand_graph(
        self, seed_hits: list[KnowledgeHit], scenario_id: Optional[str]
    ) -> list[RecallItem]:
        """Bounded WikiLink neighborhood expansion over the semantic seeds.

        Seeds keep retrieval order (scoped before global), deduplicated by
        durable item id. Neighbor scores inherit the discovering parent's
        score, decayed per hop — a provenance signal, not a ranking change.
        Traversal crosses scenarios freely; each neighbor carries
        ``scenario_affinity`` ("same"/"cross") so the future ranking layer can
        prefer same-scenario neighbors without hard-blocking global knowledge
        (M4.1). Returns [] (never raises) when expansion is unwired or finds
        nothing.
        """
        if self._neighborhood is None or self._fetch_knowledge is None:
            return []
        seen: set[str] = set()
        seeds: list[str] = []
        for hit in seed_hits:
            hid = str(hit.item.id)
            if hid and hid not in seen:
                seen.add(hid)
                seeds.append(hid)
        if not seeds:
            return []

        neighbors = traverse(seeds, self._neighborhood, config=self._expansion)
        if not neighbors:
            return []

        try:
            hits = self._fetch_knowledge([n.item_id for n in neighbors])
        except Exception:
            log.warning("graph neighbor fetch failed", exc_info=True)
            return []
        by_id = {str(h.item.id): h for h in hits}

        scores: dict[str, float] = {
            str(hit.item.id): float(hit.score) for hit in seed_hits
        }
        out: list[RecallItem] = []
        for n in neighbors:
            hit = by_id.get(n.item_id)
            if hit is None:
                continue
            parent_score = scores.get(n.parent_id, 0.0)
            score = parent_score * self._expansion.hop_decay
            scores[n.item_id] = score
            out.append(_graph_item(hit, score, n, scenario_id))
        return out

    def _safe_memory(
        self, query: str, k: int, threshold: Optional[float]
    ) -> list[dict]:
        try:
            return list(self._query_memory(query, k, threshold))
        except Exception:
            log.warning("conversation/memory fallback failed", exc_info=True)
            return []

    def _tier(
        self, hits: list[KnowledgeHit], scenario_id: Optional[str]
    ) -> list[KnowledgeHit]:
        """Apply §5 lexicographic tiering when enabled (back-compat flag)."""
        if not self._tiered:
            return hits
        from .tiering import tier_hits

        active = {scenario_id} if scenario_id else set()
        return tier_hits(hits, active_scenario_ids=active)


def _knowledge_items(hits: list[KnowledgeHit]) -> list[RecallItem]:
    return [
        RecallItem(
            text=hit.item.content,
            score=float(hit.score),
            source="knowledge",
            metadata={
                "kind": "knowledge",
                "id": hit.item.id,
                "scenario_id": hit.item.scenario_id,
                "tags": hit.item.tags,
            },
        )
        for hit in hits
    ]


def _graph_item(
    hit: KnowledgeHit,
    score: float,
    neighbor: GraphNeighbor,
    scenario_id: Optional[str],
) -> RecallItem:
    """RecallItem for a graph-expanded neighbor (M4).

    ``origin="wikilink"`` distinguishes it from semantic results; semantic
    items carry no ``origin`` key. ``scenario_affinity`` is "same" only when
    an active scenario matches the neighbor's owning scenario — "cross"
    otherwise, including the no-active-scenario case (M4.1). It is advisory
    metadata for ranking; traversal itself never blocks on it.
    """
    affinity = (
        "same"
        if scenario_id and hit.item.scenario_id == scenario_id
        else "cross"
    )
    return RecallItem(
        text=hit.item.content,
        score=float(score),
        source="knowledge",
        metadata={
            "kind": "knowledge",
            "id": hit.item.id,
            "scenario_id": hit.item.scenario_id,
            "tags": hit.item.tags,
            "origin": "wikilink",
            "hops": neighbor.hops,
            "via": neighbor.via,
            "scenario_affinity": affinity,
        },
    )


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


def _best_score(hits: list[KnowledgeHit]) -> float:
    if not hits:
        return 0.0
    return max(float(hit.score) for hit in hits)


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
