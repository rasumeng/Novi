"""Milestone 5 Phase 5E-1 — ExecutionCoordinator lifecycle ownership.

Covers the shared execution seam extracted from WebUI ``Session.start_run``:

  - fresh run: 1 Task + 1 Plan + 1 Job + exactly 1 ExecutionHistory entry;
    Job finalised COMPLETED.
  - continuation run: reopen creates a NEW attempt; resume_from is the
    checkpoint's next step; history records original ("interrupted") + resume
    ("resumed") with no duplicates.
  - duplicate-Job guard: when JobLifecycle is wired to the same event bus, the
    coordinator-created Job is registered so plan.started never creates a
    second Job.
  - duplicate-history guard: re-recording a known job_id yields no extra entry.
  - ambiguity: candidates are surfaced, nothing created.
  - Session.start_run delegates to the coordinator (thin adapter).
"""

import threading
import time

import pytest

from cozmo.jobs.job import Checkpoint, Job, JobStatus
from cozmo.jobs.manager import JobManager
from cozmo.orchestrator import Orchestrator
from cozmo.orchestrator.intent import IntentDetector
from cozmo.orchestrator.task_store import TaskStore
from cozmo.orchestrator.task_types import (
    ExecutionPlan, ExecutionStrategy, Goal, IntentType, Task,
)
from cozmo.planner.models import Plan, PlanStep
from cozmo.runtime.event_bus import EventBus
from cozmo.services.execution import ExecutionCoordinator
from cozmo.services.job_lifecycle import JobLifecycle


# ── fakes / harness ───────────────────────────────────────────────────────────

class _FakeIntent(IntentDetector):
    def detect(self, user_input, history=None, has_images=False):
        return (IntentType.CODING, 1.0)


class _HarnessOrchestrator(Orchestrator):
    """Orchestrator double: fresh planning against a real TaskStore."""

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
        plan_obj.add_step(PlanStep(id="plan-1-s1", plan_id="plan-1",
                                   description="build it"))
        task.plan = plan_obj
        self.task_store.update(task)
        return ExecutionPlan(
            task_id=task.id,
            goal=Goal(text=user_input, intent=IntentType.CODING),
            strategy=ExecutionStrategy.EXECUTE,
            tools=[],
            model_spec={"model": "m1", "supports_tools": True},
            plan=plan_obj,
            context={"task_id": task.id},
        )


class _HarnessRuntime:
    """Runtime double mirroring plan/step events + resume_from handoff."""

    def __init__(self, bus=None):
        self.bus = bus
        self.resume_from_seen = None

    def run_stream(self, user_input="", attachments=None, execution_plan=None,
                   conversation_id=None, resume_from=None):
        plan_obj = getattr(execution_plan, "plan", None)
        steps = list(getattr(plan_obj, "steps", None) or [])
        plan_id = getattr(plan_obj, "id", "") if plan_obj else ""
        task_id = execution_plan.task_id
        start = resume_from if resume_from is not None else 0
        self.resume_from_seen = resume_from
        if self.bus is not None:
            self.bus.emit("plan.started", task_id=task_id, plan_id=plan_id,
                          step_count=len(steps))
        for i, step in enumerate(steps):
            if i < start:
                continue
            if self.bus is not None:
                self.bus.emit("step.completed", task_id=task_id, plan_id=plan_id,
                              step_id=step.id, index=i, result="ok")
            yield ("token", step.description)
        if self.bus is not None:
            self.bus.emit("plan.completed", task_id=task_id, plan_id=plan_id,
                          result="done", step_count=len(steps))
        yield ("assistant", "result")


@pytest.fixture
def task_store(tmp_path):
    return TaskStore(persist_dir=str(tmp_path / "tasks"))


@pytest.fixture
def job_store(tmp_path, monkeypatch):
    import cozmo.jobs.persistence as persistence
    monkeypatch.setattr(persistence, "JOBS_DIR", tmp_path / "jobs")
    from cozmo.jobs.persistence import JobStore
    return JobStore()


def _coordinator(task_store, job_manager, continuation=None, lifecycle=None):
    return ExecutionCoordinator(
        orchestrator=_HarnessOrchestrator(task_store),
        job_manager=job_manager,
        task_store=task_store,
        continuation=continuation,
        job_lifecycle=lifecycle,
    )


# ── fresh run: exactly one of each ───────────────────────────────────────────

def test_fresh_run_creates_exactly_one_each(task_store):
    jm = JobManager()
    coord = _coordinator(task_store, jm)
    rt = _HarnessRuntime()

    items = list(coord.run_stream(rt, "build the widget", conversation_id="conv-1"))

    assert any(i[0] == "assistant" for i in items)
    jobs = jm.list_by_task(task_store.list()[0].id)
    assert len(jobs) == 1
    assert jobs[0].status is JobStatus.COMPLETED

    task = task_store.get(jobs[0].task_id)
    assert task is not None
    assert task.plan is not None                      # 1 Plan
    assert task.execution_history.count() == 1        # 1 History entry
    assert task.execution_history.all_job_ids == [jobs[0].id]
    assert coord.mode == "fresh"


