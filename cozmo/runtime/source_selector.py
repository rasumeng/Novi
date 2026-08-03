"""SourceSelector — pure multi-source selection strategy layer.

Phase 9.5 step 2. Extracts the source-selection decision branches from
``RetrievalPolicy`` into a pluggable, pure layer.

Responsibilities:
  - decide which sources participate, and the retrieval strategy, from
    already-computed analysis signals
  - deterministic — identical inputs yield an identical ``SourceSelection``

Owns nothing else: no I/O, no retrieval, no stores, no runtime imports.
``RetrievalPolicy`` keeps ``resolve()`` / ``RetrievalPlan`` /
``ContextAllocation`` ownership and turns a ``SourceSelection`` into a plan.

``SourceType`` and ``RetrievalStrategy`` live here as the single source of
truth; ``RetrievalPolicy`` re-exports them so existing imports from
``cozmo.runtime.retrieval_policy`` keep working.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SourceType(str, Enum):
    """Information source kinds available for retrieval.

    Renamed from ``RetrievalSource`` (Phase 9 step 1) so the name is freed for
    the ``RetrievalSource`` protocol in cozmo.runtime.sources.base.

    ``FILE`` is reserved for workspace file indexing (Phase 9 step 5). No
    strategy selects it yet: ``FileRetrievalSource`` is a NoOp stub and the
    runtime has no file retrieval path to execute.
    """
    MEMORY = "memory"
    KNOWLEDGE = "knowledge"
    PROJECT = "project"
    FILE = "file"
    WEB = "web"


class RetrievalStrategy(str, Enum):
    """Retrieval strategy — primary source ordering and grounding mode."""
    NONE = "none"
    MEMORY_FIRST = "memory_first"
    KNOWLEDGE_ONLY = "knowledge_only"
    WEB_ONLY = "web_only"
    KNOWLEDGE_THEN_WEB = "knowledge_then_web"
    PROJECT_FIRST = "project_first"


@dataclass(frozen=True)
class SourceSelection:
    """A strategy's source/strategy decision.

    ``sources`` lists the strategy's base sources only. Context sources
    (memory/project) are inserted by ``RetrievalPolicy._with_context`` when
    their signals are present, mirroring the pre-extraction policy flow.
    """

    sources: tuple[SourceType, ...]
    strategy: RetrievalStrategy
    reason: str


class SourceSelector:
    """Pure decision layer. No I/O, no retrieval, no stores, no runtime imports.

    Consumes the same structured signals ``RetrievalPolicy.resolve`` consumed
    directly and returns a ``SourceSelection`` the policy turns into a plan.
    Selection is driven by existing analysis signals only; keyword matching,
    ranking, merging, and execution are intentionally absent.
    """

    @staticmethod
    def select(
        needs_grounding: bool,
        signal_types: list[str],
        signal_strengths: dict[str, str],
        intent: str,
        needs_memory: bool = False,
        needs_project: bool = False,
    ) -> SourceSelection:
        """Choose sources + strategy from the analysis signals."""
        if needs_grounding:
            return SourceSelector._select_grounding(
                signal_types, signal_strengths, intent, needs_memory, needs_project
            )
        return SourceSelector._select_local(intent, needs_memory, needs_project)

    # ── grounding strategies ────────────────────────────────────────────

    @staticmethod
    def _select_grounding(
        signal_types: list[str],
        signal_strengths: dict[str, str],
        intent: str,
        needs_memory: bool,
        needs_project: bool,
    ) -> SourceSelection:
        """Grounding required → current information is the priority."""
        if "dynamic" in signal_types:
            return SourceSelection(
                (SourceType.WEB,),
                RetrievalStrategy.WEB_ONLY,
                "Time-sensitive dynamic content requested; direct web retrieval",
            )
        if intent == "research":
            return SourceSelection(
                (SourceType.WEB,),
                RetrievalStrategy.WEB_ONLY,
                "Explicit research intent; direct web retrieval",
            )
        if signal_strengths.get("temporal") == "high":
            return SourceSelection(
                (SourceType.WEB,),
                RetrievalStrategy.WEB_ONLY,
                "Time-sensitive temporal query; direct web retrieval",
            )
        # External signal present (temporal medium/low, comparative) or the
        # LLM said yes without a specific signal → knowledge first, web fallback.
        if needs_memory:
            return SourceSelection(
                (SourceType.KNOWLEDGE, SourceType.WEB),
                RetrievalStrategy.MEMORY_FIRST,
                "Personal memory context first; then local knowledge, then web fallback",
            )
        return SourceSelection(
            (SourceType.KNOWLEDGE, SourceType.WEB),
            RetrievalStrategy.KNOWLEDGE_THEN_WEB,
            "Current information; local knowledge attempted first with web fallback",
        )

    # ── local strategies ────────────────────────────────────────────────

    @staticmethod
    def _select_local(
        intent: str,
        needs_memory: bool,
        needs_project: bool,
    ) -> SourceSelection:
        """No grounding → stable, local, or personal information only."""
        if intent in ("coding", "work") or needs_project:
            return SourceSelection(
                (SourceType.PROJECT, SourceType.KNOWLEDGE),
                RetrievalStrategy.PROJECT_FIRST,
                "Project-aware query; project context first, then technical knowledge",
            )
        if needs_memory:
            return SourceSelection(
                (SourceType.MEMORY,),
                RetrievalStrategy.NONE,
                "Stable personal memory query; no external retrieval needed",
            )
        return SourceSelection(
            (),
            RetrievalStrategy.NONE,
            "No retrieval needed",
        )
