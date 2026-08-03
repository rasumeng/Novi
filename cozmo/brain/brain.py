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
    Relationship,
    ReflectionReport,
    Turn,
)

log = logging.getLogger("cozmo.brain")

__all__ = ["Brain"]


class Brain:
    """Facade exposing observe / recall / learn / resolve / reflect.

    Args:
        memory: MemoryManager-like component. Defaults to the process-global
            manager registered via set_memory_manager.
        project_index: ProjectIndex-like component used to resolve the active
            project. No process-global exists for it; inject it.
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
        conversation_store: Optional[ConversationStore] = None,
        event_bus: Any = None,
        extractor: Any = None,
        knowledge_layer: Any = None,
        scenario_layer: Any = None,
        relationship_store: Any = None,
        resolver: Any = None,
        extract_every: int = 5,
    ) -> None:
        self._memory = memory
        self._project_index = project_index
        self._conversation_store = conversation_store
        self._event_bus = event_bus
        self._extractor = extractor
        self._knowledge_layer = knowledge_layer
        self._scenario_layer = scenario_layer
        self._relationship_store = relationship_store
        self._resolver = resolver
        self._extract_every = max(1, extract_every)
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
            memory_types=list(ctx.memory_types) or None,
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

    def learn(self, statement: str, source: Optional[str] = None) -> None:
        """Explicitly acquire knowledge: user asks to remember, write_knowledge.

        ``source`` is reserved for provenance (derived_from edges, Phase D).
        """
        self._memory_manager().store_fact(statement)

    def resolve(self, query: str) -> ContextResolution:
        """Resolve the active project + scenario for a query.

        Phase A resolves only the project — today that is the working
        directory. Scenario resolution arrives with the scenarios layer.
        """
        if self._project_index is not None:
            return ContextResolution(project_id=str(self._project_index.root), method="cwd")
        return ContextResolution(method="none")

    def reflect(self) -> ReflectionReport:
        """Run a consolidation pass over knowledge.

        Phase A maps reflection to the existing merge/consolidate pass;
        promotion, verification, and supersession arrive with the reasoning
        tier (Phase F).
        """
        merges = self._memory_manager().consolidate()
        return ReflectionReport(merges=merges)

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