def test_fresh_run_attachments_flag_has_images(task_store):
    jm = JobManager()
    coord = _coordinator(task_store, jm)
    rt = _HarnessRuntime()
    list(coord.run_stream(rt, "look at this", attachments=[{"type": "image", "name": "a.png"}]))
    assert len(jm.list()) == 1


# ── continuation: new attempt + resume_from + history ────────────────────────

def _seed_continuation(task_store, job_store):
    plan = Plan(id="plan-1", task_id="task-1")
    for i, desc in enumerate(["a", "b", "c"]):
        plan.add_step(PlanStep(id=f"plan-1-s{i+1}", plan_id="plan-1",
                               description=desc))
    task = Task(id="task-1", conversation_id="conv-1", raw_goal="build it",
                plan=plan)
    task_store.save(task)
    job = Job(
        id="job-orig", task_id="task-1", status=JobStatus.INTERRUPTED,
        strategy="planned", started_at="2026-01-01T00:00:00",
        checkpoint=Checkpoint(
            job_id="job-orig", task_id="task-1", plan_id="plan-1",
            step=1, completed_steps=["plan-1-s1"],
        ),
    )
    job_store.save(job)
    return task, job


def test_continuation_resumes_new_attempt_with_resume_from(task_store, job_store):
    from cozmo.services.continuation import ContinuationService

    _seed_continuation(task_store, job_store)
    jm = JobManager(store=job_store)
    service = ContinuationService(task_store=task_store, job_store=job_store)
    coord = _coordinator(task_store, jm, continuation=service)
    _force_continuation_intent(coord)
    rt = _HarnessRuntime()

    items = list(coord.run_stream(rt, "continue the task", conversation_id="conv-1"))

    assert any(i[0] == "assistant" for i in items)
    task = task_store.get("task-1")
    jobs = jm.list_by_task("task-1")
    assert len(jobs) == 1                            # only the NEW attempt
    assert jobs[0].id != "job-orig"                  # never resurrect
    assert jobs[0].status is JobStatus.COMPLETED
    assert jobs[0].metadata.get("resumed_from") == "job-orig"
    assert rt.resume_from_seen == 1                  # cp.step 1 = 1 completed → resume at 1

    # history: original (interrupted) + resume (resumed), no dups
    hist = task.execution_history
    assert hist.all_job_ids == ["job-orig", jobs[0].id]
    assert hist.find("job-orig").reason == "interrupted"
    entry = hist.find(jobs[0].id)
    assert entry.reason == "resumed"
    assert entry.parent_job_id == "job-orig"
    assert coord.mode == "continuation"


def test_continuation_reopen_failure_surfaces_error(task_store, job_store):
    from cozmo.services.continuation import ContinuationService

    plan = Plan(id="plan-1", task_id="task-1")
    plan.add_step(PlanStep(id="plan-1-s1", plan_id="plan-1", description="x"))
    task_store.save(Task(id="task-1", conversation_id="conv-1",
                         raw_goal="build it", plan=plan))
    job_store.save(Job(id="job-orig", task_id="task-1",
                       status=JobStatus.INTERRUPTED, strategy="planned",
                       checkpoint=Checkpoint(job_id="job-orig", task_id="task-1",
                                             plan_id="plan-1", step=0)))
    service = ContinuationService(task_store=task_store, job_store=job_store)

    # jm has NO store and no in-memory copy → reopen cannot find the job → error
    jm = JobManager()
    coord = _coordinator(task_store, jm, continuation=service)
    _force_continuation_intent(coord)

    items = list(coord.run_stream(_HarnessRuntime(),
                                  "continue the task", conversation_id="conv-1"))

    controls = [i for i in items if i[0] == "control"]
    assert coord.mode == "error"
    assert len(jm.list()) == 0                       # nothing created
    assert controls and controls[0][1]["type"] == "error"


# ── duplicate-Job guard: coordinator + wired JobLifecycle ────────────────────

def test_plan_started_does_not_double_create_job(task_store):
    bus = EventBus()
    jm = JobManager()
    lifecycle = JobLifecycle(jm, task_store=task_store).subscribe(bus)
    coord = _coordinator(task_store, jm, lifecycle=lifecycle)
    rt = _HarnessRuntime(bus=bus)   # emits plan.* on the same bus as lifecycle

    list(coord.run_stream(rt, "build the widget"))

    task = task_store.list()[0]
    jobs = jm.list_by_task(task.id)
    assert len(jobs) == 1                            # exactly ONE job
    assert task.execution_history.count() == 1       # exactly ONE history entry


