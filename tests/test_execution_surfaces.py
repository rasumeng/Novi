"""Milestone 5 Phase 5E-2 — unified execution entry points (surfaces).

Every non-WebUI execution surface (CLI, Telegram, TaskQueue, background runs,
scheduler triggers) must flow through the SAME ExecutionCoordinator seam as
WebUI chat. These tests prove, hermetically (fake runtime/ctx, real
stores/coordinator):

  - CLI session -> coordinator with stable ``cli:<session_id>`` identity
  - Telegram message -> coordinator with ``telegram:<chat_id>`` identity,
    executed OFF the event loop (thread bridge)
  - TaskQueue worker -> coordinator, no fake/orphan Job task ids
  - background run -> Task/Plan/Job/History/checkpoint, no fake task ids
  - scheduler trigger -> coordinator chain without live cron
  - cross-entry invariants: 1 Task/Plan/Job, 1 history entry, identity
    isolation, no orphan Jobs, continuation from a migrated entry point
"""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from cozmo.jobs.job import Checkpoint, Job, JobStatus
from cozmo.jobs.manager import JobManager
from cozmo.orchestrator import Orchestrator
from cozmo.orchestrator.intent import IntentDetector
from cozmo.orchestrator.task_types import (
    ExecutionPlan,
    ExecutionStrategy,
    Goal,
    IntentType,
    Task,
    TaskStatus,
)
from cozmo.orchestrator.task_store import TaskStore
from cozmo.planner.models import Plan, PlanStep
from cozmo.services.continuation import ContinuationService
from cozmo.services.job_lifecycle import JobLifecycle


# ---- harness doubles (mirror test_execution_coordinator) ---------------------

class _FakeIntent(IntentDetector):
    def detect(self, user_input, history=None, has_images=False):
        return (IntentType.CODING, 1.0)


class _ContIntent(_FakeIntent):
    def detect(self, user_input, history=None, has_images=False):
        return (IntentType.CONTINUATION, 1.0)


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
    """Runtime double: drives lifecycle events on its bus then yields the
    assistant final text. Also records the executing thread for the Telegram
    off-loop assertion."""

    def __init__(self, bus=None):
        self.bus = bus
        self.resume_from_seen = None
        self.thread_idents = []

    def run_stream(self, user_input="", attachments=None,
                   execution_plan=None, conversation_id=None, resume_from=None):
        self.thread_idents.append(threading.current_thread().ident)
        plan_obj = getattr(execution_plan, "plan", None)
        steps = list(getattr(plan_obj, "steps", None) or [])
        plan_id = getattr(plan_obj, "id", "") if plan_obj else ""
        task_id = execution_plan.task_id
        start = resume_from if resume_from is not None else 0
        self.resume_from_seen = resume_from
        if self.bus is not None:
            self.bus.emit("plan.started", task_id=task_id, plan_id=plan_id,
                          step_count=len(steps))
        yield ("plan.started", plan_id, plan_id or "")
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
        yield ("plan.completed", plan_id, f"Completed {len(steps)} step(s)")
        yield ("assistant", "result")

    def reset(self):
        pass


class _FakeCtx:
    """Composition-root double: mirrors what ``build_application_execution``
    needs from CozmoContext and subscribes the same projections production
    does (TaskLifecycleProjection + optional JobLifecycle)."""

    def __init__(self, task_store, job_manager, *, job_lifecycle=None,
                 continuation=None, runtime_factory=None):
        self.task_store = task_store
        self.job_manager = job_manager
        self.job_lifecycle = job_lifecycle
        self.continuation = continuation
        self.orchestrator = _HarnessOrchestrator(task_store)
        self.runtimes = []
        self._runtime_factory = runtime_factory or (
            lambda bus: _HarnessRuntime(bus=bus))

    def create_runtime(self, **kw):
        from cozmo.orchestrator.projection import TaskLifecycleProjection

        bus = kw.get("event_bus")
        rt = self._runtime_factory(bus)
        TaskLifecycleProjection(self.task_store).subscribe(bus)
        jl = kw.get("job_lifecycle")
        if jl is not None and bus is not None:
            jl.subscribe(bus)
        self.runtimes.append(rt)
        return rt


# ---- fixtures / seeds ---------------------------------------------------------

@pytest.fixture
def task_store(tmp_path):
    return TaskStore(persist_dir=str(tmp_path / "taskstore"))


@pytest.fixture
def job_store(tmp_path, monkeypatch):
    import cozmo.jobs.persistence as persistence
    monkeypatch.setattr(persistence, "JOBS_DIR", tmp_path / "jobs")
    from cozmo.jobs.persistence import JobStore
    return JobStore()


