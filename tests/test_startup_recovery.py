"""Milestone 5 Phase 6B — startup interruption recovery (composition root).

Recognizes executions abandoned by a previous crashed process and surfaces them
as INTERRUPTED on the next startup, with zero automatic resume:

    RUNNING/eligible nonterminal persisted Job
        → startup sweep (co/services/recovery.recover_interrupted_jobs)
        → INTERRUPTED, checkpoint preserved, job.interrupted event emitted
        → discoverable by ContinuationService, only an explicit continuation
          reopens it (JobManager.reopen → NEW attempt)

Covered here:
  1. eligible statuses → INTERRUPTED (RUNNING/COMPLETING/CREATED/PENDING/QUEUED)
  2. PAUSED preserved (deliberate pause stays the JobManager.resume path)
  3. terminal jobs untouched (DONE/COMPLETED/ERROR/FAILED/CANCELLED)
  4. checkpoint + Task/Plan references survive intact; Job id unchanged
  5. idempotency — a second sweep is a no-op, no duplicate events/state
  6. no auto-resume — recovery produces zero model/tool execution
  7. job.interrupted flows through the established timeline projection
  8. interrupted work is discoverable by ContinuationService at Checkpoint.step
  9. the CozmoContext composition hook delegates to the same recovery
"""

import pytest

from cozmo.jobs.job import Checkpoint, Job, JobStatus
from cozmo.jobs.persistence import JobStore
from cozmo.orchestrator.task_store import TaskStore
from cozmo.orchestrator.task_types import Task
from cozmo.planner.models import Plan, PlanStep
from cozmo.runtime.event_bus import EventBus
from cozmo.services.continuation import ContinuationService
from cozmo.services.recovery import INTERRUPT_EVENT, recover_interrupted_jobs
from cozmo.timeline import JOB_INTERRUPTED, TimelineService
from cozmo.timeline.timeline_store import TimelineStore


# ── helpers ─────────────────────────────────────────────────────────────────

def _plan(task_id="task-1", plan_id="plan-1"):
    plan = Plan(id=plan_id, task_id=task_id)
    for i in ("1", "2", "3"):
        plan.add_step(PlanStep(id=f"{plan_id}-s{i}", plan_id=plan_id,
                               description="x"))
    return plan


def _job(job_id, task_id, status, *, cp_step=None, completed=None,
         plan_id="plan-1"):
    cp = None
    if cp_step is not None:
        cp = Checkpoint(job_id=job_id, task_id=task_id, plan_id=plan_id,
                        step=cp_step, completed_steps=list(completed or []))
    return Job(id=job_id, task_id=task_id, status=status, strategy="planned",
               checkpoint=cp, started_at="2026-01-01T00:00:00")


@pytest.fixture
def task_store(tmp_path):
    return TaskStore(persist_dir=str(tmp_path / "tasks"))


@pytest.fixture
def job_store(tmp_path, monkeypatch):
    import cozmo.jobs.persistence as persistence
    monkeypatch.setattr(persistence, "JOBS_DIR", tmp_path / "jobs")
    return JobStore()


def _seed(task_store, job_store, status, task_id="task-1", cp_step=2,
          completed=("plan-1-s1", "plan-1-s2")):
    task = Task(id=task_id, conversation_id="conv-1", raw_goal="build it",
                plan=_plan(task_id))
    task_store.save(task)
    job_store.save(_job(f"job-{task_id}", task_id, status, cp_step=cp_step,
                        completed=list(completed)))
    return task


# ── 1. eligible nonterminal → INTERRUPTED ───────────────────────────────────

@pytest.mark.parametrize("status", [
    JobStatus.RUNNING, JobStatus.COMPLETING, JobStatus.CREATED,
    JobStatus.PENDING, JobStatus.QUEUED,
])
def test_startup_transitions_eligible_nonterminal_to_interrupted(
        task_store, job_store, status):
    task = _seed(task_store, job_store, status)
    marked = recover_interrupted_jobs(job_store)

    assert [m["job_id"] for m in marked] == ["job-task-1"]
    assert marked[0]["status"] == "interrupted"
    stored = job_store.load("job-task-1")
    assert stored.status is JobStatus.INTERRUPTED
    assert stored.task_id == task.id           # Task reference preserved
    assert stored.checkpoint is not None       # checkpoint preserved
    assert stored.checkpoint.step == 2
    assert stored.checkpoint.completed_steps == ["plan-1-s1", "plan-1-s2"]
    assert stored.checkpoint.plan_id == "plan-1"   # plan reference preserved