# ── duplicate-history guard ──────────────────────────────────────────────────

def test_record_history_is_idempotent(task_store):
    task = Task(id="task-x", raw_goal="x")
    task_store.save(task)
    coord = _coordinator(task_store, JobManager())

    coord._record_history("task-x", "job-1", reason="started")
    coord._record_history("task-x", "job-1", reason="started")  # replay

    fresh = task_store.get("task-x")
    assert fresh.execution_history.count() == 1
    assert fresh.execution_history.all_job_ids == ["job-1"]


def test_record_history_with_parent_records_both_exactly_once(task_store):
    task = Task(id="task-y", raw_goal="y")
    task_store.save(task)
    coord = _coordinator(task_store, JobManager())

    coord._record_history("task-y", "job-r", reason="resumed",
                          parent_job_id="job-orig")
    coord._record_history("task-y", "job-r", reason="resumed",
                          parent_job_id="job-orig")

    fresh = task_store.get("task-y")
    assert fresh.execution_history.count() == 2
    assert fresh.execution_history.all_job_ids == ["job-orig", "job-r"]


# ── ambiguity ────────────────────────────────────────────────────────────────

def test_ambiguous_continuation_surfaces_candidates_no_job(task_store, job_store):
    from cozmo.services.continuation import ContinuationService

    # two distinct resumable tasks in different conversations → ambiguous
    plan = Plan(id="plan-1", task_id="task-1")
    plan.add_step(PlanStep(id="p", plan_id="plan-1", description="x"))
    task_store.save(Task(id="task-1", conversation_id="conv-A",
                         raw_goal="one", plan=plan))
    job_store.save(Job(id="job-1", task_id="task-1",
                       status=JobStatus.INTERRUPTED, strategy="planned",
                       checkpoint=Checkpoint(job_id="job-1", task_id="task-1",
                                             plan_id="plan-1", step=0)))
    task_store.save(Task(id="task-2", conversation_id="conv-B",
                         raw_goal="two", plan=plan,
                         updated_at="2026-01-02T00:00:00"))
    job_store.save(Job(id="job-2", task_id="task-2",
                       status=JobStatus.INTERRUPTED, strategy="planned",
                       checkpoint=Checkpoint(job_id="job-2", task_id="task-2",
                                             plan_id="plan-1", step=0)))

    jm = JobManager(store=job_store)
    service = ContinuationService(task_store=task_store, job_store=job_store)
    coord = _coordinator(task_store, jm, continuation=service)
    _force_continuation_intent(coord)

    items = list(coord.run_stream(_HarnessRuntime(),
                                  "continue whatever", conversation_id="conv-Z"))

    assert coord.mode == "ambiguous"
    assert len(jm.list()) == 0                       # nothing created
    controls = [i for i in items if i[0] == "control"]
    assert len(controls) == 1
    assert controls[0][1]["type"] == "continuation_candidates"


def _force_continuation_intent(coord):
    """Force a continuation intent by swapping the coordinator's detector."""
    from cozmo.orchestrator.task_types import IntentType
    class _CIntent(_FakeIntent):
        def detect(self, user_input, history=None, has_images=False):
            return (IntentType.CONTINUATION, 1.0)
    coord._orchestrator.intent_detector = _CIntent()
    return coord


# ── Session thin-adapter delegation ──────────────────────────────────────────

@pytest.fixture
def session():
    from cozmo.webui_server import Session
    from cozmo import config
    from cozmo.runtime.event_bus import EventBus
    from unittest.mock import patch, MagicMock

    cfg = config.load()
    loop = MagicMock()
    loop.call_soon_threadsafe = lambda fn, *a: fn(*a) if callable(fn) else None
    backend = (MagicMock(), MagicMock(), MagicMock(), EventBus())
    with patch("cozmo.webui_server.build_runtime", return_value=backend):
        sess = Session(cfg, loop)
    return sess


class TestSessionAdapter:
    def test_start_run_delegates_to_coordinator(self, session):
        calls = []

        class FakeCoord:
            mode = "fresh"
            job_id = "job-1"
            task_id = "task-1"
            candidates = []

            def run_stream(self, **kw):
                calls.append(kw)
                return iter([("token", "hi")])

        session.coordinator = FakeCoord()
        session.start_run("hello")

        deadline = time.time() + 3
        done = False
        while time.time() < deadline:
            drained = []
            while not session.events.empty():
                drained.append(session.events.get_nowait())
            if any(e.get("type") == "done" for e in drained):
                done = True
                break
            time.sleep(0.02)

        assert done
        assert calls and calls[0]["user_input"] == "hello"
        assert calls[0]["runtime"] is session.runtime
        assert calls[0]["stop_check"] is not None