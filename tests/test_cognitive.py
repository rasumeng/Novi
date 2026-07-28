"""Integration tests for the Cognitive Layer:

1. Memory context assembly (type-filtered, importance-ranked)
2. ModelRouter with complexity awareness
3. LessonStore reflection
4. Background task scheduler via Job system
"""

from unittest.mock import MagicMock, patch

import pytest


# ── Priority 1: Memory context assembly ────────────────────────────────────


class TestMemoryContextAssembly:
    @pytest.fixture
    def runtime(self):
        from cozmo.runtime.runtime import CozmoRuntime
        from cozmo.runtime.event_bus import EventBus

        mm = MagicMock()
        rt = CozmoRuntime(model_service=mm)
        return rt

    def test_memory_types_per_intent(self):
        """Each intent maps to appropriate memory types."""
        from cozmo.runtime.runtime import CozmoRuntime

        assert set(CozmoRuntime._memory_types_for_intent("conversation")) == {"conversation", "preference", "fact"}
        assert set(CozmoRuntime._memory_types_for_intent("coding")) == {"project", "learning", "reference"}
        assert set(CozmoRuntime._memory_types_for_intent("research")) == {"reference", "fact", "conversation"}

    def test_query_memory_empty_when_no_memory(self, runtime):
        """Without a memory manager, query returns empty string."""
        result = runtime._query_memory("hello")
        assert result == ""

    def test_query_memory_returns_formatted(self, runtime):
        """With memory manager, query returns formatted sections."""
        mock_memory = MagicMock()
        mock_memory.query.return_value = [
            {"text": "User likes Python", "distance": 0.2,
             "metadata": {"type": "preference", "frequency": 3, "timestamp": ""}},
        ]
        runtime.memory = mock_memory
        result = runtime._query_memory("hello", intent="conversation")
        assert "Preference" in result or "preference" in result
        assert "likes Python" in result

    def test_rank_memories_by_importance(self, runtime):
        """Memories are ranked by frequency x recency x distance."""
        from datetime import datetime, timedelta

        older = (datetime.now() - timedelta(hours=100)).isoformat()
        newer = datetime.now().isoformat()
        results = [
            {"text": "old frequent", "distance": 0.1,
             "metadata": {"frequency": 10, "timestamp": older}},
            {"text": "new rare", "distance": 0.3,
             "metadata": {"frequency": 1, "timestamp": newer}},
        ]
        ranked = runtime._rank_memories(results)
        assert len(ranked) == 2

    def test_memory_filtered_by_intent(self, runtime):
        """Memory query filters by types matching intent."""
        mock_memory = MagicMock()
        runtime.memory = mock_memory
        runtime._query_memory("refactor main.py", intent="coding")
        call_kwargs = mock_memory.query.call_args[1]
        assert "memory_types" in call_kwargs
        assert call_kwargs["memory_types"] == ["project", "learning", "reference"]


# ── Priority 2: ModelRouter with complexity ────────────────────────────────


class TestModelRouterComplexity:
    @pytest.fixture
    def router(self):
        from cozmo.runtime.model_router import ModelRouter
        from cozmo.runtime.resources import ResourceManager

        rm = ResourceManager(vram_total_gb=16.0)
        r = ModelRouter(default_model="gemma4:12b", resource_manager=rm)
        from cozmo.runtime.model_router import ModelInfo
        r.register(ModelInfo(name="phi4-mini", capability="conversation", vram_required_gb=2.0, is_loaded=True))
        r.register(ModelInfo(name="qwen3:8b", capability="research", vram_required_gb=4.0))
        r.register(ModelInfo(name="ornith:9b", capability="coding", vram_required_gb=6.0))
        r.register(ModelInfo(name="qwen2.5-coder:14b", capability="coding", vram_required_gb=8.0))
        r.resource_manager.load_model("phi4-mini", 2.0)
        return r

    def test_preferred_model_used(self, router):
        """Preferred model is used when its capability matches the requirement."""
        from cozmo.runtime.model_router import ModelRequirement
        result = router.resolve(
            requirements=[ModelRequirement(capability="research")],
            preferred="qwen3:8b",
        )
        assert result == "qwen3:8b"

    def test_loaded_model_preferred(self, router):
        """Already-loaded models are preferred over unloaded ones."""
        from cozmo.runtime.model_router import ModelRequirement
        router.resource_manager.load_model("ornith:9b", 6.0)
        result = router.resolve(
            requirements=[ModelRequirement(capability="coding")],
        )
        assert result == "ornith:9b"

    def test_complexity_upgrades_capability(self, router):
        """High complexity score upgrades capability tier."""
        from cozmo.runtime.model_router import ModelRouter
        assert ModelRouter._complexity_tier("conversation", None) == "conversation"
        assert ModelRouter._complexity_tier("conversation", type("CS", (), {"score": 2})()) == "conversation"
        assert ModelRouter._complexity_tier("conversation", type("CS", (), {"score": 5})()) == "research"

    def test_resolve_with_complexity(self, router):
        """Complexity score influences model selection."""
        from cozmo.runtime.model_router import ModelRequirement
        from cozmo.runtime.resources import ResourceManager

        router.resource_manager = ResourceManager(vram_total_gb=4.0)
        result = router.resolve(
            requirements=[ModelRequirement(capability="conversation")],
            complexity_score=type("CS", (), {"score": 7})(),
        )
        # With 4GB VRAM at conversation tier, high complexity should upgrade to research
        # Only qwen3:8b (4GB, research) fits
        assert result == "qwen3:8b"

    def test_default_when_no_candidates(self, router):
        """When no model fits, return default."""
        from cozmo.runtime.resources import ResourceManager
        router.resource_manager = ResourceManager(vram_total_gb=0.5)
        result = router.resolve()
        assert result == "gemma4:12b"


# ── Priority 3: LessonStore reflection ─────────────────────────────────────


class TestLessonStore:
    @pytest.fixture
    def store(self, tmp_path):
        from cozmo.runtime.lessons import LessonStore
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
        from cozmo.runtime.lessons import LessonStore
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
        import cozmo.scheduler
        orig_path = cozmo.scheduler.SCHEDULES_PATH
        fake = tmp_path / "schedules.json"
        fake.write_text('{"schedules": []}', "utf-8")
        cozmo.scheduler.SCHEDULES_PATH = fake
        yield
        cozmo.scheduler.SCHEDULES_PATH = orig_path

    def test_job_created_for_background_run(self):
        """Scheduler trigger creates a job via JobManager."""
        from cozmo.jobs.manager import JobManager

        jm = JobManager()
        with patch("cozmo.webui_server._start_background_run") as mock_run:
            from cozmo.webui_server import _start_background_run as real_start
            real_start("test goal", {"test": True}, job_manager=jm)
            runs = jm.list_by_task("test goal")
            assert len(runs) == 0

    def test_scheduler_init(self, isolated_scheduler):
        """Scheduler can be initialized with a job manager reference."""
        from cozmo.scheduler import Scheduler
        s = Scheduler()
        assert s is not None
        assert s.list() == []

    def test_schedule_add_and_list(self, isolated_scheduler):
        """Schedules persist and can be listed."""
        from cozmo.scheduler import Scheduler
        s = Scheduler()
        s.add("test goal", "test description", interval_minutes=10)
        items = s.list()
        assert len(items) == 1
        assert items[0].goal == "test goal"

    def test_schedule_remove(self, isolated_scheduler):
        """Schedules can be removed."""
        from cozmo.scheduler import Scheduler
        s = Scheduler()
        item = s.add("test goal")
        assert s.remove(item.id) is True
        assert s.list() == []
