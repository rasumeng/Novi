"""Milestone 5 Phase 6B — WebUI checkpoint regression.

Proves the real WebUI composition (``webui_server.build_runtime`` + the
``Session`` ExecutionCoordinator wiring) attaches the SAME JobLifecycle observer
as every other execution surface, so a normal WebUI execution persists step
checkpoints:

    WebUI execution
        → ExecutionCoordinator
        → JobManager creates Job
        → JobLifecycle.register(task_id, job_id)
        → Runtime EventBus (session bus)
        → plan.started / step.completed
        → JobLifecycle
        → Checkpoint persisted

Expected result:
    - exactly ONE Job for the run (registration prevents the plan.started
      fallback from creating a second Job)
    - Job registered with JobLifecycle while the run is active
    - Checkpoint exists after step completion, Checkpoint.step correct
    - completed steps correct
    - no duplicate history entries

Hermetic: a deterministic fake runtime harness + real stores; no Ollama,
Telegram, Discord, MCP, or network services.
"""

from types import SimpleNamespace

import pytest

from cozmo.jobs.job import JobStatus
from cozmo.jobs.manager import JobManager
from cozmo.jobs.persistence import JobStore
from cozmo.orchestrator import Orchestrator
from cozmo.orchestrator.intent import IntentDetector
from cozmo.orchestrator.task_store import TaskStore
from cozmo.orchestrator.task_types import (
    ExecutionPlan, ExecutionStrategy, Goal, IntentType,
)
from cozmo.planner.models import Plan, PlanStep
from cozmo.services.job_lifecycle import JobLifecycle


# ── harness (mirrors test_execution_surfaces / test_execution_coordinator) ──

class _FakeIntent(IntentDetector):
    def detect(self, user_input, history=None, has_images=False):
        return (IntentType.CODING, 1.0)


class _HarnessOrchestrator(Orchestrator):
    def __init__(self, task_store):
        super().__init__(intent_detector=_FakeIntent(), task_store=task_store)

    def plan(self, user_input, history=None, has_images=False,
             force_capability=None, force_model=None, conversation_id=None):
        task = self.task_store.get_or_create(
            conversation_id=conversation_id or "",
            goal_text=user_input[:500],
            intent=IntentType.CODING,
        )
        plan_obj = Plan(id="plan-1", task_id=task.id)
        for i in ("1", "2", "3"):
            plan_obj.add_step(PlanStep(id=f"plan-1-s{i}", plan_id="plan-1",
                                       description="build it"))
        task.plan = plan_obj
        self.task_store.update(task)
        return ExecutionPlan(
            task_id=task.id,
            goal=Goal(text=user_input, intent=IntentType.CODING),
            strategy=ExecutionStrategy.EXECUTE,
            capabilities=[],
            tools=[],
            model_spec={"model": "m1", "supports_tools": True},
            system_prompt="",
            messages=[],
            plan=plan_obj,
            context={"task_id": task.id, "is_continuation": False},
            max_steps=10,
            temperature=0.2,
            requires_approval=False,
        )


class _HarnessRuntime:
    """Deterministic runtime double: drives plan/step events on its bus."""

    def __init__(self, bus=None):
        self.bus = bus
        self.resume_from_seen = None

    def run_stream(self, user_input="", attachments=None,
                   execution_plan=None, conversation_id=None, resume_from=None):
        plan_obj = getattr(execution_plan, "plan", None)
        steps = list(getattr(plan_obj, "steps", None) or [])
        plan_id = getattr(plan_obj, "id", "") if plan_obj else ""
        task_id = execution_plan.task_id
        self.resume_from_seen = resume_from
        if self.bus is not None:
            self.bus.emit("plan.started", task_id=task_id, plan_id=plan_id,
                          step_count=len(steps))
        for i, step in enumerate(steps):
            if self.bus is not None:
                self.bus.emit("step.completed", task_id=task_id, plan_id=plan_id,
                              step_id=step.id, index=i, result="ok")
            yield ("token", step.description)
        if self.bus is not None:
            self.bus.emit("plan.completed", task_id=task_id, plan_id=plan_id,
                          result="done", step_count=len(steps))
        yield ("plan.completed", plan_id, "done")
        yield ("assistant", "result")

    def reset(self):
        pass