@pytest.fixture
def job_manager(job_store):
    return JobManager(store=job_store)


def _seed_continuation(task_store, job_store):
    plan = Plan(id="plan-1", task_id="task-1")
    for i, desc in enumerate(["build it", "build it", "build it"]):
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


def _assert_fresh_invariants(task_store, job_manager, conversation_id):
    tasks = task_store.list()
    assert len(tasks) == 1
    task = tasks[0]
    assert task.conversation_id == conversation_id
    assert task.plan is not None
    assert task.status in (TaskStatus.COMPLETED, TaskStatus.IN_PROGRESS)
    jobs = job_manager.list_by_task(task.id)
    assert len(jobs) == 1
    assert jobs[0].status is JobStatus.COMPLETED
    assert jobs[0].task_id == task.id
    assert task.execution_history.count() == 1
    assert task.execution_history.all_job_ids == [jobs[0].id]


def _wait_until(predicate, timeout=10.0):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met within timeout")


# ---- 5E-2A CLI -> Coordinator ------------------------------------------------

def test_cli_session_routes_fresh_run_through_coordinator(task_store, job_manager):
    from cozmo.cli import CliSessionAdapter

    ctx = _FakeCtx(task_store, job_manager)
    session = CliSessionAdapter(ctx, session_id="abc")
    assert session.conversation_id == "cli:abc"
    out = session.run("build the widget")
    assert "build it" in out
    _assert_fresh_invariants(task_store, job_manager, "cli:abc")


def test_cli_session_continuation_reopens_new_attempt(task_store, job_store):
    from cozmo.cli import CliSessionAdapter

    _seed_continuation(task_store, job_store)
    jm = JobManager(store=job_store)
    continuation = ContinuationService(task_store=task_store, job_store=job_store,
                                       job_manager=jm)
    ctx = _FakeCtx(task_store, jm, continuation=continuation)
    ctx.orchestrator.intent_detector = _ContIntent()

    session = CliSessionAdapter(ctx, session_id="abc")
    out = session.run("continue the task")
    assert "build it" in out
    assert session.coordinator.mode == "continuation"
    assert session.runtime.resume_from_seen == 2

    task = task_store.get("task-1")
    jobs = jm.list_by_task("task-1")
    assert len(jobs) == 1
    assert jobs[0].id != "job-orig"
    assert jobs[0].status is JobStatus.COMPLETED
    assert task.execution_history.all_job_ids == ["job-orig", jobs[0].id]
    assert task.execution_history.count() == 2


def test_cli_render_surfaces_tokens_errors_and_candidates():
    from cozmo.cli import _render_run

    class _TokenCoord:
        def run_stream(self, runtime, text, conversation_id=None):
            yield ("status", "analyzing")
            yield ("token", "hello ")
            yield ("token", "world")
            yield ("thinking", "done")

    assert _render_run(_TokenCoord(), object(), "x", "c") == "hello world"

    class _ErrorCoord:
        def run_stream(self, runtime, text, conversation_id=None):
            yield ("control", {"type": "error", "text": "boom"})
            yield ("token", "ignored")

    assert _render_run(_ErrorCoord(), object(), "x", "c") == "boom"

    class _CandidatesCoord:
        def run_stream(self, runtime, text, conversation_id=None):
            yield ("control", {"type": "continuation_candidates",
                                "candidates": [{"title": "T1", "progress": "1/3"}]})

    out = _render_run(_CandidatesCoord(), object(), "x", "c")
    assert "T1" in out and "1/3" in out


# ---- 5E-2B Telegram -> Coordinator -------------------------------------------

def test_telegram_sync_routes_fresh_run_through_coordinator(task_store, job_manager):
    from cozmo.services.telegram import _handle_sync

    ctx = _FakeCtx(task_store, job_manager)
    out = _handle_sync(ctx, "12345", "build the widget")
    assert "build it" in out
    _assert_fresh_invariants(task_store, job_manager, "telegram:12345")


def test_telegram_handler_runs_off_event_loop(task_store, job_manager):
    from cozmo.services.telegram import build_telegram_handler

    ctx = _FakeCtx(task_store, job_manager)
    handler = build_telegram_handler(ctx)
    out = asyncio.run(handler("7", "build the widget"))
    assert "build it" in out
    main_ident = threading.main_thread().ident
    idents = [i for rt in ctx.runtimes for i in rt.thread_idents]
    assert idents and all(i != main_ident for i in idents), \
        "coordinator must not run on the event-loop (main) thread"