def test_startup_surfaces_next_step_as_checkpoint_step(task_store, job_store):
    _seed(task_store, job_store, JobStatus.RUNNING, cp_step=2)
    marked = recover_interrupted_jobs(job_store)
    assert marked[0]["next_step"] == 2         # exactly cp.step — no +1
    assert marked[0]["plan_id"] == "plan-1"
    assert marked[0]["completed_steps"] == ["plan-1-s1", "plan-1-s2"]
    assert marked[0]["has_checkpoint"] is True


def test_startup_marks_running_job_without_checkpoint(task_store, job_store):
    _seed(task_store, job_store, JobStatus.RUNNING, cp_step=None)
    marked = recover_interrupted_jobs(job_store)
    assert len(marked) == 1
    assert marked[0]["has_checkpoint"] is False
    assert marked[0]["next_step"] == 0         # nothing completed → resume at 0


# ── 2. PAUSED preserved ─────────────────────────────────────────────────────

def test_startup_preserves_paused_distinction(task_store, job_store):
    _seed(task_store, job_store, JobStatus.PAUSED, cp_step=1)
    marked = recover_interrupted_jobs(job_store)
    assert marked == []                        # PAUSED is a deliberate state
    stored = job_store.load("job-task-1")
    assert stored.status is JobStatus.PAUSED
    assert stored.checkpoint is not None       # intact, resume path is manager


# ── 3. terminal jobs untouched ──────────────────────────────────────────────

@pytest.mark.parametrize("status", [
    JobStatus.DONE, JobStatus.COMPLETED, JobStatus.ERROR,
    JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED,
])
def test_startup_never_touches_terminal_jobs(task_store, job_store, status):
    _seed(task_store, job_store, status, cp_step=1)
    marked = recover_interrupted_jobs(job_store)
    assert marked == []
    assert job_store.load("job-task-1").status is status


# ── 4. original Job id preserved; no new Job ────────────────────────────────

def test_startup_keeps_original_job_id_and_creates_nothing(task_store, job_store):
    _seed(task_store, job_store, JobStatus.RUNNING)
    before = set(job_store.list_ids())
    recover_interrupted_jobs(job_store)
    after = set(job_store.list_ids())
    assert after == before                     # exactly the same Job files
    assert len(after) == 1
    stored = job_store.load("job-task-1")
    assert stored.id == "job-task-1"
    assert stored.status is JobStatus.INTERRUPTED
    assert stored.completed_at in (None, "")   # historical attempt, not finished


# ── 5. idempotency ──────────────────────────────────────────────────────────

def test_startup_recovery_is_idempotent(task_store, job_store):
    _seed(task_store, job_store, JobStatus.RUNNING)
    bus = EventBus()
    events = []
    bus.on_any(lambda ev: events.append(ev))

    first = recover_interrupted_jobs(job_store, bus=bus)
    second = recover_interrupted_jobs(job_store, bus=bus)   # sweep again

    assert len(first) == 1
    assert second == []                        # nothing left to transition
    assert job_store.load("job-task-1").status is JobStatus.INTERRUPTED
    assert len(events) == 1                    # event emitted exactly once
    assert set(job_store.list_ids()) == {"job-task-1"}

    # No duplicate checkpoint files were written by the second run.
    import cozmo.jobs.persistence as persistence
    cps = list((persistence.JOBS_DIR).glob("*.checkpoint.json"))
    from pathlib import Path
    assert len(list(Path(persistence.JOBS_DIR).glob("*.json"))) == 1


# ── 6. no auto-resume ───────────────────────────────────────────────────────

