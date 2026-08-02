"""RetrievalPolicy — decides WHERE and HOW to retrieve information.

Separates:
  "Should we retrieve?" (GroundingDecision)
from:
  "Where should retrieval happen?" (RetrievalPolicy)

Consumes existing structured signals only:
  - GroundingDecision (needs_grounding)
  - EvidenceAnalysis (signals, requirements, needs_memory, needs_project)
  - Intent

Outputs RetrievalPlan that the runtime executes.

Phase 9 step 5: multi-source planning. The policy now plans across Memory,
Knowledge, Project, File (stub), and Web. It decides:

  - which sources participate      → plan.sources
  - execution order                → deterministic plan.sources ordering
  - context allocation             → plan.allocation (ContextAllocation)
  - retrieval strategy             → plan.strategy

The policy stays pure: no I/O, no source implementations, no keyword
matching. Selection is driven by existing analysis signals only. Cross-source
merging and evidence quality decisions are intentionally NOT made here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .retrieval_budget import ContextAllocation


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


# Deterministic source order within a plan: context sources first, then
# grounding sources. FILE reserved — never selected by a strategy yet.
_SOURCE_ORDER = {
    SourceType.MEMORY: 0,
    SourceType.PROJECT: 1,
    SourceType.KNOWLEDGE: 2,
    SourceType.FILE: 3,
    SourceType.WEB: 4,
}


@dataclass
class RetrievalPlan:
    """What, where, and how to retrieve before the ReAct loop."""
    sources: list[SourceType] = field(default_factory=list)
    strategy: RetrievalStrategy = RetrievalStrategy.NONE
    reason: str = ""
    allocation: ContextAllocation = field(default_factory=ContextAllocation)


class RetrievalPolicy:
    """Pure decision logic. No runtime dependencies, no keyword matching.

    Consumes GroundingDecision + EvidenceAnalysis + intent string.
    Outputs RetrievalPlan describing which sources to query and in what order.
    """

    @staticmethod
    def resolve(
        needs_grounding: bool,
        signal_types: list[str],
        signal_strengths: dict[str, str],
        has_external: bool,
        intent: str,
        needs_memory: bool = False,
        needs_project: bool = False,
    ) -> RetrievalPlan:
        """Decide the multi-source retrieval plan from existing signals.

        Args:
            needs_grounding: GroundingDecision.needs_grounding
            signal_types: List of evidence signal types (e.g. ["temporal", "dynamic"])
            signal_strengths: Map of signal type -> strength (e.g. {"temporal": "medium"})
            has_external: EvidenceAnalysis.requirements.external
            intent: Intent type string (e.g. "conversation", "research", "coding")
            needs_memory: EvidenceAnalysis.needs_memory — participate memory?
            needs_project: EvidenceAnalysis.needs_project — participate project?

        Memory/project are context sources: they join the plan whenever their
        signal is present, regardless of the grounding strategy. Strategy
        reflects the primary grounding path the executor must run.
        """
        if needs_grounding:
            return RetrievalPolicy._resolve_grounding(
                signal_types, signal_strengths, intent, needs_memory, needs_project
            )
        return RetrievalPolicy._resolve_local(intent, needs_memory, needs_project)

    # ── decision helpers ────────────────────────────────────────────────

    @staticmethod
    def _resolve_grounding(
        signal_types: list[str],
        signal_strengths: dict[str, str],
        intent: str,
        needs_memory: bool,
        needs_project: bool,
    ) -> RetrievalPlan:
        """Grounding required → current information is the priority."""
        if "dynamic" in signal_types:
            sources = [SourceType.WEB]
            strategy = RetrievalStrategy.WEB_ONLY
            reason = "Time-sensitive dynamic content requested; direct web retrieval"
        elif intent == "research":
            sources = [SourceType.WEB]
            strategy = RetrievalStrategy.WEB_ONLY
            reason = "Explicit research intent; direct web retrieval"
        elif signal_strengths.get("temporal") == "high":
            sources = [SourceType.WEB]
            strategy = RetrievalStrategy.WEB_ONLY
            reason = "Time-sensitive temporal query; direct web retrieval"
        else:
            # External signal present (temporal medium/low, comparative) or the
            # LLM said yes without a specific signal → knowledge first, web fallback.
            sources = [SourceType.KNOWLEDGE, SourceType.WEB]
            if needs_memory:
                strategy = RetrievalStrategy.MEMORY_FIRST
                reason = "Personal memory context first; then local knowledge, then web fallback"
            else:
                strategy = RetrievalStrategy.KNOWLEDGE_THEN_WEB
                reason = "Current information; local knowledge attempted first with web fallback"
        sources = RetrievalPolicy._with_context(sources, needs_memory, needs_project)
        return RetrievalPolicy._plan(sources, strategy, reason)

    @staticmethod
    def _resolve_local(
        intent: str,
        needs_memory: bool,
        needs_project: bool,
    ) -> RetrievalPlan:
        """No grounding → stable, local, or personal information only."""
        if intent in ("coding", "work") or needs_project:
            sources = [SourceType.PROJECT, SourceType.KNOWLEDGE]
            strategy = RetrievalStrategy.PROJECT_FIRST
            reason = "Project-aware query; project context first, then technical knowledge"
            return RetrievalPolicy._plan(
                RetrievalPolicy._with_context(sources, needs_memory, needs_project),
                strategy,
                reason,
            )
        if needs_memory:
            return RetrievalPolicy._plan(
                [SourceType.MEMORY],
                RetrievalStrategy.NONE,
                "Stable personal memory query; no external retrieval needed",
            )
        return RetrievalPlan(
            sources=[],
            strategy=RetrievalStrategy.NONE,
            reason="No retrieval needed",
            allocation=ContextAllocation(max_sources=0),
        )

    # ── plan construction ───────────────────────────────────────────────

    @staticmethod
    def _with_context(
        sources: list[SourceType],
        needs_memory: bool,
        needs_project: bool,
    ) -> list[SourceType]:
        """Insert memory/project context sources at deterministic positions."""
        result = list(sources)
        if needs_memory and SourceType.MEMORY not in result:
            result.insert(0, SourceType.MEMORY)
        if needs_project and SourceType.PROJECT not in result:
            idx = 1 if SourceType.MEMORY in result else 0
            result.insert(idx, SourceType.PROJECT)
        return result

    @staticmethod
    def _plan(
        sources: list[SourceType],
        strategy: RetrievalStrategy,
        reason: str,
    ) -> RetrievalPlan:
        """Build a plan with deterministic source order + context allocation."""
        ordered = sorted(sources, key=lambda s: _SOURCE_ORDER.get(s, 99))
        allocation = ContextAllocation(max_sources=len(ordered))
        return RetrievalPlan(
            sources=ordered,
            strategy=strategy,
            reason=reason,
            allocation=allocation,
        )