def test_telegram_continuation_uses_coordinator_path(task_store, job_store):
    from cozmo.services.telegram import _handle_sync

    _seed_continuation(task_store, job_store)
    jm = JobManager(store=job_store)
    continuation = ContinuationService(task_store=task_store, job_store=job_store,
                                       job_manager=jm)
    ctx = _FakeCtx(task_store, jm, continuation=continuation)
    ctx.orchestrator.intent_detector = _ContIntent()

    out = _handle_sync(ctx, "12345", "continue the task")
    assert "build it" in out
    task = task_store.get("task-1")
    jobs = jm.list_by_task("task-1")
    assert len(jobs) == 1
    assert jobs[0].id != "job-orig"
    assert jobs[0].status is JobStatus.COMPLETED
    assert task.execution_history.all_job_ids == ["job-orig", jobs[0].id]
    assert task.conversation_id == "conv-1"


# ---- 5E-2C TaskQueue worker -> Coordinator -----------------------------------

def test_task_queue_worker_routes_through_coordinator(task_store, job_manager,
                                                      tmp_path, monkeypatch):
    import cozmo.task_queue as tq
    from cozmo.services.background import run_background
    from cozmo.task_queue import TaskQueue, TaskStatus

    monkeypatch.setattr(tq, "TASKS_DIR", tmp_path / "tasks")
    ctx = _FakeCtx(task_store, job_manager)
    queue = TaskQueue()

    def runner(task):
        result = run_background(ctx, task.prompt,
                                conversation_id=f"queue:{task.id}")
        task.cozmo_task_id = result.task_id
        return result.answer

    task = queue.add("queue it", "build the widget")
    queue.run_task(task, runner)
    _wait_until(lambda: task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED))

    assert task.status is TaskStatus.COMPLETED
    assert "build it" in task.result
    assert task.cozmo_task_id
    resolved = task_store.get(task.cozmo_task_id)
    assert resolved is not None
    assert resolved.execution_history.count() == 1
    _assert_fresh_invariants(task_store, job_manager, f"queue:{task.id}")


def test_task_queue_worker_failure_marks_failed(tmp_path, monkeypatch):
    import cozmo.task_queue as tq
    from cozmo.task_queue import TaskQueue, TaskStatus

    monkeypatch.setattr(tq, "TASKS_DIR", tmp_path / "tasks")
    queue = TaskQueue()

    def runner(task):
        raise RuntimeError("nope")

    task = queue.add("d", "prompt")
    queue.run_task(task, runner)
    _wait_until(lambda: task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED))
    assert task.status is TaskStatus.FAILED
    assert "nope" in task.error


# ---- 5E-2D Background run -> Coordinator -------------------------------------

def test_background_run_creates_full_chain(task_store, job_store):
    from cozmo.services.background import run_background

    jm = JobManager(store=job_store)
    lifecycle = JobLifecycle(jm, task_store=task_store)
    ctx = _FakeCtx(task_store, jm, job_lifecycle=lifecycle)

    result = run_background(ctx, "build the widget", conversation_id="bg:run1")
    _assert_fresh_invariants(task_store, jm, "bg:run1")

    task = task_store.list()[0]
    assert result.task_id == task.id
    jobs = jm.list_by_task(task.id)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.checkpoint is not None and job.checkpoint.step == 3
    assert task.status is TaskStatus.COMPLETED
    # no orphan fake-task Job: every Job resolves to a TaskStore Task
    task_ids = {t.id for t in task_store.list()}
    for j in jm.list():
        assert j.task_id in task_ids, f"orphan Job referencing fake task {j.task_id}"


def test_background_run_stop_check_finalises_job(task_store, job_manager):
    from cozmo.services.background import run_background

    ctx = _FakeCtx(task_store, job_manager)
    result = run_background(ctx, "build the widget", conversation_id="bg:stop",
                            stop_check=lambda: True)
    task = task_store.list()[0]
    jobs = job_manager.list_by_task(task.id)
    assert len(jobs) == 1
    assert jobs[0].status is JobStatus.COMPLETED
    assert result.job_id == jobs[0].id


def test_background_on_event_streams_items(task_store, job_manager):
    from cozmo.services.background import run_background

    ctx = _FakeCtx(task_store, job_manager)
    kinds = []
    run_background(ctx, "build the widget", conversation_id="bg:ev",
                   on_event=lambda item: kinds.append(item[0]))
    assert "token" in kinds
    assert "plan.started" in kinds
    assert "plan.completed" in kinds


# ---- 5E-2E Scheduler -> Background -> Coordinator -----------------------------

