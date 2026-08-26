"""Phase 9 step 6 — retrieval tools delegate to RetrievalSource adapters.

Regression tests verifying:
- search_memory / search_knowledge produce identical outputs through the
  MemoryRetrievalSource / KnowledgeRetrievalSource adapters
- adapters are invoked with the exact pre-unification call parameters
- coordinator web budgeting is unchanged (memory/knowledge are NOT web tools)
- tool error/empty/None-store paths are preserved
- no network access (all stores mocked)

No live memory, no live knowledge index, no live web.
"""

from __future__ import annotations

from unittest.mock import patch

import novi.tools.file_ops as file_ops
import novi.tools.memory_ops as memory_ops


class _FakeMemory:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def query(self, text, k=5, distance_threshold=0.5, memory_types=None):
        self.calls.append((text, k, distance_threshold, memory_types))
        return self.results


class _FakeKnowledgeIndex:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, k=5, rerank=True):
        self.calls.append((query, k, rerank))
        return self.results


def _memory_results():
    return [
        {"text": "User loves Python", "distance": 0.2, "score": 0.9,
         "metadata": {"type": "preference", "title": "Likes Python", "frequency": 3}},
        {"text": "User prefers async", "distance": 0.4, "score": 0.7,
         "metadata": {"type": "fact", "title": "", "frequency": 1}},
    ]


def _knowledge_results():
    return [
        {"text": "def foo():\n    return 1", "score": 0.8,
         "metadata": {"path": "src/foo.py", "title": "foo helper"}},
        {"text": "Async patterns", "score": 0.6,
         "metadata": {"path": "learnings/async.md", "title": ""}},
    ]


class TestSearchMemoryDelegation:
    def test_output_identical_to_legacy_format(self):
        mem = _FakeMemory(_memory_results())
        with patch.object(memory_ops, "get_memory_manager", return_value=mem):
            out = memory_ops.search_memory("python", k=5)
        expected = (
            "- **[preference] Likes Python** (score=0.90): User loves Python\n"
            "- **[fact] fact** (score=0.70): User prefers async"
        )
        assert out == expected

    def test_adapter_invoked_with_legacy_call_params(self):
        mem = _FakeMemory(_memory_results())
        with patch.object(memory_ops, "get_memory_manager", return_value=mem):
            memory_ops.search_memory("python", k=7)
        # distance_threshold=1.0 and k=min(k,20) match the pre-unification call.
        assert mem.calls == [("python", 7, 1.0, None)]

    def test_k_capped_at_20(self):
        mem = _FakeMemory([])
        with patch.object(memory_ops, "get_memory_manager", return_value=mem):
            memory_ops.search_memory("q", k=100)
        assert mem.calls[0][1] == 20

    def test_no_manager_error_string(self):
        with patch.object(memory_ops, "get_memory_manager", return_value=None):
            out = memory_ops.search_memory("q")
        assert out == "[error] Memory not initialized. Start a chat session first."

    def test_no_results_info_string(self):
        mem = _FakeMemory([])
        with patch.object(memory_ops, "get_memory_manager", return_value=mem):
            out = memory_ops.search_memory("q")
        assert out == "[info] No matching memories found."

    def test_failed_query_raises(self):
        class _BoomMemory(_FakeMemory):
            def query(self, text, k=5, distance_threshold=0.5, memory_types=None):
                raise RuntimeError("store down")

        mem = _BoomMemory([])
        with patch.object(memory_ops, "get_memory_manager", return_value=mem):
            try:
                memory_ops.search_memory("q")
                assert False, "expected RuntimeError"
            except RuntimeError as e:
                assert str(e) == "store down"


class TestSearchKnowledgeDelegation:
    def test_output_identical_to_legacy_format(self):
        ki = _FakeKnowledgeIndex(_knowledge_results())
        with patch.object(file_ops, "get_knowledge_index", return_value=ki):
            out = file_ops.search_knowledge("foo", k=5)
        expected = (
            "- **foo helper** (src/foo.py, score=0.80): def foo():     return 1\n"
            "- **** (learnings/async.md, score=0.60): Async patterns"
        )
        assert out == expected

    def test_adapter_invoked_with_legacy_call_params(self):
        ki = _FakeKnowledgeIndex(_knowledge_results())
        with patch.object(file_ops, "get_knowledge_index", return_value=ki):
            file_ops.search_knowledge("foo", k=3)
        # k=min(k,20), rerank=True (index default) match the pre-unification call.
        assert ki.calls == [("foo", 3, True)]

    def test_no_index_error_string(self):
        with patch.object(file_ops, "get_knowledge_index", return_value=None):
            out = file_ops.search_knowledge("q")
        assert out == "[error] Knowledge index not initialized. Start a chat session first."

    def test_no_results_info_string(self):
        ki = _FakeKnowledgeIndex([])
        with patch.object(file_ops, "get_knowledge_index", return_value=ki):
            out = file_ops.search_knowledge("q")
        assert out == "[info] No matching knowledge found."

    def test_failed_search_raises(self):
        class _BoomIndex(_FakeKnowledgeIndex):
            def search(self, query, k=5, rerank=True):
                raise RuntimeError("index down")

        ki = _BoomIndex([])
        with patch.object(file_ops, "get_knowledge_index", return_value=ki):
            try:
                file_ops.search_knowledge("q")
                assert False, "expected RuntimeError"
            except RuntimeError as e:
                assert str(e) == "index down"


