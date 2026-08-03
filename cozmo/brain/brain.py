"""Brain — cognition facade over Cozmo's knowledge system.

Phase A establishes the architectural seam, nothing more: every cognition
method delegates to today's components, no new behavior is introduced.
The layered retrieval, scenario resolution, and reasoning tier arrive in
later phases (C/E).

Transitional note: the facade currently wraps the legacy memory components
(cozmo/memory). Nothing outside cozmo/brain should reach into storage
directly; callers interact only with Brain.
"""

from __future__ import annotations

from typing import Any, Optional

from ..memory.manager import get_memory_manager
from .types import (
    ContextResolution,
    QueryContext,
    RecallItem,
    RecallResult,
    ReflectionReport,
    Turn,
)

__all__ = ["Brain"]


class Brain:
    """Facade exposing observe / recall / learn / resolve / reflect.

    Args:
        memory: MemoryManager-like component. Defaults to the process-global
            manager registered via set_memory_manager.
        project_index: ProjectIndex-like component used to resolve the active
            project. No process-global exists for it; inject it.
    """

    def __init__(self, *, memory: Any = None, project_index: Any = None) -> None:
        self._memory = memory
        self._project_index = project_index

    def observe(self, turn: Turn) -> None:
        """Capture a completed turn for later learning."""
        self._memory_manager().add_interaction(turn.user, turn.assistant)

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

    def _memory_manager(self):
        manager = self._memory
        if manager is None:
            manager = get_memory_manager()
        if manager is None:
            raise RuntimeError(
                "no memory manager wired; call set_memory_manager or pass memory= to Brain()"
            )
        return manager
