"""Milestone 5 Phase 4 — Durable Execution Lifecycle.

Covers:
  - Job lifecycle persistence at every transition (4A)
  - task with multiple jobs / execution history updates (4B)
  - checkpoint save/load (4C)
  - interrupted job recovery on startup (4D)
  - timeline lifecycle projection (4E)
  - JobLifecycle coordinator wiring (plan events → Job + Checkpoint + history)

Boundaries exercised implicitly: the Runtime never appears here — Jobs and
checkpoints are driven purely from the plan/step events it emits.
"""

import pytest

import novi.jobs.persistence as persistence
from novi.jobs.job import Checkpoint, JobStatus
from novi.jobs.manager import JobManager
from novi.jobs.persistence import (
    JobStore,
    find_interrupted_jobs,
    mark_interrupted,
)
from novi.orchestrator.task_types import Task
from novi.runtime.event_bus import EventBus
from novi.services.job_lifecycle import JobLifecycle
from novi.timeline import (
    JOB_CHECKPOINTED,
    JOB_COMPLETED,
    JOB_CREATED,
    JOB_FAILED,
    JOB_INTERRUPTED,
    JOB_STARTED,
    TimelineService,
)
from novi.timeline.timeline_store import TimelineStore


# ── Store isolation ──────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    """JobStore writing to a temp dir."""
    import novi.jobs.persistence as persistence
    monkeypatch.setattr(persistence, "JOBS_DIR", tmp_path / "jobs")
    return JobStore()


@pytest.fixture
def manager(store):
    return JobManager(store=store)


class _FakeTaskStore:
    def __init__(self):
        self.tasks = {}

    def save(self, task):
        self.tasks[task.id] = task
        return True

    def get(self, task_id):
        return self.tasks.get(task_id)

    def update(self, task):
        return self.save(task)


def make_task(task_id="task-1"):
    return Task(id=task_id, raw_goal="do the thing")


# ── 4A: Job lifecycle persistence ───────────────────────────────────────────

def test_job_lifecycle_persists_every_transition(manager, store):
    job = manager.submit(task_id="task-1", status=JobStatus.CREATED)
    assert job.status is JobStatus.CREATED

    stored = store.load(job.id)
    assert stored is not None and stored.status is JobStatus.CREATED

    assert manager.start(job.id) is True
    assert store.load(job.id).status is JobStatus.RUNNING

    assert manager.complete(job.id, result="done") is True
    stored = store.load(job.id)
    assert stored.status in (JobStatus.DONE, JobStatus.COMPLETED)
    assert stored.result == "done"


def test_job_failure_persisted(manager, store):
    job = manager.submit(task_id="task-2")
    manager.start(job.id)
    assert manager.fail(job.id, error="boom") is True

    stored = store.load(job.id)
    assert stored.status is JobStatus.FAILED
    assert "boom" in stored.error


def test_retry_persists_new_attempt(manager, store):
    job = manager.submit(task_id="task-3", max_retries=3)
    manager.start(job.id)
    manager.fail(job.id, error="first try")
    retried = manager.retry(job.id)
    assert retried is not None and retried.retry_count == 1
    assert store.load(retried.id) is not None
    assert store.load(job.id).status is JobStatus.FAILED


# ── 4A: collision-resistant Job ids (Phase 6A) ─────────────────────────────

def test_job_ids_unique_within_same_second(manager, store):
    """Many Jobs created in the same second → all ids + files distinct."""
    ids = []
    for _ in range(25):
        ids.append(manager.submit(task_id="task-idx").id)

    assert len(set(ids)) == 25                       # no same-second collision
    assert all(i.startswith("job-") for i in ids)
    # final segment is a uuid hex — not an incrementing per-manager counter
    suffixes = {i.rsplit("-", 1)[1] for i in ids}
    assert len(suffixes) == 25
    assert all(len(s) == 12 for s in suffixes)

    persisted = store.list_ids()
    assert len(persisted) == 25                       # all durable
    paths = {str(persistence._job_path(i)) for i in ids}
    assert len(paths) == 25                           # distinct files


def test_job_ids_unique_across_separate_managers(store):
    """Independence from any in-memory counter: two managers, same store."""
    m1 = JobManager(store=store)
    m2 = JobManager(store=store)
    ids = [
        m1.submit(task_id="task-a").id,
        m2.submit(task_id="task-b").id,
        m1.submit(task_id="task-c").id,
    ]
    assert len(set(ids)) == 3
    assert len(store.list_ids()) == 3


# ── 4A/4B: JobLifecycle coordinator + execution history ────────────────────