class TestCoordinatorBudgetUnchanged:
    def test_memory_knowledge_are_not_web_tools(self):
        from novi.runtime.retrieval_coordinator import RetrievalCoordinator

        c = RetrievalCoordinator()
        assert not c.is_web_tool("search_memory")
        assert not c.is_web_tool("search_knowledge")
        assert not c.is_search_tool("search_memory")
        assert not c.is_fetch_tool("search_memory")

    def test_record_does_not_consume_web_budget(self):
        from novi.runtime.retrieval_coordinator import RetrievalBudget, RetrievalCoordinator

        coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=1, max_web_fetches=1))
        coord.record("search_memory", {"query": "q"}, "result")
        coord.record("search_knowledge", {"query": "q"}, "result")
        assert coord.budget.searches_used == 0
        assert coord.budget.fetches_used == 0

    def test_intercept_allows_memory_knowledge_through(self):
        from novi.runtime.retrieval_coordinator import RetrievalBudget, RetrievalCoordinator

        coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=0, max_web_fetches=0))
        assert coord.intercept("search_memory", {"query": "q"}) is None
        assert coord.intercept("search_knowledge", {"query": "q"}) is None


class TestToolExecutorIntegration:
    def test_tool_output_flows_through_pipeline_unchanged(self):
        """ToolResult.output equals direct tool output → trace preview identical."""
        from novi.runtime.retrieval_coordinator import RetrievalCoordinator
        from novi.runtime.tool_executor import ToolExecutor

        mem = _FakeMemory(_memory_results())
        ki = _FakeKnowledgeIndex(_knowledge_results())

        registry = _RegistryWith({})
        perms = _AllowAll()
        exe = ToolExecutor(
            registry=registry,
            perms=perms,
            lesson_store=_NoopLessons(),
            lc_tools={},
            tool_fallbacks={},
            max_tool_output=8000,
            perm_mode="bypass",
        )
        coord = RetrievalCoordinator()

        with patch.object(memory_ops, "get_memory_manager", return_value=mem):
            result = exe.execute("search_memory", {"query": "python", "k": 5}, coordinator=coord)
        assert result.success is True
        assert "User loves Python" in result.output

        with patch.object(file_ops, "get_knowledge_index", return_value=ki):
            result = exe.execute("search_knowledge", {"query": "foo", "k": 5}, coordinator=coord)
        assert result.success is True
        assert "foo helper" in result.output

    def test_failed_retrieval_marks_tool_failure(self):
        """Adapter failure surfaces as a ToolExecutor failure, not success."""
        from novi.runtime.tool_executor import ToolExecutor

        class _BoomMemory(_FakeMemory):
            def query(self, text, k=5, distance_threshold=0.5, memory_types=None):
                raise RuntimeError("store down")

        exe = ToolExecutor(
            registry=_RegistryWith({}),
            perms=_AllowAll(),
            lesson_store=_NoopLessons(),
            lc_tools={},
            tool_fallbacks={},
            max_tool_output=8000,
            perm_mode="bypass",
        )
        with patch.object(memory_ops, "get_memory_manager", return_value=_BoomMemory([])):
            result = exe.execute("search_memory", {"query": "q"}, coordinator=None)
        assert result.success is False
        assert "store down" in result.output


class _RegistryWith:
    """Minimal ToolRegistry stand-in registering tool functions."""

    def __init__(self, extra):
        from novi.tools import TOOL_REGISTRY
        from novi.runtime.tool_registry import ToolRegistry

        self._reg = ToolRegistry()
        for name, fn in TOOL_REGISTRY.items():
            self._reg.register(name, fn)

    def get(self, name):
        return self._reg.get(name)


class _AllowAll:
    def resolve(self, name, args, agent=""):
        return "allow"


class _NoopLessons:
    def record(self, *a, **k):
        pass
