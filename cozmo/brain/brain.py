"""Brain — cognition facade over Cozmo's knowledge system.

Phase B established the write pipeline:
    Runtime reports events.
    Brain decides persistence.
    Storage executes persistence.

Phase C adds extraction: observe() persists the turn, emits
ConversationObserved, then runs extraction on a buffered batch (legacy 5-turn
cadence). Extracted claims become atomic KnowledgeItems, a scenario is created
or updated (1:1 with the conversation), the conversation is linked, and
KnowledgeExtracted is emitted. All optional: without an extractor/layers, the
Brain behaves exactly as Phase B.

The legacy MemoryManager write pipeline is no longer called by observe(); it
survives only as the brain=None fallback in the runtime and WebUI, removed in
Phase G.

Architecture Rule #5 (knowledge is append-only):
    KnowledgeItems are immutable historical observations. Their
    content/form/created_at never change after creation; confidence advances
    only through the monotonic promotion monitor. Change is represented as a
    new observation linked to the old one via a typed edge (supersedes), so
    the store keeps a full history and never mutates in place. Current state
    is always derived — the newest verified, non-superseded observation —
    never a claim of absolute truth.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from ..memory.manager import get_memory_manager
from .events import (
    CONVERSATION_OBSERVED,
    KNOWLEDGE_EXTRACTED,
    ConversationObserved,
    KnowledgeExtracted,
)
from .storage.base import ConversationStore
from .types import (
    ContextResolution,
    EdgeKind,
    QueryContext,
    RecallItem,
    RecallResult,
    ReflectionReport,
    Relationship,
    ScenarioStatus,
    Turn,
)

log = logging.getLogger("cozmo.brain")

__all__ = ["Brain", "set_brain", "get_brain"]

_brain_instance: "Brain | None" = None


def set_brain(brain: "Brain | None"):
    """Register the active Brain for tool access (mirrors MemoryManager pattern)."""
    global _brain_instance
    _brain_instance = brain


def get_brain() -> "Brain | None":
    return _brain_instance

# Soft tags that mark an item as part of the accumulated Identity layer.
_IDENTITY_TAGS = frozenset({"preference", "goal", "skill", "identity"})


class Brain:
    """Facade exposing observe / recall / learn / resolve / reflect.

    Args:
        memory: MemoryManager-like component. Defaults to the process-global
            manager registered via set_memory_manager.
        project_index: ProjectIndex-like component used to resolve the active
            project and retrieve project context. No process-global exists for
            it; inject it.
        knowledge_index: KnowledgeIndex-like component used to search the
            file-backed knowledge base. Defaults to the process-global index
            registered via init_knowledge_index.
        conversation_store: raw conversation persistence behind the
            ConversationStore protocol. Never generates identifiers — the
            Brain owns conversation identity.
        event_bus: object with ``emit(event_type, **data)``. Brain domain
            events are emitted here after state is persisted.
        extractor: reasoning KnowledgeExtractor (optional). When None (with
            the layers), observe() only persists + emits.
        knowledge_layer: knowledge domain manager (optional).
        scenario_layer: scenario domain manager (optional).
        relationship_store: typed edge store (optional). When present, extracted
            knowledge items get derived_from (conversation) and observed_in
            (scenario) provenance edges.
        extract_every: number of buffered turns before extraction runs
            (legacy 5-turn cadence).
    """

    def __init__(
        self,
        *,
        memory: Any = None,
        project_index: Any = None,
        knowledge_index: Any = None,
        conversation_store: Optional[ConversationStore] = None,
        event_bus: Any = None,
        extractor: Any = None,
        knowledge_layer: Any = None,
        scenario_layer: Any = None,
        relationship_store: Any = None,
        resolver: Any = None,
        extract_every: int = 5,
        tiered_resolver: bool = True,
    ) -> None:
        self._memory = memory
        self._project_index = project_index
        self._knowledge_index = knowledge_index
        self._conversation_store = conversation_store
        self._event_bus = event_bus
        self._extractor = extractor
        self._knowledge_layer = knowledge_layer
        self._scenario_layer = scenario_layer
        self._relationship_store = relationship_store
        self._resolver = resolver
        self._extract_every = max(1, extract_every)
        self._tiered_resolver = tiered_resolver
        self._pending_turns: dict[str, list[Turn]] = {}

    def observe(self, turn: Turn) -> None:
        """Capture a completed turn.

        Order: conversation persisted → ConversationObserved → extraction
        (buffered). Events are emitted only after the work they describe is
        durably done; extraction failure never blocks persistence.
        """
        conversation_id = turn.conversation_id or self._new_conversation_id(turn)
        stored = False
        if self._conversation_store is not None:
            try:
                self._conversation_store.append(turn, conversation_id)
                stored = True
            except Exception:
                log.warning("conversation store append failed", exc_info=True)
        if stored:
            self._emit_conversation_observed(conversation_id, turn)
        self._maybe_extract(conversation_id, turn)

    def recall(self, query: str, context: Optional[QueryContext] = None) -> RecallResult:
        """Retrieve knowledge relevant to a query.

        Phase E: when the layered resolver is available, retrieval walks the
        layers top-down — project → scenario → knowledge → conversation —
        scoring knowledge inside the scenario neighborhood and expanding only
        when the sufficiency gate fails. Without a resolver, behaves exactly
        as Phase A (flat memory query).
        """
        ctx = context or QueryContext()
        resolver = self._resolver or self._default_resolver()
        if resolver is not None:
            return resolver.recall(query, ctx)
        results = self._memory_manager().query(
            text=query,
            k=ctx.top_k,
            distance_threshold=ctx.distance_threshold,
            memory_types=list(ctx.memory_types) if ctx.memory_types else None,
        )
        items = tuple(
            RecallItem(
                text=str(item.get("text", "")),
                score=float(item.get("score", item.get("distance", 0.0))),
                source="memory",
                metadata=dict(item.get("metadata", {})),
            )
            for item in results
        )
        return RecallResult(query=query, items=items)

    def retrieve_memory_rows(
        self,
        query: str,
        k: int = 5,
        distance_threshold: Optional[float] = 0.5,
        memory_types: Optional[list[str]] = None,
    ) -> list[dict]:
        """Temporary compat adapter: RecallResult → legacy flat memory rows.

        Bridges the runtime's memory formatter (which consumes flat dicts with
        id/text/score/distance/metadata) to the canonical ``recall`` API so the
        runtime keeps talking only to the Brain. Removed once the runtime memory
        section consumes ``RecallResult`` directly.
        """
        result = self.recall(
            query,
            QueryContext(
                top_k=k,
                distance_threshold=distance_threshold,
                memory_types=tuple(memory_types or ()),
            ),
        )
        rows = []
        for item in result.items:
            metadata = dict(item.metadata)
            distance = metadata.pop("distance", 1.0 - item.score if item.score else 0.5)
            rows.append(
                {
                    "id": str(metadata.pop("id", "")),
                    "text": item.text,
                    "score": item.score,
                    "distance": distance,
                    "metadata": metadata,
                }
            )
        return rows

    def _default_resolver(self):
        """Build the layered resolver from the injected layers, if present.

        Pure wiring: composes the domain layers behind the resolver's read
        callables. Returns None when the layers are absent, keeping recall
        byte-identical to Phase A.
        """
        if self._knowledge_layer is None or self._scenario_layer is None:
            return None
        from .reasoning.resolver import LayeredRetrievalResolver

        return LayeredRetrievalResolver(
            load_scenario=self._scenario_layer.store.get,
            query_knowledge=self._knowledge_layer.query_scoped,
            query_memory=self._resolver_memory_query,
            tiered=self._tiered_resolver,
        )

    def _resolver_memory_query(
        self, query: str, k: int, threshold: Optional[float]
    ) -> list[dict]:
        try:
            return self._memory_manager().query(
                text=query,
                k=k,
                distance_threshold=threshold,
                memory_types=None,
            )
        except Exception:
            return []

    def retrieve_knowledge(self, query: str, k: int = 5) -> list[dict]:
        """Search the file-backed knowledge base (Architecture Rule #6).

        The knowledge index is an internal Brain concern; the runtime never
        touches it directly. Returns the same row shape KnowledgeIndex.search
        produces, so source adapters translate byte-identically.
        """
        if self._knowledge_index is None:
            return []
        return self._knowledge_index.search(query, k=k, rerank=True)

    def retrieve_project(self, query: str, k: int = 5) -> str:
        """Retrieve project context through the Brain (Architecture Rule #6).

        Returns the same formatted string ProjectIndex.query produces.
        """
        if self._project_index is None:
            return ""
        return self._project_index.query(text=query, k=k)

    @property
    def project_root(self) -> str:
        if self._project_index is None:
            return ""
        return str(self._project_index.root)

    def set_project_index(self, project_index: Any) -> None:
        """Swap the project index (runtime set_config may update it later)."""
        self._project_index = project_index

    def learn(self, statement: str, source: Optional[str] = None) -> None:
        """Explicitly acquire knowledge: user asks to remember, write_knowledge.

        ``source`` is reserved for provenance tagging (Phase D: derived_from
        edges; Phase F: identity tag selection). When the knowledge layer is
        wired, the statement persists directly as a verified knowledge item —
        immediately discoverable by the resolver, closing the legacy
        stale-index gap. Without layers it falls back to the legacy flat
        writer.
        """
        if self._knowledge_layer is not None and self._scenario_layer is not None:
            tags = _tags_for_source(source)
            self._knowledge_layer.write(statement, tags=tags)
            return
        self._memory_manager().store_fact(statement)

    def resolve(self, query: str) -> ContextResolution:
        """Resolve the active project + scenario for a query.

        Phase A resolves only the project — today that is the working
        directory. Scenario resolution arrives with the scenarios layer.
        """
        if self._project_index is not None:
            return ContextResolution(project_id=str(self._project_index.root), method="cwd")
        return ContextResolution(method="none")

    def project_context(self) -> dict:
        """Derived, read-only personal-context projection (Phase F Step 4).

        Groups identity-tagged knowledge by category, ranked by the §5
        hierarchy. Always recomputed from the store — never cached. Returns an
        empty projection when no identity-tagged items exist; never invents
        attributes.
        """
        from .projection import project

        if self._knowledge_layer is None or self._scenario_layer is None:
            return {}
        items = self._knowledge_layer.list_objects()
        active_ids: set[str] = set()
        store = getattr(self._scenario_layer, "store", None)
        if store is not None:
            try:
                scenarios = store.list() if hasattr(store, "list") else ()
                for s in scenarios:
                    if getattr(s, "status", None) == ScenarioStatus.ACTIVE:
                        active_ids.add(s.id)
            except Exception:
                log.warning("failed to read active scenarios", exc_info=True)
        return project(items, active_scenario_ids=active_ids)

    def reflect(
        self,
        *,
        scenario_completed: bool = False,
        confirm_burst: bool = False,
        idle_pending: bool = False,
        on_demand: bool = True,
    ) -> ReflectionReport:
        """Run a consolidation + promotion pass over knowledge (Phase F).

        Triggers gate the knowledge pass (§8.2): it runs only when there is
        pending work AND at least one trigger fired. Defaults to ``on_demand``
        (manual ``reflect()`` invocation always honors an explicit pass).

        Without a knowledge layer this behaves exactly as Phase A (legacy
        consolidate pass, ungated).
        """
        from .reasoning import reflection

        if self._knowledge_layer is None or self._scenario_layer is None:
            merges = self._memory_manager().consolidate()
            return ReflectionReport(merges=merges)
        items = self._knowledge_layer.list_objects()
        pending = reflection.pending_count(items)
        if not reflection.should_reflect(
            pending,
            scenario_completed=scenario_completed,
            confirm_burst=confirm_burst,
            idle_pending=idle_pending,
            on_demand=on_demand,
        ):
            return ReflectionReport(touched_ids=())
        return self._reflect_knowledge(items)

    def inspect_memory(self) -> dict:
        """Trust surface (design §4.3): list what Cozmo remembers.

        Read-only view the user can audit. Returns a per-category projection
        plus every item's status/confidence/last_seen_at/evidence and its
        supersession/conflict edges. No writes; empty when no knowledge layer.
        """
        from .reasoning import reflection
        from .types import EdgeKind, KnowledgeStatus

        if self._knowledge_layer is None or self._scenario_layer is None:
            return {}
        items = self._knowledge_layer.list_objects()
        edges_by_source: dict[str, list[str]] = {}
        if self._relationship_store is not None:
            try:
                for edge in self._relationship_store.list():
                    edges_by_source.setdefault(edge.source_id, []).append(
                        f"{edge.kind.value}:{edge.target_id}"
                    )
            except Exception:
                log.warning("failed to read relationship edges", exc_info=True)
        projection = self.project_context()
        items_view = [
            {
                "id": i.id,
                "content": i.content,
                "status": i.status.value,
                "confidence": i.confidence,
                "importance": i.importance,
                "tags": list(i.tags),
                "scenario_id": i.scenario_id,
                "last_seen_at": reflection.last_used(i).isoformat(),
                "edges": edges_by_source.get(i.id, []),
            }
            for i in items
        ]
        return {"categories": projection, "items": items_view}

    def correct_memory(
        self,
        item_id: str | None = None,
        *,
        statement: str | None = None,
        action: str = "superseded",
        tags: tuple[str, ...] | list[str] = (),
    ) -> dict:
        """Trust surface (design §4.4): user correction, append-only.

        - ``action="superseded"`` (default): demote ``item_id`` and, when
          ``statement`` is given, record the correction as a new verified item
          linked by a ``supersedes`` edge. Idempotent for already-superseded.
        - ``action="demote"``: lower confirmation to ``corroborated``.
        - ``action="archive"``: demote to ``candidate`` (out of default
          retrieval/projection, still queryable).

        Correction outranks corroboration going forward (new verified wins).
        Never deletes. Returns a small mutation report.
        """
        from .types import KnowledgeStatus

        if self._knowledge_layer is None or self._scenario_layer is None:
            return {"ok": False, "error": "no knowledge layer"}
        if item_id is None:
            return {"ok": False, "error": "item_id required"}

        if action == "demote":
            self._knowledge_layer.update_status(item_id, KnowledgeStatus.CORROBORATED)
            return {"ok": True, "demoted": item_id, "recorded": None}

        if action == "archive":
            self._knowledge_layer.update_status(item_id, KnowledgeStatus.CANDIDATE)
            return {"ok": True, "archived": item_id, "recorded": None}

        self._knowledge_layer.update_status(item_id, KnowledgeStatus.SUPERSEDED)
        recorded = None
        if statement:
            new_id = self._knowledge_layer.write(statement, tags=tags)
            recorded = new_id
            if self._relationship_store is not None:
                self._relationship_store.add_many(
                    [
                        Relationship(
                            source_id=new_id,
                            target_id=item_id,
                            kind=EdgeKind.SUPERSEDES,
                        )
                    ]
                )
        return {"ok": True, "superseded": item_id, "recorded": recorded}

    def _reflect_knowledge(self, items) -> ReflectionReport:
        from .reasoning import reflection
        from .types import KnowledgeStatus

        plan = reflection.make_plan(items, find_related=_find_related)
        promotes = 0
        corroborated = 0
        superseded = 0
        conflicts = 0
        decays = 0
        touched: set[str] = set()
        edges: list[Relationship] = []
        for outcome in plan:
            item = outcome.item
            if outcome.new_status == item.status:
                continue
            self._knowledge_layer.update_status(item.id, outcome.new_status)
            touched.add(item.id)
            if outcome.new_status == KnowledgeStatus.VERIFIED:
                promotes += 1
            else:
                corroborated += 1
            if outcome.supersedes is not None:
                superseded += 1
                touched.add(outcome.supersedes.target_id)
                self._knowledge_layer.update_status(
                    outcome.supersedes.target_id, KnowledgeStatus.SUPERSEDED
                )
                edges.append(outcome.supersedes)
                if outcome.conflicts is not None:
                    conflicts += 1
                    edges.append(outcome.conflicts)
        for item in reflection.decay_plan(items, datetime.now()):
            if item.status in (KnowledgeStatus.VERIFIED, KnowledgeStatus.SUPERSEDED):
                continue
            self._knowledge_layer.update_status(item.id, KnowledgeStatus.CANDIDATE)
            decays += 1
            touched.add(item.id)
        if edges and self._relationship_store is not None:
            try:
                self._relationship_store.add_many(edges)
            except Exception:
                log.warning("failed to write supersedes edges", exc_info=True)
        report = ReflectionReport(
            promotions=promotes,
            corroborated=corroborated,
            superseded=superseded,
            conflicts=conflicts,
            decays=decays,
            touched_ids=tuple(sorted(touched)),
        )
        if touched:
            self._emit_knowledge_promoted(report, touched)
        return report

    def _emit_knowledge_promoted(
        self, report: ReflectionReport, touched: set[str]
    ) -> None:
        """Emit a ``knowledge.promoted`` event after durable writes.

        Emitted only when at least one item was touched. Carries canonical
        Brain item ids, never storage rows or ids.
        """
        from .events import KNOWLEDGE_PROMOTED, KnowledgePromoted

        if self._event_bus is None:
            return
        event = KnowledgePromoted(
            item_ids=tuple(sorted(touched)),
            promotions=report.promotions,
            corroborated=report.corroborated,
            superseded=report.superseded,
            conflicts=report.conflicts,
        )
        try:
            self._event_bus.emit(KNOWLEDGE_PROMOTED, **event.to_payload())
        except Exception:
            log.warning("failed to emit knowledge.promoted", exc_info=True)

    def _new_conversation_id(self, turn: Turn) -> str:
        """Brain-owned conversation identity.

        Conversations are Brain concepts, not runtime or storage concepts.
        """
        return f"conv-{turn.timestamp.strftime('%Y%m%dT%H%M%S%f')}"

    def _maybe_extract(self, conversation_id: str, turn: Turn) -> None:
        """Buffer turns; run extraction when the batch reaches extract_every.

        All optional deps. Extraction is best-effort: any failure is logged
        and the buffered batch is dropped, never re-raising into observe().
        """
        if (
            self._extractor is None
            or self._knowledge_layer is None
            or self._scenario_layer is None
        ):
            return
        batch = self._pending_turns.get(conversation_id, [])
        batch.append(turn)
        if len(batch) < self._extract_every:
            self._pending_turns[conversation_id] = batch
            return
        self._pending_turns.pop(conversation_id, None)
        try:
            result = self._extractor.extract(tuple(batch))
            conversation = None
            if self._conversation_store is not None:
                conversation = self._conversation_store.get(conversation_id)
            scenario_id = self._scenario_layer.ensure_for_conversation(
                conversation, result
            )
            knowledge_ids = self._knowledge_layer.store_extracted(
                conversation_id, scenario_id, result
            )
            if self._conversation_store is not None and conversation is not None:
                if getattr(conversation, "scenario_id", None) != scenario_id:
                    self._conversation_store.set_scenario_id(
                        conversation_id, scenario_id
                    )
            if knowledge_ids:
                self._write_provenance_edges(knowledge_ids, conversation_id, scenario_id)
                self._emit_knowledge_extracted(
                    tuple(knowledge_ids), conversation_id, scenario_id, result.summary
                )
        except Exception:
            log.warning("knowledge extraction failed", exc_info=True)

    def _write_provenance_edges(
        self, knowledge_ids: list[str], conversation_id: str, scenario_id: str
    ) -> None:
        """Provenance as first-class relationships (Phase D).

        Each extracted item links to its source conversation (derived_from)
        and its scenario (observed_in). Best-effort; edge failure never breaks
        extraction or persistence.
        """
        if self._relationship_store is None:
            return
        try:
            now = datetime.now()
            relationships = []
            for kid in knowledge_ids:
                relationships.append(
                    Relationship(
                        source_id=kid,
                        target_id=conversation_id,
                        kind=EdgeKind.DERIVED_FROM,
                        created_at=now,
                    )
                )
                relationships.append(
                    Relationship(
                        source_id=kid,
                        target_id=scenario_id,
                        kind=EdgeKind.OBSERVED_IN,
                        created_at=now,
                    )
                )
            self._relationship_store.add_many(relationships)
        except Exception:
            log.warning("failed to write provenance edges", exc_info=True)

    def _emit_knowledge_extracted(
        self,
        knowledge_ids: tuple[str, ...],
        conversation_id: str,
        scenario_id: str,
        summary: str,
    ) -> None:
        event = KnowledgeExtracted(
            knowledge_ids=knowledge_ids,
            conversation_id=conversation_id,
            scenario_id=scenario_id,
            summary=summary,
        )
        if self._event_bus is None:
            return
        try:
            self._event_bus.emit(KNOWLEDGE_EXTRACTED, **event.to_payload())
        except Exception:
            log.warning("failed to emit %s", KNOWLEDGE_EXTRACTED, exc_info=True)

    def _emit_conversation_observed(self, conversation_id: str, turn: Turn) -> None:
        event = ConversationObserved(
            conversation_id=conversation_id,
            user=turn.user,
            assistant=turn.assistant,
            timestamp=turn.timestamp,
            tool_outputs=turn.tool_outputs,
        )
        if self._event_bus is None:
            return
        try:
            self._event_bus.emit(CONVERSATION_OBSERVED, **event.to_payload())
        except Exception:
            log.warning("failed to emit %s", CONVERSATION_OBSERVED, exc_info=True)

    def _memory_manager(self):
        manager = self._memory
        if manager is None:
            manager = get_memory_manager()
        if manager is None:
            raise RuntimeError(
                "no memory manager wired; call set_memory_manager or pass memory= to Brain()"
            )
        return manager


def _tags_for_source(source: Optional[str]) -> tuple[str, ...]:
    """Map a ``source`` label to identity-seeding tags (Phase F)."""
    if not source:
        return ()
    s = source.lower()
    if "preference" in s:
        return ("preference", "identity")
    if "goal" in s:
        return ("goal", "identity")
    if "skill" in s:
        return ("skill", "identity")
    return ()


def _find_related(item, items) -> Optional[Any]:
    """Most recent VERIFIED item that overlaps this item (supersession target).

    Identity change keeps history: a newly verified claim supersedes the most
    recent verified item whose tags overlap the new one. Returns None when no
    such item exists (e.g. the very first verified preference).
    """
    from .types import KnowledgeStatus

    prospective = _IDENTITY_TAGS.intersection(item.tags)
    best = None
    for other in items:
        if other.id == item.id:
            continue
        if other.status != KnowledgeStatus.VERIFIED:
            continue
        if not _IDENTITY_TAGS.intersection(other.tags):
            continue
        if prospective and not (set(prospective) & set(other.tags)):
            continue
        if best is None or other.created_at >= best.created_at:
            best = other
    return best