def _wired(manager, task_store=None):
    bus = EventBus()
    seen = []
    bus.on_any(lambda ev: seen.append(ev))
    lifecycle = JobLifecycle(manager, task_store=task_store)
    lifecycle.subscribe(bus)
    return bus, seen, lifecycle


def test_planned_execution_creates_job_and_history(manager):
    task_store = _FakeTaskStore()
    task = make_task()
    task_store.save(task)
    bus, seen, _ = _wired(manager, task_store)

    bus.emit("plan.started", task_id="task-1", plan_id="plan-1", step_count=2)
    bus.emit("step.completed", task_id="task-1", plan_id="plan-1",
             step_id="plan-1-s1", index=0, result="one")
    bus.emit("plan.completed", task_id="task-1", plan_id="plan-1",
             result="done", step_count=2)

    jobs = manager.list_by_task("task-1")
    assert len(jobs) == 1
    assert jobs[0].status is JobStatus.COMPLETED
    assert jobs[0].started_at is not None

    updated = task_store.get("task-1")
    assert updated.execution_history.all_job_ids == [jobs[0].id]

    emitted = {e.type for e in seen}
    assert {"job.created", "job.started", "job.completed",
            "job.checkpointed"}.issubset(emitted)


def test_plan_failure_marks_job_failed(manager):
    bus, seen, _ = _wired(manager)
    bus.emit("plan.started", task_id="task-9", plan_id="plan-9", step_count=1)
    bus.emit("step.failed", task_id="task-9", plan_id="plan-9",
             step_id="plan-9-s1", index=0, error="boom")
    bus.emit("plan.failed", task_id="task-9", plan_id="plan-9",
             step_id="plan-9-s1", error="boom")

    jobs = manager.list_by_task("task-9")
    assert len(jobs) == 1
    assert jobs[0].status is JobStatus.FAILED
    assert "boom" in jobs[0].error


def test_task_with_multiple_jobs(manager):
    """Two attempts against one Task → two distinct Jobs, ordered history."""
    task_store = _FakeTaskStore()
    task = make_task()
    task_store.save(task)
    bus, _, _ = _wired(manager, task_store)

    # attempt 1 — fails
    bus.emit("plan.started", task_id="task-1", plan_id="plan-a", step_count=2)
    bus.emit("plan.failed", task_id="task-1", plan_id="plan-a",
             step_id="plan-a-s1", error="boom")
    # attempt 2 — completes
    bus.emit("plan.started", task_id="task-1", plan_id="plan-b", step_count=2)
    bus.emit("plan.completed", task_id="task-1", plan_id="plan-b",
             result="done", step_count=2)

    jobs = manager.list_by_task("task-1")
    assert len(jobs) == 2
    assert jobs[0].status is JobStatus.FAILED
    assert jobs[1].status is JobStatus.COMPLETED
    assert jobs[0].id != jobs[1].id

    updated = task_store.get("task-1")
    assert updated.execution_history.all_job_ids == [jobs[0].id, jobs[1].id]
    assert updated.result == "done"


# ── 4B: ExecutionHistory records attempts ───────────────────────────────────

def test_execution_history_updates_on_each_attempt(manager):
    task_store = _FakeTaskStore()
    task_store.save(make_task())
    bus, _, lifecycle = _wired(manager, task_store)

    bus.emit("plan.started", task_id="task-1", plan_id="p1", step_count=1)
    job1 = lifecycle.active_job("task-1")
    bus.emit("plan.completed", task_id="task-1", plan_id="p1", result="ok")

    bus.emit("plan.started", task_id="task-1", plan_id="p2", step_count=1)
    job2 = lifecycle.active_job("task-1")
    bus.emit("plan.failed", task_id="task-1", plan_id="p2",
             step_id="p2-s1", error="bad")

    updated = task_store.get("task-1")
    assert updated.execution_history.count() == 2
    assert updated.execution_history.last_job_id == job2


# ── 4C: Checkpoint save/load ────────────────────────────────────────────────

def test_checkpoint_round_trip(manager, store):
    job = manager.submit(task_id="task-cp")
    manager.start(job.id)
    cp = Checkpoint(
        job_id=job.id,
        task_id="task-cp",
        plan_id="plan-cp",
        step=3,
        completed_steps=["plan-cp-s1", "plan-cp-s2"],
    )
    assert manager.checkpoint(job.id, cp) is True

    loaded = store.load_checkpoint(job.id)
    assert loaded is not None
    assert loaded.task_id == "task-cp"
    assert loaded.plan_id == "plan-cp"
    assert loaded.step == 3
    assert loaded.completed_steps == ["plan-cp-s1", "plan-cp-s2"]
    assert loaded.job_id == job.id

    # job file itself carries the checkpoint too
    stored_job = store.load(job.id)
    assert stored_job.checkpoint is not None
    assert stored_job.checkpoint.completed_steps == ["plan-cp-s1", "plan-cp-s2"]


