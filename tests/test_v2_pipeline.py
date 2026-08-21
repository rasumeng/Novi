"""Integration tests for the v2 pipeline: Orchestrator → JobManager → Runtime."""
import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def orchestrator():
    from cozmo.orchestrator.orchestrator import Orchestrator
    from cozmo.capabilities import CapabilityRegistry
    from cozmo.capabilities.builtin import register_builtin_capabilities
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    return Orchestrator(capability_registry=registry)


@pytest.fixture
def job_manager():
    from cozmo.jobs.manager import JobManager
    return JobManager()


@pytest.fixture
def backend():
    """Minimal backend dict simulating the shared backend."""
    from cozmo.runtime.tool_registry import ToolRegistry
    from cozmo.tools import TOOL_REGISTRY

    mm = MagicMock()
    registry = ToolRegistry()
    for name, fn in TOOL_REGISTRY.items():
        registry.register(name, fn)
    simple_llm = MagicMock()
    return {
        "model_service": mm,
        "simple_llm": simple_llm,
        "registry": registry,
        "skills": {},
    }


@pytest.fixture
def runtime(backend):
    from cozmo.runtime.runtime import CozmoRuntime
    from cozmo.runtime.event_bus import EventBus

    rt = CozmoRuntime(
        model_service=backend["model_service"],
        registry=backend["registry"],
        skills=backend["skills"],
        event_bus=EventBus(),
    )
    return rt


# ── Test: Orchestrator produces correct plans ──────────────────────────────


class TestOrchestrator:
    def test_conversation_intent(self, orchestrator):
        plan = orchestrator.plan("Hello, how are you?")
        assert plan.goal is not None
        assert plan.goal.intent.value == "conversation"
        assert any(c.id == "conversation" for c in plan.capabilities)
        assert plan.strategy.value == "respond"
        assert plan.max_steps <= 5

    def test_coding_intent(self, orchestrator):
        plan = orchestrator.plan("Write a Python function to sort a list")
        assert plan.goal.intent.value == "coding"
        assert "coding" in plan.capabilities or "coding" in str(plan.capabilities)
        assert plan.strategy.value == "execute"
        assert plan.tools is not None

    def test_research_intent(self, orchestrator):
        plan = orchestrator.plan("What is the latest news about AI?")
        assert plan.goal.intent.value == "research"
        assert plan.strategy.value == "research"

    def test_force_capability_override(self, orchestrator):
        plan = orchestrator.plan("Hello", force_capability="coding")
        assert "coding" in plan.capabilities or "coding" in str(plan.capabilities)

    def test_plan_has_tools(self, orchestrator):
        plan = orchestrator.plan("Refactor this Python code")
        assert len(plan.tools) > 0


# ── Test: JobManager lifecycle ─────────────────────────────────────────────


class TestJobManager:
    def test_submit_and_start(self, job_manager):
        job = job_manager.submit(task_id="task-1", strategy="execute")
        assert job.id.startswith("job-")
        assert job.status.value == "pending"
        assert job_manager.start(job.id) is True
        assert job.status.value == "running"

    def test_submit_pause_resume(self, job_manager):
        job = job_manager.submit(task_id="task-2")
        job_manager.start(job.id)
        from cozmo.jobs.job import Checkpoint
        cp = Checkpoint(job_id=job.id, step=3)
        assert job_manager.pause(job.id, cp) is True
        assert job.status.value == "paused"
        new_job = job_manager.resume(job.id)
        assert new_job is not None
        assert new_job.status.value == "queued"
        assert new_job.metadata.get("resumed_from") == job.id

    def test_submit_cancel(self, job_manager):
        job = job_manager.submit(task_id="task-3")
        job_manager.start(job.id)
        assert job_manager.cancel(job.id) is True
        assert job.status.value == "cancelled"

    def test_submit_complete(self, job_manager):
        job = job_manager.submit(task_id="task-4")
        job_manager.start(job.id)
        assert job_manager.complete(job.id, result="done") is True
        assert job.status.value == "done"

    def test_list_by_task(self, job_manager):
        job_manager.submit(task_id="task-group")
        job_manager.submit(task_id="task-group")
        job_manager.submit(task_id="task-other")
        group_jobs = job_manager.list_by_task("task-group")
        assert len(group_jobs) == 2

    def test_retry(self, job_manager):
        job = job_manager.submit(task_id="task-retry", max_retries=3)
        job_manager.start(job.id)
        job_manager.complete(job.id, error="oops")
        retried = job_manager.retry(job.id)
        assert retried is not None
        assert retried.retry_count == 1

    def test_max_retries_exceeded(self, job_manager):
        job = job_manager.submit(task_id="task-maxretry", max_retries=0)
        assert job_manager.retry(job.id) is None


# ── Test: Runtime run_stream yields expected events ─────────────────────────


class TestRuntimeStream:
    def test_stream_yields_status_and_thinking(self, runtime):
        """The runtime should yield status/thinking events at minimum."""
        events = list(runtime.run_stream("say hello"))
        kinds = [e[0] for e in events]
        assert "status" in kinds
        assert "thinking" in kinds or "trace" in kinds
        assert "token" in kinds

    def test_stream_ends_with_token(self, runtime):
        """The stream should eventually yield at least one token."""
        events = list(runtime.run_stream("say hello"))
        tokens = [e for e in events if e[0] == "token"]
        assert len(tokens) >= 1

    def test_stream_stops_on_flag(self, runtime):
        """Setting stop_event should halt the stream early."""
        stop = threading.Event()
        runtime.stop_event = stop
        stop.set()
        events = list(runtime.run_stream("say hello"))
        assert len(events) < 4


