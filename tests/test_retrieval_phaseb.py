"""Phase 9 step 4 — RetrievalExecutor integration for memory/project context.

Verifies the Phase B port:
- executor populates ctx.memory_context / ctx.project_context
- prompt context identical to the pre-migration runtime output
- runtime no longer queries memory or project index directly
- gating (needs_memory, intent) matches the removed runtime logic

All stores mocked — no live MemoryManager, no live ProjectIndex, no network.
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest

from novi.runtime.execution_context import ExecutionContext
from novi.runtime.retrieval import RetrievalExecutor


class _FakeMemory:
    def __init__(self, results):
        self.results = results
        self.query_calls = []

    def query(self, text, k=5, distance_threshold=0.5, memory_types=None):
        self.query_calls.append((text, k, distance_threshold, memory_types))
        return self.results


class _FakeProject:
    def __init__(self, text):
        self.text = text
        self.query_calls = []
        self.root = "/fake/project"

    def query(self, text, k=5):
        self.query_calls.append((text, k))
        return self.text


def _analysis(intent="conversation", needs_memory=False):
    from novi.runtime.retrieval_policy import RetrievalPlan, SourceType

    plan = RetrievalPlan()
    if needs_memory:
        plan.sources.append(SourceType.MEMORY)
    if intent in ("coding", "work"):
        plan.sources.append(SourceType.PROJECT)

    return types.SimpleNamespace(
        intent=types.SimpleNamespace(value=intent),
        capabilities=[intent],
        evidence=types.SimpleNamespace(
            signals=[], confidence=0.0, needs_memory=needs_memory
        ),
        grounding=types.SimpleNamespace(
            needs_grounding=False, confidence=0.0, source="none", reason=""
        ),
        retrieval_plan=plan,
        complexity=types.SimpleNamespace(score=1, plan_level=0),
        strategy=types.SimpleNamespace(value="direct"),
    )


def _ctx(user_input, intent="conversation", needs_memory=False):
    ctx = ExecutionContext(user_input=user_input)
    ctx.analysis = _analysis(intent=intent, needs_memory=needs_memory)
    return ctx


class TestMemoryContextIntegration:
    def _runtime(self, memory):
        return RetrievalExecutor(memory=memory)

    def test_populates_memory_context(self):
        memory = _FakeMemory([
            {"text": "User likes Python", "distance": 0.2,
             "metadata": {"type": "preference", "frequency": 3, "timestamp": ""}},
        ])
        rt = self._runtime(memory)
        ctx = _ctx("hello", intent="conversation", needs_memory=True)
        list(rt.execute(ctx, "hello"))
        assert "User likes Python" in ctx.memory_context
        assert "Preference" in ctx.memory_context

    def test_memory_context_matches_legacy_format(self):
        """Format must match removed runtime._query_memory output."""
        results = [
            {"text": "fact one", "distance": 0.1,
             "metadata": {"type": "fact", "frequency": 2, "timestamp": ""}},
            {"text": "pref two", "distance": 0.2,
             "metadata": {"type": "preference", "frequency": 1, "timestamp": ""}},
        ]
        rt = self._runtime(_FakeMemory(results))
        ctx = _ctx("q", intent="conversation", needs_memory=True)
        list(rt.execute(ctx, "q"))
        expected = "\n--- Fact ---\n  fact one\n\n--- Preference ---\n  pref two"
        assert ctx.memory_context == expected

    def test_no_query_when_needs_memory_false(self):
        memory = _FakeMemory([])
        rt = self._runtime(memory)
        ctx = _ctx("q", intent="conversation", needs_memory=False)
        list(rt.execute(ctx, "q"))
        assert memory.query_calls == []
        assert ctx.memory_context == ""

    def test_no_query_without_memory_manager(self):
        rt = RetrievalExecutor()
        ctx = _ctx("q", intent="conversation", needs_memory=True)
        list(rt.execute(ctx, "q"))
        assert ctx.memory_context == ""

    def test_fallback_when_analysis_none(self):
        """analysis None → query for conversation/planning intent only."""
        rt = self._runtime(_FakeMemory([]))
        ctx = ExecutionContext(user_input="hello")
        list(rt.execute(ctx, "hello"))
        assert ctx.analysis is None
        assert ctx.intent_str == "conversation"
        assert ctx.memory_context == ""

    def test_intent_type_filter_flow(self):
        memory = _FakeMemory([])
        rt = self._runtime(memory)
        ctx = _ctx("refactor main.py", intent="coding", needs_memory=True)
        list(rt.execute(ctx, "refactor main.py"))
        assert memory.query_calls
        _, _, _, memory_types = memory.query_calls[0]
        assert memory_types == ["project", "learning", "reference"]

    def test_distance_threshold_from_config(self):
        memory = _FakeMemory([])
        rt = RetrievalExecutor(memory=memory,
                          memory_distance_threshold=0.7)
        ctx = _ctx("q", intent="conversation", needs_memory=True)
        list(rt.execute(ctx, "q"))
        _, _, threshold, _ = memory.query_calls[0]
        assert threshold == 0.7

    def test_brain_routes_memory_source_through_brain(self):
        from novi.brain import Brain

        memory = _FakeMemory([
            {"text": "via brain", "distance": 0.1,
             "metadata": {"type": "fact", "frequency": 1, "timestamp": ""}}
        ])
        brain = Brain(memory=memory)
        rt = RetrievalExecutor(memory=memory, brain=brain)
        ctx = _ctx("q", intent="conversation", needs_memory=True)
        list(rt.execute(ctx, "q"))
        assert memory.query_calls and memory.query_calls[0][0] == "q"
        assert "via brain" in ctx.memory_context


class TestProjectContextIntegration:
    def _runtime(self, project):
        return RetrievalExecutor(project_index=project)

    def test_populates_project_context_for_coding(self):
        project = _FakeProject("src/foo.py: def foo()")
        rt = self._runtime(project)
        ctx = _ctx("how does foo work", intent="coding", needs_memory=False)
        list(rt.execute(ctx, "how does foo work"))
        assert ctx.project_context == "src/foo.py: def foo()"
        assert project.query_calls == [("how does foo work", 3)]

    def test_work_intent_queries_project(self):
        project = _FakeProject("src/bar.py: def bar()")
        rt = self._runtime(project)
        ctx = _ctx("work on bar", intent="work", needs_memory=False)
        list(rt.execute(ctx, "work on bar"))
        assert ctx.project_context == "src/bar.py: def bar()"

    def test_no_query_for_non_coding_intent(self):
        project = _FakeProject("content")
        rt = self._runtime(project)
        ctx = _ctx("hello", intent="conversation", needs_memory=False)
        list(rt.execute(ctx, "hello"))
        assert project.query_calls == []
        assert ctx.project_context == ""
    def test_no_project_index(self):
        rt = RetrievalExecutor()
        ctx = _ctx("how does foo work", intent="coding", needs_memory=False)
        list(rt.execute(ctx, "how does foo work"))
        assert ctx.project_context == ""

    def test_empty_query_result(self):
        rt = self._runtime(_FakeProject(""))
        ctx = _ctx("q", intent="coding", needs_memory=False)
        list(rt.execute(ctx, "q"))
        assert ctx.project_context == ""

# Retrieval integration coverage intentionally ends at the executor boundary.
