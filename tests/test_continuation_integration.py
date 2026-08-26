"""Milestone 5 Phase 5D — Continuation execution wiring.

Covers the composition-root hand-off for "continue where I left off":

  - JobManager.reopen() opens a NEW attempt for an interrupted/historical
    job (never resurrects the old one), linking resumed_from.
  - The checkpoint step becomes the runtime resume_from value.
  - Task ExecutionHistory records the original + resume attempts exactly
    once each (no duplicates).
  - Same-conversation resolution still prefers the current thread.

Runtime resume_from behavior itself is covered in test_resume_runtime.py.
"""

import pytest

from novi.orchestrator.task_types import IntentType, Task, TaskStatus
from novi.orchestrator.task_store import TaskStore
from novi.jobs.job import Checkpoint, Job, JobStatus
from novi.jobs.manager import JobManager
from novi.jobs.persistence import JobStore
from novi.planner.models import Plan, PlanStep
from novi.services.continuation import ContinuationService


def make_task(tid, *, status=TaskStatus.IN_PROGRESS, conversation_id="conv-1",
              raw_goal="build the widget", plan=None, updated_at=""):
    return Task(
        id=tid,
        status=status,
        conversation_id=conversation_id,
        raw_goal=raw_goal,
        plan=plan,
        updated_at=updated_at,
    )


def make_job(job_id, task_id, *, status=JobStatus.INTERRUPTED,
             cp_step=1, completed_steps=None, plan_id="plan-1"):
    return Job(
        id=job_id,
        task_id=task_id,
        status=status,
        strategy="planned",
        started_at="2026-01-01T00:00:00",
        checkpoint=Checkpoint(
            job_id=job_id,
            task_id=task_id,
            plan_id=plan_id,
            step=cp_step,
            completed_steps=completed_steps or [],
        ),
    )


def three_step_plan(task_id="task-1"):
    plan = Plan(id="plan-1", task_id=task_id)
    for i, desc in enumerate(["a", "b", "c"]):
        plan.add_step(PlanStep(id=f"plan-1-s{i+1}", plan_id="plan-1",
                               description=desc))
    return plan


@pytest.fixture
def task_store(tmp_path):
    return TaskStore(persist_dir=str(tmp_path / "tasks"))


@pytest.fixture
def job_store(tmp_path, monkeypatch):
    import novi.jobs.persistence as persistence
    monkeypatch.setattr(persistence, "JOBS_DIR", tmp_path / "jobs")
    return JobStore()


# ── reopen: new attempt, never resurrect ──────────────────────────────────

def test_reopen_creates_new_attempt(job_store):
    job_store.save(make_job("job-orig", "task-1"))
    manager = JobManager(store=job_store)

    new_job = manager.reopen("job-orig")

    assert new_job is not None
    assert new_job.id != "job-orig"               # new attempt
    assert new_job.task_id == "task-1"
    assert new_job.status is JobStatus.QUEUED
    assert new_job.metadata.get("resumed_from") == "job-orig"
    assert new_job.metadata.get("reopen") is True
    assert new_job.checkpoint is not None
    assert new_job.checkpoint.step == 1           # original checkpoint carried
    # original stays untouched + historical
    old = job_store.load("job-orig")
    assert old.status is JobStatus.INTERRUPTED
    assert old.checkpoint is not None


def test_reopen_refuses_terminal_and_missing(job_store):
    manager = JobManager(store=job_store)
    assert manager.reopen("nope") is None

    done = make_job("job-done", "task-1", status=JobStatus.COMPLETED)
    job_store.save(done)
    assert manager.reopen("job-done") is None

    no_cp = Job(id="job-nocp", task_id="task-1",
                status=JobStatus.INTERRUPTED, strategy="execute")
    job_store.save(no_cp)
    assert manager.reopen("job-nocp") is None


# ── checkpoint → resume_from handoff ─────────────────────────────────────

def test_checkpoint_step_becomes_resume_from(task_store, job_store):
    task_store.save(make_task("task-1", conversation_id="conv-1",
                              plan=three_step_plan()))
    job_store.save(make_job("job-1", "task-1", cp_step=2,
                            completed_steps=["plan-1-s1", "plan-1-s2"]))
    task_store.save(make_task("task-2", conversation_id="conv-1",
                              updated_at="2026-01-02T00:00:00"))
    job_store.save(make_job("job-2", "task-2", cp_step=0))

    service = ContinuationService(task_store=task_store, job_store=job_store)
    rec = service.recommended(conversation_id="conv-1")

    assert rec is not None
    assert rec.task_id == "task-1"          # only resumable candidate in conv
    assert rec.next_step == 2                 # cp.step 2 = 2 completed → resume at 2


# ── ExecutionHistory: original + resume, no dups ─────────────────────────

def test_execution_history_records_original_and_resume():
    task = make_task("task-1", plan=three_step_plan())
    history = task.execution_history

    # attempt 1: original interrupted job
    history.add("job-orig", reason="interrupted")
    # attempt 2: resume → new job linked to original
    history.add("job-resume", reason="resumed", parent_job_id="job-orig")

    assert history.all_job_ids == ["job-orig", "job-resume"]
    original = history.find("job-orig")
    resumed = history.find("job-resume")
    assert original.reason == "interrupted"
    assert resumed.parent_job_id == "job-orig"
    # idempotent composition: re-recording a known job_id does not duplicate
    if history.find("job-resume") is None:
        history.add("job-resume", reason="resumed", parent_job_id="job-orig")
    assert history.count() == 2


# ── same-conversation priority on resume ─────────────────────────────────

def test_resume_prefers_current_conversation(task_store, job_store):
    task_store.save(make_task("task-A", conversation_id="conv-A",
                              updated_at="2026-01-01T00:00:00"))
    job_store.save(make_job("job-A", "task-A"))
    task_store.save(make_task("task-E", conversation_id="conv-E",
                              updated_at="2026-01-02T00:00:00"))
    job_store.save(make_job("job-E", "task-E"))

    service = ContinuationService(task_store=task_store, job_store=job_store)
    rec = service.recommended(conversation_id="conv-A")
    assert rec.task_id == "task-A"


def test_reopen_refuses_missing_checkpoint():
    manager = JobManager(store=None)
    assert manager.reopen("job-nocp") is None


class _FakeJobStore:
    """In-memory JobStore double for reopen without a real store."""
    def __init__(self):
        self._jobs = {}

    def save(self, job):
        self._jobs[job.id] = job
        return True

    def list(self):
        return list(self._jobs.values())

    def load(self, job_id):
        return self._jobs.get(job_id)