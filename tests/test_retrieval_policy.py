"""Phase 9 step 5 — RetrievalPolicy multi-source planning tests.

Planning-only. No executor, no stores, no network. Analysis inputs are
mocked primitives. Covers:

- source selection per signal/intent
- strategy selection
- ContextAllocation generation
- deterministic ordering
- edge cases (no retrieval, memory-only, FILE reserved)
"""

from __future__ import annotations

from cozmo.runtime.retrieval_budget import ContextAllocation
from cozmo.runtime.retrieval_policy import (
    RetrievalPlan,
    RetrievalPolicy,
    RetrievalStrategy,
    SourceType,
)

_SOURCE_ORDER = {
    SourceType.MEMORY: 0,
    SourceType.PROJECT: 1,
    SourceType.KNOWLEDGE: 2,
    SourceType.FILE: 3,
    SourceType.WEB: 4,
}


def resolve(needs_grounding=False, signal_types=(), signal_strengths=None,
            has_external=False, intent="conversation",
            needs_memory=False, needs_project=False,
            needs_scenario=False, needs_identity=False):
    return RetrievalPolicy.resolve(
        needs_grounding=needs_grounding,
        signal_types=list(signal_types),
        signal_strengths=signal_strengths or {},
        has_external=has_external,
        intent=intent,
        needs_memory=needs_memory,
        needs_project=needs_project,
        needs_scenario=needs_scenario,
        needs_identity=needs_identity,
    )


class TestSourceType:
    def test_enum_covers_all_planned_sources(self):
        assert set(SourceType) == {
            SourceType.MEMORY, SourceType.KNOWLEDGE,
            SourceType.PROJECT, SourceType.FILE, SourceType.WEB,
            SourceType.SCENARIO, SourceType.IDENTITY,
        }

    def test_layered_identity_precedes_scenario(self):
        plan = resolve(
            intent="coding",
            needs_project=True,
            needs_scenario=True,
            needs_identity=True,
        )
        assert plan.sources == [
            SourceType.IDENTITY,
            SourceType.PROJECT,
            SourceType.SCENARIO,
            SourceType.KNOWLEDGE,
        ]

    def test_layered_absent_when_signals_absent(self):
        plan = resolve(intent="coding", needs_project=True)
        assert SourceType.SCENARIO not in plan.sources
        assert SourceType.IDENTITY not in plan.sources

    def test_strategy_covers_multi_source_variants(self):
        assert RetrievalStrategy.MEMORY_FIRST.value == "memory_first"
        assert RetrievalStrategy.PROJECT_FIRST.value == "project_first"
        assert RetrievalStrategy.KNOWLEDGE_THEN_WEB.value == "knowledge_then_web"


class TestStrategySelection:
    def test_dynamic_signal_web_only(self):
        plan = resolve(needs_grounding=True, signal_types=["dynamic"])
        assert plan.strategy == RetrievalStrategy.WEB_ONLY
        assert plan.sources == [SourceType.WEB]

    def test_research_intent_web_only(self):
        plan = resolve(needs_grounding=True, intent="research")
        assert plan.strategy == RetrievalStrategy.WEB_ONLY
        assert plan.sources == [SourceType.WEB]

    def test_temporal_high_web_only(self):
        plan = resolve(needs_grounding=True, signal_types=["temporal"],
                       signal_strengths={"temporal": "high"})
        assert plan.strategy == RetrievalStrategy.WEB_ONLY

    def test_grounding_without_memory_knowledge_then_web(self):
        plan = resolve(needs_grounding=True, signal_types=["temporal"],
                       signal_strengths={"temporal": "medium"})
        assert plan.strategy == RetrievalStrategy.KNOWLEDGE_THEN_WEB
        assert plan.sources == [SourceType.KNOWLEDGE, SourceType.WEB]

    def test_grounding_with_memory_memory_first(self):
        plan = resolve(needs_grounding=True, signal_types=["temporal"],
                       signal_strengths={"temporal": "medium"}, needs_memory=True)
        assert plan.strategy == RetrievalStrategy.MEMORY_FIRST
        assert plan.sources == [SourceType.MEMORY, SourceType.KNOWLEDGE, SourceType.WEB]

    def test_grounding_conservative_knowledge_then_web(self):
        plan = resolve(needs_grounding=True)
        assert plan.strategy == RetrievalStrategy.KNOWLEDGE_THEN_WEB

    def test_coding_project_first(self):
        plan = resolve(intent="coding")
        assert plan.strategy == RetrievalStrategy.PROJECT_FIRST
        assert plan.sources == [SourceType.PROJECT, SourceType.KNOWLEDGE]

    def test_work_intent_project_first(self):
        plan = resolve(intent="work")
        assert plan.strategy == RetrievalStrategy.PROJECT_FIRST

    def test_needs_project_project_first(self):
        plan = resolve(needs_project=True)
        assert plan.strategy == RetrievalStrategy.PROJECT_FIRST
        assert SourceType.PROJECT in plan.sources

    def test_no_retrieval_needed(self):
        plan = resolve()
        assert plan.strategy == RetrievalStrategy.NONE
        assert plan.sources == []

    def test_research_without_grounding_none(self):
        plan = resolve(intent="research")
        assert plan.strategy == RetrievalStrategy.NONE
        assert plan.sources == []


