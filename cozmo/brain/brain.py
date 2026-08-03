"""Brain — cognition facade over Cozmo's knowledge system.

Phase B establishes the write pipeline:
    Runtime reports events.
    Brain decides persistence.
    Storage executes persistence.

observe() persists a turn to the ConversationStore, keeps the legacy
MemoryManager write alive as a temporary compatibility shim, then emits
ConversationObserved. No extraction, retrieval, summarization, or reasoning
belongs here — later phases add those.

The legacy MemoryManager call inside observe() is a TEMPORARY compatibility
shim, not part of Brain's design. It exists only until Phase C replaces the
legacy memory pipeline with extraction, then it is removed.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..memory.manager import get_memory_manager
from .events import CONVERSATION_OBSERVED, ConversationObserved
from .storage.base import ConversationStore
from .types import (
    ContextResolution,
    QueryContext,
    RecallItem,
    RecallResult,
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
    """

    def __init__(
        self,
        *,
        memory: Any = None,
        project_index: Any = None,
        conversation_store: Optional[ConversationStore] = None,
        event_bus: Any = None,
    ) -> None:
        self._memory = memory
        self._project_index = project_index
        self._conversation_store = conversation_store
        self._event_bus = event_bus

    def observe(self, turn: Turn) -> None:
        """Capture a completed turn.

        Order: conversation persisted → legacy compatibility write → event
        emitted. The event is emitted only after the turn is durably
        persisted, and only when a conversation store is wired.
        """
        conversation_id = turn.conversation_id or self._new_conversation_id(turn)
        stored = False
        if self._conversation_store is not None:
            try:
                self._conversation_store.append(turn, conversation_id)
                stored = True
            except Exception:
                log.warning("conversation store append failed", exc_info=True)
        self._write_legacy_memory(turn)
        if stored:
            self._emit_conversation_observed(conversation_id, turn)

    def recall(self, query: str, context: Optional[QueryContext] = None) -> RecallResult:
        """Retrieve knowledge relevant to a query.

        Phase A serves memories via the existing manager; layered
        project → scenario → knowledge → conversation retrieval arrives
        with the resolver (Phase E).
        """
        ctx = context or QueryContext()
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

    def _write_legacy_memory(self, turn: Turn) -> None:
        """Temporary compatibility shim — NOT part of Brain's design.

        Keeps the legacy MemoryManager write alive until Phase C replaces
        the legacy memory pipeline with extraction. Removed in Phase C.
        """
        try:
            manager = self._memory_manager()
            manager.add_interaction(turn.user, turn.assistant)
        except Exception:
            log.warning("legacy memory write failed", exc_info=True)

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
