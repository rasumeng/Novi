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

Phase 9.5 step 2: source selection moves into the pure ``SourceSelector``
layer (novi.runtime.source_selector). The policy delegates the decision and
keeps ownership of ``resolve()``, ``RetrievalPlan`` construction, context
insertion (``_with_context``), deterministic ordering, and
``ContextAllocation`` creation.

``SourceType`` / ``RetrievalStrategy`` are re-exported from
``novi.runtime.source_selector`` (their single source of truth) so existing
imports from this module keep working.

The policy stays pure: no I/O, no source implementations, no keyword
matching. Selection is driven by existing analysis signals only. Cross-source
merging and evidence quality decisions are intentionally NOT made here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .retrieval_budget import ContextAllocation
from .source_selector import RetrievalStrategy, SourceSelector, SourceType


# Deterministic source order within a plan: context tiers first, then
# grounding sources. Layered (Phase E): IDENTITY and SCENARIO sit between the
# context memory tier and the competency/base tiers, mirroring the Brain's
# identity → project → scenario → knowledge hierarchy. FILE reserved — never
# selected by a strategy yet.
_SOURCE_ORDER = {
    SourceType.MEMORY: 0,
    SourceType.IDENTITY: 1,
    SourceType.PROJECT: 2,
    SourceType.SCENARIO: 3,
    SourceType.KNOWLEDGE: 4,
    SourceType.FILE: 5,
    SourceType.WEB: 6,
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
        needs_scenario: bool = False,
        needs_identity: bool = False,
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
            needs_scenario: layered tier — scenario context participates
            needs_identity: layered tier — identity context participates

        Memory/project are context sources: they join the plan whenever their
        signal is present, regardless of the grounding strategy. Strategy
        reflects the primary grounding path the executor must run. Scenario
        and identity are the layered tiers (Phase E): they slot into the plan
        between memory/project and the competency/base sources, mirroring the
        Brain's identity → project → scenario → knowledge hierarchy.
        """
        selection = SourceSelector.select(
            needs_grounding=needs_grounding,
            signal_types=signal_types,
            signal_strengths=signal_strengths,
            intent=intent,
            needs_memory=needs_memory,
            needs_project=needs_project,
        )
        if not selection.sources:
            return RetrievalPlan(
                sources=[],
                strategy=RetrievalStrategy.NONE,
                reason=selection.reason,
                allocation=ContextAllocation(max_sources=0),
            )
        sources = RetrievalPolicy._with_context(
            selection.sources,
            needs_memory,
            needs_project,
            needs_scenario,
            needs_identity,
        )
        return RetrievalPolicy._plan(sources, selection.strategy, selection.reason)

    # ── plan construction ───────────────────────────────────────────────

    @staticmethod
    def _with_context(
        sources: list[SourceType] | tuple[SourceType, ...],
        needs_memory: bool,
        needs_project: bool,
        needs_scenario: bool = False,
        needs_identity: bool = False,
    ) -> list[SourceType]:
        """Insert context + layered tiers at deterministic positions."""
        result = list(sources)
        if needs_memory and SourceType.MEMORY not in result:
            result.insert(0, SourceType.MEMORY)
        if needs_project and SourceType.PROJECT not in result:
            idx = 1 if SourceType.MEMORY in result else 0
            result.insert(idx, SourceType.PROJECT)
        if needs_identity and SourceType.IDENTITY not in result:
            idx = 0 if SourceType.MEMORY not in result else 1
            if SourceType.PROJECT in result:
                idx = result.index(SourceType.PROJECT)
            result.insert(idx, SourceType.IDENTITY)
        if needs_scenario and SourceType.SCENARIO not in result:
            idx = 0
            if SourceType.MEMORY in result:
                idx = result.index(SourceType.MEMORY) + 1
            if SourceType.PROJECT in result:
                idx = result.index(SourceType.PROJECT) + 1
            if SourceType.IDENTITY in result:
                idx = result.index(SourceType.IDENTITY) + 1
            result.insert(idx, SourceType.SCENARIO)
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
