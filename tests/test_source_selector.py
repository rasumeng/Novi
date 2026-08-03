"""Phase 9.5 step 2 — SourceSelector tests.

Pure selection layer. No executor, no stores, no network. Verifies the
strategy branches extracted from RetrievalPolicy produce identical source
lists and strategies as the pre-extraction policy flow.

Covers each strategy, determinism, memory/project combinations, grounding
cases, coding/project cases, and the no-signal fallback. ``resolve()``
output parity is gated by the unchanged test_retrieval_policy.py suite.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from cozmo.runtime.retrieval_policy import RetrievalPolicy, RetrievalStrategy, SourceType
from cozmo.runtime.source_selector import SourceSelection, SourceSelector


def select(needs_grounding=False, signal_types=(), signal_strengths=None,
           intent="conversation", needs_memory=False, needs_project=False):
    return SourceSelector.select(
        needs_grounding=needs_grounding,
        signal_types=list(signal_types),
        signal_strengths=signal_strengths or {},
        intent=intent,
        needs_memory=needs_memory,
        needs_project=needs_project,
    )


class TestSourceSelectionContract:
    def test_frozen(self):
        sel = select()
        assert isinstance(sel, SourceSelection)
        with pytest.raises(FrozenInstanceError):
            sel.sources = (SourceType.WEB,)

    def test_fields(self):
        sel = select(needs_grounding=True, signal_types=["dynamic"])
        assert isinstance(sel.sources, tuple)
        assert isinstance(sel.strategy, RetrievalStrategy)
        assert isinstance(sel.reason, str)


class TestGroundingStrategies:
    def test_dynamic_web_only(self):
        sel = select(needs_grounding=True, signal_types=["dynamic"])
        assert sel.strategy == RetrievalStrategy.WEB_ONLY
        assert sel.sources == (SourceType.WEB,)

    def test_research_intent_web_only(self):
        sel = select(needs_grounding=True, intent="research")
        assert sel.strategy == RetrievalStrategy.WEB_ONLY
        assert sel.sources == (SourceType.WEB,)

    def test_temporal_high_web_only(self):
        sel = select(needs_grounding=True, signal_types=["temporal"],
                     signal_strengths={"temporal": "high"})
        assert sel.strategy == RetrievalStrategy.WEB_ONLY

    def test_grounding_without_memory_knowledge_then_web(self):
        sel = select(needs_grounding=True, signal_types=["temporal"],
                     signal_strengths={"temporal": "medium"})
        assert sel.strategy == RetrievalStrategy.KNOWLEDGE_THEN_WEB
        assert sel.sources == (SourceType.KNOWLEDGE, SourceType.WEB)

    def test_grounding_with_memory_memory_first(self):
        sel = select(needs_grounding=True, signal_types=["temporal"],
                     signal_strengths={"temporal": "medium"}, needs_memory=True)
        assert sel.strategy == RetrievalStrategy.MEMORY_FIRST
        assert sel.sources == (SourceType.KNOWLEDGE, SourceType.WEB)

    def test_grounding_conservative_knowledge_then_web(self):
        sel = select(needs_grounding=True)
        assert sel.strategy == RetrievalStrategy.KNOWLEDGE_THEN_WEB


class TestLocalStrategies:
    def test_coding_project_first(self):
        sel = select(intent="coding")
        assert sel.strategy == RetrievalStrategy.PROJECT_FIRST
        assert sel.sources == (SourceType.PROJECT, SourceType.KNOWLEDGE)

    def test_work_intent_project_first(self):
        sel = select(intent="work")
        assert sel.strategy == RetrievalStrategy.PROJECT_FIRST

    def test_needs_project_project_first(self):
        sel = select(needs_project=True)
        assert sel.strategy == RetrievalStrategy.PROJECT_FIRST
        assert SourceType.PROJECT in sel.sources

    def test_no_retrieval_needed(self):
        sel = select()
        assert sel.strategy == RetrievalStrategy.NONE
        assert sel.sources == ()

    def test_research_without_grounding_none(self):
        sel = select(intent="research")
        assert sel.strategy == RetrievalStrategy.NONE
        assert sel.sources == ()


class TestMemoryAndProjectCombinations:
    def test_web_only_keeps_base_sources_with_memory_signal(self):
        sel = select(needs_grounding=True, signal_types=["dynamic"], needs_memory=True)
        # Context insertion is the policy's job; base sources stay WEB.
        assert sel.strategy == RetrievalStrategy.WEB_ONLY
        assert sel.sources == (SourceType.WEB,)

    def test_memory_only_no_grounding(self):
        sel = select(needs_memory=True)
        assert sel.strategy == RetrievalStrategy.NONE
        assert sel.sources == (SourceType.MEMORY,)

    def test_coding_with_memory_base_sources(self):
        sel = select(intent="coding", needs_memory=True)
        assert sel.strategy == RetrievalStrategy.PROJECT_FIRST
        assert sel.sources == (SourceType.PROJECT, SourceType.KNOWLEDGE)


class TestDeterminism:
    def test_same_inputs_same_selection(self):
        kw = dict(needs_grounding=True, signal_types=["temporal"],
                  signal_strengths={"temporal": "medium"}, needs_memory=True)
        assert select(**kw) == select(**kw)

    def test_repeated_calls_equal(self):
        a = select(needs_grounding=True, signal_types=["dynamic"], needs_project=True)
        b = select(needs_grounding=True, signal_types=["dynamic"], needs_project=True)
        assert a.sources == b.sources
        assert a.strategy == b.strategy
        assert a.reason == b.reason


class TestPolicyParity:
    """Selection must drive identical resolve() output to pre-extraction."""

    def test_strategy_flows_into_plan(self):
        sel = select(needs_grounding=True, signal_types=["temporal"],
                     signal_strengths={"temporal": "medium"}, needs_memory=True)
        plan = RetrievalPolicy.resolve(
            needs_grounding=True,
            signal_types=["temporal"],
            signal_strengths={"temporal": "medium"},
            has_external=False,
            intent="conversation",
            needs_memory=True,
        )
        assert sel.strategy == plan.strategy
        assert sel.reason == plan.reason

    def test_context_sources_inserted_by_policy(self):
        sel = select(needs_grounding=True, signal_types=["dynamic"], needs_memory=True)
        plan = RetrievalPolicy.resolve(
            needs_grounding=True, signal_types=["dynamic"],
            signal_strengths={}, has_external=False, intent="conversation",
            needs_memory=True,
        )
        assert SourceType.MEMORY not in sel.sources  # base sources
        assert plan.sources == [SourceType.MEMORY, SourceType.WEB]  # context inserted

    def test_file_never_selected(self):
        cases = [
            dict(needs_grounding=True, signal_types=["dynamic"]),
            dict(needs_grounding=True, signal_types=["temporal"],
                 signal_strengths={"temporal": "medium"}, needs_memory=True),
            dict(intent="coding"),
            dict(intent="work"),
            dict(needs_memory=True),
        ]
        for kw in cases:
            assert SourceType.FILE not in select(**kw).sources