def test_checkpoint_emitted_from_step_events(manager, store):
    bus, seen, lifecycle = _wired(manager)
    bus.emit("plan.started", task_id="task-cp2", plan_id="plan-cp2", step_count=3)
    job_id = lifecycle.active_job("task-cp2")
    bus.emit("step.completed", task_id="task-cp2", plan_id="plan-cp2",
             step_id="plan-cp2-s1", index=0, result="a")
    bus.emit("step.completed", task_id="task-cp2", plan_id="plan-cp2",
             step_id="plan-cp2-s2", index=1, result="b")

    cp = store.load_checkpoint(job_id)
    assert cp is not None
    assert cp.step == 2
    assert cp.completed_steps == ["plan-cp2-s1", "plan-cp2-s2"]

    checkpoint_events = [e for e in seen if e.type == "job.checkpointed"]
    assert len(checkpoint_events) == 2


# ── 4D: Interrupted job recovery ────────────────────────────────────────────

def test_recovery_detects_and_marks_interrupted(manager, store):
    job = manager.submit(task_id="task-rec")
    manager.start(job.id)  # RUNNING — would be interrupted by a crash
    done_job = manager.submit(task_id="task-done")
    manager.start(done_job.id)
    manager.complete(done_job.id, result="ok")

    candidates = find_interrupted_jobs(store)
    assert [c["job_id"] for c in candidates] == [job.id]
    assert candidates[0]["task_id"] == "task-rec"
    assert candidates[0]["status"] == "running"

    marked = mark_interrupted(store)
    assert len(marked) == 1
    assert marked[0]["status"] == "interrupted"
    assert store.load(job.id).status is JobStatus.INTERRUPTED
    assert store.load(done_job.id).status is JobStatus.COMPLETED


def test_recovery_exposes_continue_state(manager, store):
    job = manager.submit(task_id="task-rec2")
    manager.start(job.id)
    cp = Checkpoint(job_id=job.id, task_id="task-rec2", plan_id="plan-rec",
                    step=2, completed_steps=["plan-rec-s1", "plan-rec-s2"])
    manager.checkpoint(job.id, cp)

    candidates = find_interrupted_jobs(store)
    assert len(candidates) == 1
    row = candidates[0]
    assert row["plan_id"] == "plan-rec"
    assert row["next_step"] == 2         # cp.step 2 = 2 completed → resume at 2
    assert row["completed_steps"] == ["plan-rec-s1", "plan-rec-s2"]
    assert row["has_checkpoint"] is True


# ── 4E: Timeline lifecycle projection ───────────────────────────────────────

def test_timeline_projects_job_lifecycle_events(tmp_path):
    bus = EventBus()
    store = TimelineStore(persist_dir=tmp_path / "timeline")
    service = TimelineService(bus, store=store)
    service.start()

    bus.emit("job.created", job_id="job-1", task_id="task-1", status="created")
    bus.emit("job.started", job_id="job-1", task_id="task-1")
    bus.emit("job.checkpointed", job_id="job-1", task_id="task-1", step=2)
    bus.emit("job.completed", job_id="job-1", task_id="task-1", result="done")

    rows = service.recent(limit=50)
    kinds = [r["kind"] for r in rows]
    assert JOB_CREATED in kinds
    assert JOB_STARTED in kinds
    assert JOB_CHECKPOINTED in kinds
    assert JOB_COMPLETED in kinds

    by_kind = {r["kind"]: r for r in rows}
    assert by_kind[JOB_COMPLETED]["title"] == "Execution completed"
    assert by_kind[JOB_COMPLETED]["job_id"] == "job-1"
    assert by_kind[JOB_CHECKPOINTED]["detail"].startswith("Task task-1")


def test_timeline_projects_failed_and_interrupted(tmp_path):
    bus = EventBus()
    store = TimelineStore(persist_dir=tmp_path / "timeline")
    service = TimelineService(bus, store=store)
    service.start()

    bus.emit("job.failed", job_id="job-f", task_id="task-f", error="boom")
    bus.emit("job.interrupted", job_id="job-i", task_id="task-i")

    rows = {r["kind"]: r for r in service.recent(limit=50)}
    assert JOB_FAILED in rows
    assert JOB_INTERRUPTED in rows
    assert rows[JOB_FAILED]["title"] == "Execution failed"
    assert rows[JOB_INTERRUPTED]["title"] == "Execution interrupted"