def test_startup_recovery_never_auto_resumes(task_store, job_store, monkeypatch):
    """Startup marks INTERRUPTED but executes nothing: submit + runtime = 0."""
    calls = {"submit": 0, "runtime_ctor": 0}

    from cozmo.jobs.manager import JobManager
    orig_submit = JobManager.submit

    def spy_submit(self, *a, **k):
        calls["submit"] += 1
        return orig_submit(self, *a, **k)

    monkeypatch.setattr(JobManager, "submit", spy_submit)

    import cozmo.runtime.runtime as runtime_mod

    def boom(*a, **k):
        calls["runtime_ctor"] += 1
        raise AssertionError("startup recovery must never execute")

    monkeypatch.setattr(runtime_mod, "CozmoRuntime", boom)

    _seed(task_store, job_store, JobStatus.RUNNING)
    marked = recover_interrupted_jobs(job_store)

    assert len(marked) == 1
    assert calls == {"submit": 0, "runtime_ctor": 0}
    assert job_store.load("job-task-1").status is JobStatus.INTERRUPTED


# ── 7. job.interrupted through the established timeline projection ──────────

def test_startup_emits_job_interrupted_timeline_event(tmp_path, task_store,
                                                      job_store):
    _seed(task_store, job_store, JobStatus.RUNNING)
    bus = EventBus()
    tstore = TimelineStore(persist_dir=str(tmp_path / "timeline"))
    TimelineService(bus, store=tstore).start()

    recover_interrupted_jobs(job_store, bus=bus)

    rows = tstore.list(limit=50)
    rows = rows if isinstance(rows, list) else list(rows)
    kinds = [r.get("kind") for r in rows]
    assert JOB_INTERRUPTED in kinds
    interrupted = [r for r in rows if r.get("kind") == JOB_INTERRUPTED]
    assert len(interrupted) == 1
    assert interrupted[0]["job_id"] == "job-task-1"
    assert interrupted[0]["task_id"] == "task-1"


def test_recovery_emits_event_only_for_actual_transitions(task_store, job_store):
    _seed(task_store, job_store, JobStatus.RUNNING)
    bus = EventBus()
    seen = []
    bus.on(INTERRUPT_EVENT, lambda ev: seen.append(ev))
    recover_interrupted_jobs(job_store, bus=bus)
    recover_interrupted_jobs(job_store, bus=bus)   # second pass: no new event
    assert len(seen) == 1


# ── 8. discoverable by ContinuationService at Checkpoint.step ───────────────

def test_interrupted_work_discoverable_by_continuation(task_store, job_store):
    _seed(task_store, job_store, JobStatus.RUNNING)
    recover_interrupted_jobs(job_store)

    service = ContinuationService(task_store=task_store, job_store=job_store)
    cands = service.candidates(conversation_id="conv-1")
    assert len(cands) == 1
    target = cands[0]
    assert target.job_id == "job-task-1"
    assert target.task_id == "task-1"
    assert target.checkpoint is not None
    assert target.next_step == target.checkpoint.step == 2
    rec = service.recommended(conversation_id="conv-1")
    assert rec is not None and rec.job_id == "job-task-1"
    assert rec.next_step == rec.checkpoint.step == 2


# ── 9. CozmoContext composition hook ────────────────────────────────────────

def test_context_recover_jobs_delegates_to_composition_hook(tmp_path,
                                                            monkeypatch):
    import cozmo.jobs.persistence as persistence
    monkeypatch.setattr(persistence, "JOBS_DIR", tmp_path / "jobs")
    from cozmo.jobs.persistence import JobStore
    store = JobStore()
    store.save(_job("job-ctx", "task-ctx", JobStatus.RUNNING, cp_step=1))

    from cozmo.services.context import CozmoContext
    ctx = CozmoContext(cfg={"ollama": {"url": "http://x"}})
    bus = EventBus()
    marked = ctx.recover_jobs(bus=bus)

    assert [m["job_id"] for m in marked] == ["job-ctx"]
    assert store.load("job-ctx").status is JobStatus.INTERRUPTED
    assert store.load("job-ctx").checkpoint.step == 1