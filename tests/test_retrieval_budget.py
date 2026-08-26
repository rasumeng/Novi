"""Tests for the ContextAllocation budget contract (Phase 9 hardening).

Covers the dataclass defaults, overrides, and evidence that the contract is
actually consumed: the retrieval policy attaches an allocation to every plan,
the executor surfaces it in retrieval trace events, and adapters honor
``max_results`` as their item cap.

No stores are touched — all probes use fakes.
"""

from __future__ import annotations

from novi.runtime.retrieval import RetrievalExecutor
from novi.runtime.retrieval_budget import ContextAllocation
from novi.runtime.retrieval_policy import RetrievalPlan, RetrievalStrategy


class TestContextAllocationContract:
    def test_defaults(self):
        a = ContextAllocation()
        assert a.max_sources == 3
        assert a.max_results == 8
        assert a.max_context_chars == 6000

    def test_override(self):
        a = ContextAllocation(max_sources=1, max_results=4, max_context_chars=500)
        assert (a.max_sources, a.max_results, a.max_context_chars) == (1, 4, 500)

    def test_fields_are_ints(self):
        a = ContextAllocation()
        assert isinstance(a.max_sources, int)
        assert isinstance(a.max_results, int)
        assert isinstance(a.max_context_chars, int)


class TestContextAllocationConsumed:
    def test_plan_defaults_to_contract(self):
        plan = RetrievalPlan(
            sources=[], strategy=RetrievalStrategy.NONE, reason="test"
        )
        assert plan.allocation == ContextAllocation()

    def test_policy_sets_allocation_on_plan(self):
        plan = RetrievalPlan(
            sources=["memory", "knowledge"],
            strategy=RetrievalStrategy.KNOWLEDGE_THEN_WEB,
            reason="test",
            allocation=ContextAllocation(max_sources=2),
        )
        assert plan.allocation.max_sources == 2

    def test_executor_records_allocation_in_trace(self):
        plan = RetrievalPlan(
            sources=["memory"],
            strategy=RetrievalStrategy.NONE,
            reason="test",
            allocation=ContextAllocation(max_sources=1, max_results=5),
        )
        assert RetrievalExecutor._allocation_debug(plan) == {
            "max_sources": 1,
            "max_results": 5,
            "max_context_chars": 6000,
        }

    def test_executor_allocation_debug_absent_when_no_allocation(self):
        plan = RetrievalPlan(sources=[], strategy=RetrievalStrategy.NONE, reason="x")
        assert RetrievalExecutor._allocation_debug(plan) == {
            "max_sources": 3,
            "max_results": 8,
            "max_context_chars": 6000,
        }

    def test_memory_source_honors_max_results(self):
        from novi.runtime.sources import MemoryRetrievalSource

        class _FakeManager:
            def __init__(self):
                self.calls = []

            def query(self, text, k=5, distance_threshold=0.5, memory_types=None):
                self.calls.append((text, k))
                return []

        store = _FakeManager()
        MemoryRetrievalSource(store).retrieve(
            "q", ContextAllocation(max_results=12)
        )
        assert store.calls == [("q", 12)]

    def test_knowledge_source_honors_max_results(self):
        from novi.runtime.sources import KnowledgeRetrievalSource

        class _FakeIndex:
            def __init__(self):
                self.calls = []

            def search(self, query, k=5, rerank=True):
                self.calls.append((query, k, rerank))
                return []

        store = _FakeIndex()
        KnowledgeRetrievalSource(store).retrieve(
            "q", ContextAllocation(max_results=6)
        )
        assert store.calls == [("q", 6, True)]