def test_scheduler_trigger_reaches_coordinator_chain(task_store, job_manager,
                                                     tmp_path, monkeypatch):
    import cozmo.scheduler as sched_mod
    from cozmo.scheduler import ScheduledRun
    from cozmo.services.background import run_background

    monkeypatch.setattr(sched_mod, "SCHEDULES_PATH", tmp_path / "schedules.json")
    ctx = _FakeCtx(task_store, job_manager)
    scheduler = sched_mod.Scheduler()
    scheduler.on_trigger = lambda s: run_background(
        ctx, s.goal, conversation_id=f"schedule:{s.id}")

    # drive the trigger directly — no live cron/thread wait
    scheduler.on_trigger(ScheduledRun(id="sched1", goal="build the widget",
                                      description="scheduled"))
    _assert_fresh_invariants(task_store, job_manager, "schedule:sched1")


def test_context_scheduled_trigger_routes_to_background(monkeypatch):
    from cozmo.services.context import CozmoContext

    recorded = {}

    def fake_run_background(ctx, goal, **kw):
        recorded["goal"] = goal
        recorded["kw"] = kw
        return SimpleNamespace(task_id="t1", job_id="j1")

    monkeypatch.setattr("cozmo.services.background.run_background", fake_run_background)
    ctx = CozmoContext(cfg={"ollama": {"url": "http://x"}})
    ctx._scheduled_trigger(SimpleNamespace(id="s9", goal="g"))
    assert recorded["goal"] == "g"
    assert recorded["kw"]["conversation_id"] == "schedule:s9"


# ---- cross-entry invariants ---------------------------------------------------

@pytest.mark.parametrize("surface", ["cli", "telegram", "background"])
def test_each_surface_creates_exactly_one_job_and_history(surface, task_store,
                                                          job_store):
    jm = JobManager(store=job_store)
    lifecycle = JobLifecycle(jm, task_store=task_store)
    ctx = _FakeCtx(task_store, jm, job_lifecycle=lifecycle)
    text = "build the widget"

    if surface == "cli":
        from cozmo.cli import CliSessionAdapter
        conv = "cli:px"
        CliSessionAdapter(ctx, session_id="px").run(text)
    elif surface == "telegram":
        from cozmo.services.telegram import _handle_sync
        conv = "telegram:9"
        _handle_sync(ctx, "9", text)
    else:
        from cozmo.services.background import run_background
        conv = "bg:z"
        run_background(ctx, text, conversation_id=conv)

    _assert_fresh_invariants(task_store, jm, conv)
    assert len(task_store.list()) == 1


def test_conversation_identity_isolation(task_store, job_manager):
    from cozmo.services.telegram import _handle_sync

    ctx = _FakeCtx(task_store, job_manager)
    _handle_sync(ctx, "111", "one")
    _handle_sync(ctx, "222", "two")

    tasks = task_store.list()
    assert len(tasks) == 2
    assert {t.conversation_id for t in tasks} == {"telegram:111", "telegram:222"}
    for t in tasks:
        assert len(job_manager.list_by_task(t.id)) == 1


def test_no_orphan_fake_task_jobs_across_surfaces(task_store, job_store,
                                                  tmp_path, monkeypatch):
    import cozmo.task_queue as tq
    from cozmo.services.background import run_background
    from cozmo.services.telegram import _handle_sync
    from cozmo.task_queue import TaskQueue, TaskStatus

    jm = JobManager(store=job_store)
    lifecycle = JobLifecycle(jm, task_store=task_store)
    ctx = _FakeCtx(task_store, jm, job_lifecycle=lifecycle)

    _handle_sync(ctx, "1", "telegram task")
    run_background(ctx, "background task", conversation_id="bg:orphan")

    monkeypatch.setattr(tq, "TASKS_DIR", tmp_path / "tasks")
    queue = TaskQueue()

    def runner(task):
        result = run_background(ctx, task.prompt,
                                conversation_id=f"queue:{task.id}")
        task.cozmo_task_id = result.task_id
        return result.answer

    task = queue.add("d", "queue task")
    queue.run_task(task, runner)
    _wait_until(lambda: task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED))

    real_task_ids = {t.id for t in task_store.list()}
    for job in jm.list():
        assert job.task_id in real_task_ids, f"fake/orphan task id: {job.task_id}"


def test_surface_adapters_stay_thin_adapters():
    """Architecture guard: surface adapters must not own coordinator/job/runtime internals."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "cozmo"
    forbidden = ("cozmo.runtime", "cozmo.jobs", "cozmo.orchestrator",
                 "ExecutionCoordinator", "CozmoRuntime")
    for rel in ("telegram_bot.py", "task_queue.py"):
        src = (root / rel).read_text("utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    assert not any(n.name.startswith(f) for f in forbidden), \
                        f"{rel} must not import {n.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not any(mod.startswith(f) for f in forbidden), \
                    f"{rel} must not import {mod}"