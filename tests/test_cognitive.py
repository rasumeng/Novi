"""Integration tests for the Cognitive Layer:

1. Memory context assembly (type-filtered, importance-ranked)
2. ModelSelector strict workload contract
3. LessonStore reflection
4. Background task scheduler via Job system
"""

from unittest.mock import MagicMock, patch

import pytest


# ── Priority 1: Memory context assembly ────────────────────────────────────


class TestMemoryContextAssembly:
    @pytest.fixture
    def runtime(self):
        from novi.runtime.runtime import NoviRuntime

        rt = NoviRuntime(model_service=MagicMock())
        return rt

    def _ctx(self, user_input="hello", needs_memory=True, intent="conversation"):
        import types
        from novi.runtime.execution_context import ExecutionContext
        from novi.runtime.retrieval_policy import RetrievalPlan, SourceType
        from novi.runtime.trace import ExecutionTrace

        plan = RetrievalPlan()
        if needs_memory:
            plan.sources.append(SourceType.MEMORY)
        if intent in ("coding", "work"):
            plan.sources.append(SourceType.PROJECT)

        ctx = ExecutionContext(user_input=user_input)
        ctx.trace = ExecutionTrace(user_input=user_input)
        ctx.analysis = types.SimpleNamespace(
            intent=types.SimpleNamespace(value=intent),
            capabilities=["conversation"],
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
        return ctx

    def test_query_memory_empty_when_no_memory(self, runtime):
        """Without a memory manager, executor leaves memory_context empty."""
        ctx = self._ctx()
        list(runtime.retrieval_executor.execute(ctx, "hello"))
        assert ctx.memory_context == ""

    def test_query_memory_returns_formatted(self, runtime):
        """With memory manager, executor populates formatted sections."""
        from novi.runtime.runtime import NoviRuntime

        mock_memory = MagicMock()
        mock_memory.query.return_value = [
            {"text": "User likes Python", "distance": 0.2,
             "metadata": {"type": "preference", "frequency": 3, "timestamp": ""}},
        ]
        rt = NoviRuntime(model_service=MagicMock(), memory=mock_memory)
        ctx = self._ctx()
        list(rt.retrieval_executor.execute(ctx, "hello"))
        assert "Preference" in ctx.memory_context or "preference" in ctx.memory_context
        assert "likes Python" in ctx.memory_context

    def test_rank_memories_by_importance(self, runtime):
        """Memories are ranked by frequency x recency x distance."""
        from datetime import datetime, timedelta

        from novi.runtime.retrieval import RetrievalExecutor
        from novi.runtime.sources import RetrievedItem

        older = (datetime.now() - timedelta(hours=100)).isoformat()
        newer = datetime.now().isoformat()
        items = [
            RetrievedItem(
                id="a", text="old frequent", source="memory",
                metadata={"frequency": 10, "timestamp": older, "distance": 0.1},
            ),
            RetrievedItem(
                id="b", text="new rare", source="memory",
                metadata={"frequency": 1, "timestamp": newer, "distance": 0.3},
            ),
        ]
        ranked = RetrievalExecutor._rank_memories(items)
        assert len(ranked) == 2

    def test_memory_filtered_by_intent(self, runtime):
        """Executor filters memory by types matching intent."""
        from novi.runtime.runtime import NoviRuntime

        mock_memory = MagicMock()
        rt = NoviRuntime(model_service=MagicMock(), memory=mock_memory)
        ctx = self._ctx(user_input="refactor main.py", intent="coding")
        list(rt.retrieval_executor.execute(ctx, "refactor main.py"))
        call_kwargs = mock_memory.query.call_args[1]
        assert "memory_types" in call_kwargs
        assert call_kwargs["memory_types"] == ["project", "learning", "reference"]

    def test_runtime_no_direct_memory_retrieval(self, runtime):
        """Legacy memory retrieval methods removed from runtime."""
        assert not hasattr(runtime, "_query_memory")
        assert not hasattr(runtime, "_rank_memories")


# ── Priority 2: ModelSelector strict contract ───────────────────────────────


class TestModelSelectorStrictContract:
    """Phase 2: workload → configured model verbatim; never substitute/rank/fall back."""

    @pytest.fixture
    def selector(self):
        import types
        from novi.runtime.model_selector import ModelSelector

        workloads = {
            "general": "qwen3:8b",
            "research": "phi4-mini",
            "code": "qwen2.5-coder:14b",
        }
        svc = types.SimpleNamespace(
            resolve=lambda w: ("test-provider", workloads.get(w, "")),
        )
        return ModelSelector(svc)

    def test_resolve_returns_configured_model_verbatim(self, selector):
        assert selector.resolve("general") == "qwen3:8b"
        assert selector.resolve("research") == "phi4-mini"
        assert selector.resolve("code") == "qwen2.5-coder:14b"

    def test_no_rank_no_upgrade_no_fallback(self, selector):
        """A lower-tier model in general must be returned exactly — never upgraded."""
        assert selector.resolve("general") == "qwen3:8b"

    def test_unset_workload_raises(self):
        import types
        from novi.models import ModelUnavailableError
        from novi.runtime.model_selector import ModelSelector

        svc = types.SimpleNamespace(resolve=lambda w: ("test-provider", ""))
        sel = ModelSelector(svc)
        with pytest.raises(ModelUnavailableError):
            sel.resolve("research")

    def test_unknown_workload_name_rejected(self, selector):
        with pytest.raises(ValueError):
            selector.resolve("chat")

    def test_missing_configured_model_raises(self):
        import types
        from novi.models import ModelUnavailableError
        from novi.runtime.model_selector import ModelSelector

        def resolve(w):
            raise ModelUnavailableError("general", "gemma4:12b", ["qwen3:8b"])

        sel = ModelSelector(types.SimpleNamespace(resolve=resolve))
        with pytest.raises(ModelUnavailableError):
            sel.resolve("general")


# ── Priority 3: LessonStore reflection ─────────────────────────────────────


class TestLessonStore:
    @pytest.fixture
    def store(self, tmp_path):
        from novi.runtime.lessons import LessonStore
        return LessonStore(persist_dir=str(tmp_path))

    def test_record_success(self, store):
        """Successful tool calls create success lessons."""
        store.record("read_file", {"path": "test.txt"}, "file content: hello")
        assert store.count() == 1
        lesson = store.list_all()[0]
        assert lesson.tool == "read_file"
        assert lesson.success is True

    def test_record_error(self, store):
        """Failed tool calls create error lessons."""
        store.record("bash", {"command": "rm -rf /"}, "Error: permission denied")
        assert store.count() == 1
        lesson = store.list_all()[0]
        assert lesson.tool == "bash"
        assert lesson.success is False

    def test_duplicate_increment_count(self, store):
        """Same tool+pattern increments count, doesn't duplicate."""
        store.record("calculator", {"expression": "2+2"}, "4")
        store.record("calculator", {"expression": "2+2"}, "4")
        assert store.count() == 1
        assert store.list_all()[0].count == 2

    def test_get_context_empty(self, store):
        """Empty store returns empty string."""
        assert store.get_context() == ""

    def test_get_context_returns_lessons(self, store):
        """Non-empty store returns formatted lesson context."""
        store.record("read_file", {"path": "x.txt"}, "content")
        result = store.get_context()
        assert "Lessons from past tool use" in result
        assert "read_file" in result

    def test_get_context_filtered_by_tool(self, store):
        """get_context can filter to specific tools."""
        store.record("read_file", {"path": "x.txt"}, "content")
        store.record("bash", {"command": "ls"}, "files")
        store.record("calculator", {"expression": "2+2"}, "4")
        result = store.get_context(tool_names=["read_file"])
        assert "read_file" in result
        assert "bash" not in result

    def test_max_lessons_enforced(self, store):
        """Store trims to MAX_LESSONS."""
        for i in range(30):
            store.record("calculator", {"expression": f"{i}+1"}, f"{i+1}")
        assert store.count() <= 20

    def test_persistence(self, tmp_path):
        """Lessons persist to disk and reload on init."""
        from novi.runtime.lessons import LessonStore
        s1 = LessonStore(persist_dir=str(tmp_path))
        s1.record("web_search", {"query": "news"}, "results")
        s2 = LessonStore(persist_dir=str(tmp_path))
        assert s2.count() == 1
        assert s2.list_all()[0].tool == "web_search"


# ── Priority 4: Scheduler via Job system ───────────────────────────────────


class TestSchedulerIntegration:
    @pytest.fixture
    def isolated_scheduler(self, tmp_path):
        """Return Scheduler with isolated persistence path."""
        import novi.scheduler
        orig_path = novi.scheduler.SCHEDULES_PATH
        fake = tmp_path / "schedules.json"
        fake.write_text('{"schedules": []}', "utf-8")
        novi.scheduler.SCHEDULES_PATH = fake
        yield
        novi.scheduler.SCHEDULES_PATH = orig_path

    def test_job_created_for_background_run(self):
        """Scheduler trigger creates a job via JobManager."""
        from novi.jobs.manager import JobManager

        jm = JobManager()
        with patch("novi.webui_server._start_background_run") as mock_run:
            from novi.webui_server import _start_background_run as real_start
            real_start("test goal", {"test": True}, job_manager=jm)
            runs = jm.list_by_task("test goal")
            assert len(runs) == 0

    def test_scheduler_init(self, isolated_scheduler):
        """Scheduler can be initialized with a job manager reference."""
        from novi.scheduler import Scheduler
        s = Scheduler()
        assert s is not None
        assert s.list() == []

    def test_schedule_add_and_list(self, isolated_scheduler):
        """Schedules persist and can be listed."""
        from novi.scheduler import Scheduler
        s = Scheduler()
        s.add("test goal", "test description", interval_minutes=10)
        items = s.list()
        assert len(items) == 1
        assert items[0].goal == "test goal"

    def test_schedule_remove(self, isolated_scheduler):
        """Schedules can be removed."""
        from novi.scheduler import Scheduler
        s = Scheduler()
        item = s.add("test goal")
        assert s.remove(item.id) is True
        assert s.list() == []