@pytest.fixture
def webui_backend(tmp_path, monkeypatch):
    """The shared WebUI backend dict, hermetic (temp stores, real JobLifecycle)."""
    import cozmo.jobs.persistence as persistence
    monkeypatch.setattr(persistence, "JOBS_DIR", tmp_path / "jobs")

    task_store = TaskStore(persist_dir=str(tmp_path / "tasks"))
    job_store = JobStore()
    job_manager = JobManager(store=job_store)
    lifecycle = JobLifecycle(job_manager, task_store=task_store)
    orchestrator = _HarnessOrchestrator(task_store)
    context = SimpleNamespace(job_lifecycle=lifecycle)

    backend = {
        "model_service": SimpleNamespace(),
        "memory": None,
        "registry": SimpleNamespace(),
        "project_index": None,
        "simple_llm": None,
        "skills": {},
        "brain": None,
        "orchestrator": orchestrator,
        "job_manager": job_manager,
        "task_store": task_store,
        "job_store": job_store,
        "continuation": None,
        "context": context,
    }
    return backend


def _fake_cozmo_runtime_factory(harness):
    """Return a CozmoRuntime replacement that returns the harness runtime."""

    def factory(**kw):
        harness.bus = kw.get("event_bus")
        return harness

    return factory


def _compose_webui_session(backend):
    """Replicate cozmo.webui_server.Session.__init__ coordinator wiring."""
    from cozmo.services.execution import ExecutionCoordinator

    return ExecutionCoordinator(
        orchestrator=backend["orchestrator"],
        job_manager=backend["job_manager"],
        task_store=backend["task_store"],
        continuation=backend.get("continuation"),
        job_lifecycle=backend["context"].job_lifecycle,
    )


def test_webui_execution_persists_checkpoint(webui_backend, monkeypatch):
    """A normal WebUI execution → exactly one Job → durable Checkpoint."""
    import cozmo.webui_server as ws
    backend = webui_backend
    monkeypatch.setattr(ws, "get_backend", lambda cfg: backend)

    harness = _HarnessRuntime()
    monkeypatch.setattr(ws, "CozmoRuntime", _fake_cozmo_runtime_factory(harness))

    runtime, orchestrator, job_manager, event_bus = ws.build_runtime({})
    assert harness.bus is event_bus          # session bus is the live bus
    assert runtime is harness                # the runtime the session will drive
    assert harness.resume_from_seen is None  # fresh run — no resume pointer

    coordinator = _compose_webui_session(backend)
    items = list(coordinator.run_stream(runtime, "build the widget",
                                        conversation_id="webui:1"))
    assert any(i[0] == "plan.completed" for i in items)

    # exactly ONE Task / ONE Job / ONE history entry
    task = backend["task_store"].list()[0]
    jobs = job_manager.list_by_task(task.id)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.status is JobStatus.COMPLETED

    # coordinator-created Job was registered with the shared JobLifecycle
    assert coordinator.job_id == job.id
    assert jobs[0].checkpoint is not None
    assert jobs[0].checkpoint.step == 3
    assert jobs[0].checkpoint.completed_steps == \
        ["plan-1-s1", "plan-1-s2", "plan-1-s3"]
    assert jobs[0].checkpoint.plan_id == "plan-1"

    # durable checkpoint file matches the in-memory Job checkpoint
    cp = backend["job_store"].load_checkpoint(job.id)
    assert cp is not None
    assert cp.step == 3
    assert cp.completed_steps == ["plan-1-s1", "plan-1-s2", "plan-1-s3"]

    # no duplicate history: the coordinator records one entry and the
    # registered JobLifecycle path never appends a second
    assert task.execution_history.count() == 1
    assert task.execution_history.all_job_ids == [job.id]


def test_webui_plan_started_does_not_double_create_job(webui_backend,
                                                       monkeypatch):
    """Invariant: one execution attempt → exactly one Job, even with a wired
    JobLifecycle whose plan.started fallback is ready to create a second."""
    import cozmo.webui_server as ws
    backend = webui_backend
    monkeypatch.setattr(ws, "get_backend", lambda cfg: backend)

    harness = _HarnessRuntime()
    monkeypatch.setattr(ws, "CozmoRuntime", _fake_cozmo_runtime_factory(harness))

    runtime, orchestrator, job_manager, event_bus = ws.build_runtime({})
    coordinator = _compose_webui_session(backend)
    list(coordinator.run_stream(runtime, "build the widget",
                                conversation_id="webui:2"))

    task = backend["task_store"].list()[0]
    assert len(job_manager.list_by_task(task.id)) == 1
    assert task.execution_history.count() == 1