class TestMemoryAndProjectParticipation:
    def test_web_only_includes_memory_context_when_needed(self):
        plan = resolve(needs_grounding=True, signal_types=["dynamic"], needs_memory=True)
        assert plan.strategy == RetrievalStrategy.WEB_ONLY
        assert plan.sources == [SourceType.MEMORY, SourceType.WEB]

    def test_web_only_includes_project_context_when_needed(self):
        plan = resolve(needs_grounding=True, signal_types=["dynamic"], needs_project=True)
        assert plan.sources == [SourceType.PROJECT, SourceType.WEB]

    def test_web_only_includes_memory_and_project(self):
        plan = resolve(needs_grounding=True, signal_types=["dynamic"],
                       needs_memory=True, needs_project=True)
        assert plan.sources == [SourceType.MEMORY, SourceType.PROJECT, SourceType.WEB]

    def test_memory_only_no_grounding(self):
        plan = resolve(needs_memory=True)
        assert plan.strategy == RetrievalStrategy.NONE
        assert plan.sources == [SourceType.MEMORY]

    def test_coding_with_memory(self):
        plan = resolve(intent="coding", needs_memory=True)
        assert plan.sources == [SourceType.MEMORY, SourceType.PROJECT, SourceType.KNOWLEDGE]


class TestContextAllocation:
    def test_plan_carries_allocation(self):
        plan = resolve(needs_grounding=True, signal_types=["temporal"],
                       signal_strengths={"temporal": "medium"})
        assert isinstance(plan.allocation, ContextAllocation)

    def test_max_sources_matches_source_count(self):
        plan = resolve(needs_grounding=True, signal_types=["temporal"],
                       signal_strengths={"temporal": "medium"}, needs_memory=True)
        assert plan.allocation.max_sources == len(plan.sources)

    def test_empty_plan_allocation_zero_sources(self):
        plan = resolve()
        assert plan.allocation.max_sources == 0

    def test_allocation_defaults_preserved(self):
        plan = resolve(needs_grounding=True, signal_types=["dynamic"])
        assert plan.allocation.max_results == 8
        assert plan.allocation.max_context_chars == 6000


class TestDeterminism:
    def test_same_inputs_same_plan(self):
        kw = dict(needs_grounding=True, signal_types=["temporal"],
                  signal_strengths={"temporal": "medium"}, needs_memory=True)
        a = resolve(**kw)
        b = resolve(**kw)
        assert a.sources == b.sources
        assert a.strategy == b.strategy
        assert a.reason == b.reason

    def test_sources_always_canonically_ordered(self):
        plan = resolve(needs_grounding=True, signal_types=["temporal"],
                       signal_strengths={"temporal": "medium"},
                       needs_memory=True, needs_project=True)
        keys = [_SOURCE_ORDER[s] for s in plan.sources]
        assert keys == sorted(keys)
        assert len(set(plan.sources)) == len(plan.sources)


class TestEdgeCases:
    def test_file_never_selected(self):
        cases = [
            dict(needs_grounding=True, signal_types=["dynamic"]),
            dict(needs_grounding=True, signal_types=["temporal"],
                 signal_strengths={"temporal": "medium"},
                 needs_memory=True, needs_project=True),
            dict(intent="coding"),
            dict(intent="work"),
            dict(needs_memory=True),
        ]
        for kw in cases:
            plan = resolve(**kw)
            assert SourceType.FILE not in plan.sources

    def test_plan_reason_is_explainable(self):
        plan = resolve(needs_grounding=True, signal_types=["dynamic"])
        assert plan.reason
        assert "web" in plan.reason.lower()

    def test_has_external_knowledge_then_web(self):
        plan = resolve(needs_grounding=True, has_external=True)
        assert plan.strategy == RetrievalStrategy.KNOWLEDGE_THEN_WEB

    def test_plan_defaults(self):
        plan = RetrievalPlan()
        assert plan.sources == []
        assert plan.strategy == RetrievalStrategy.NONE
        assert isinstance(plan.allocation, ContextAllocation)