# ── Test: End-to-end pipeline integration ───────────────────────────────────


class TestPipelineIntegration:
    def test_orchestrator_to_jobmanager(self, orchestrator, job_manager):
        """Orchestrator produces plan → JobManager creates job."""
        plan = orchestrator.plan("Write a Python script to count files")
        job = job_manager.submit(
            task_id=plan.task_id,
            strategy=plan.strategy.value,
            metadata={"intent": plan.goal.intent.value},
        )
        assert job is not None
        assert job.status.value == "pending"
        assert job_manager.start(job.id) is True

    def test_orchestrator_plan_has_tools_for_coding(self, orchestrator):
        """Coding intent should resolve to coding tools."""
        plan = orchestrator.plan("implement a Python function to sort a list")
        assert len(plan.tools) > 0
        has_workspace_tool = any(
            t in ("read_file", "glob", "write_file", "edit_file", "bash", "grep")
            for t in plan.tools
        )
        assert has_workspace_tool, f"No workspace tools found in {plan.tools}"

    def test_orchestrator_plan_has_tools_for_research(self, orchestrator):
        """Research intent should resolve to web tools."""
        plan = orchestrator.plan("Search the web for AI news")
        has_web_tool = any("web" in t or "search" in t for t in plan.tools)
        assert has_web_tool, f"No web tools found in {plan.tools}"

    def test_full_pipeline(self, orchestrator, job_manager, runtime):
        """Full flow: plan → job → runtime stream."""
        plan = orchestrator.plan("Say hello")
        job = job_manager.submit(
            task_id=plan.task_id,
            strategy=plan.strategy.value,
            metadata={"intent": plan.goal.intent.value, "tools": plan.tools},
        )
        job_manager.start(job.id)

        events = list(runtime.run_stream("Say hello"))
        kinds = [e[0] for e in events]
        assert "token" in kinds
        assert "status" in kinds

        job_manager.complete(job.id, result="done")
        assert job.status.value == "done"


# ── Test: Session class (WebSocket integration) ────────────────────────────


class TestSession:
    @pytest.fixture
    def session(self):
        from cozmo.webui_server import Session
        from cozmo.runtime.event_bus import EventBus

        loop = MagicMock()
        loop.call_soon_threadsafe = lambda fn, *a: fn(*a) if callable(fn) else None
        # Build a full production backend is slow (memory, project index, MCP).
        # Session wiring is what's under test — use a real EventBus plus mocks.
        backend = (MagicMock(), MagicMock(), MagicMock(), EventBus())
        with patch("cozmo.webui_server.build_runtime", return_value=backend):
            sess = Session(loop=loop)
        return sess

    def test_session_has_runtime(self, session):
        assert session.runtime is not None

    def test_session_has_orchestrator(self, session):
        assert session.orchestrator is not None

    def test_session_has_job_manager(self, session):
        assert session.job_manager is not None

    def test_session_has_event_bus(self, session):
        assert session.event_bus is not None

    def test_session_not_busy_initially(self, session):
        assert not session.busy

    def test_session_permission_flow(self, session):
        allowed = []
        def answer():
            time.sleep(0.05)
            session.answer_permission(True)
        t = threading.Thread(target=answer, daemon=True)
        t.start()
        result = session._ask_permission("read", {"path": "test.txt"})
        assert result is True

    def test_session_permission_stale_response_dropped(self, session):
        """A response for the wrong request id must never resolve the gate."""
        session._perm_request_id = "perm-current"
        session._perm_allowed = False
        session._perm_event.clear()
        session.answer_permission(True, request_id="perm-stale")
        assert session._perm_allowed is False
        assert session._perm_event.is_set() is False

        session.answer_permission(True, request_id="perm-current")
        assert session._perm_allowed is True
        assert session._perm_event.is_set() is True

    def test_session_permission_stop_invalidates_request(self, session):
        """stop() must invalidate the in-flight request and unblock with deny."""
        session._perm_request_id = "perm-abc"
        session._perm_allowed = False
        session._perm_event.clear()
        session.stop()
        assert session._perm_request_id == ""
        assert session._perm_allowed is False
        assert session._perm_event.is_set() is True
        # a late answer for the invalidated request is dropped, so the event
        # never flips to granted and the worker, if still waiting, fails closed
        session._perm_event.clear()
        session.answer_permission(True, request_id="perm-abc")
        assert session._perm_allowed is False
        assert not session._perm_event.is_set()

    def test_session_plan_flow(self, session):
        approved = []
        def answer():
            time.sleep(0.05)
            session.answer_plan(True)
        t = threading.Thread(target=answer, daemon=True)
        t.start()
        result = session._ask_plan("1. Do this\n2. Do that")
        assert result is True

    def test_event_bus_bridging(self, session):
        """EventBus events should be forwarded to the session event queue."""
        session.event_bus.emit("tool_called", tool="read", args={"path": "/x"}, call_id="c1")
        time.sleep(0.05)
        events = []
        while not session.events.empty():
            events.append(session.events.get_nowait())
        tool_calls = [e for e in events if e.get("type") == "tool_call"]
        assert len(tool_calls) >= 1
