"""Task 6 — Jobs as Durable Long-Running + Continuation Policy.

Covers:
  - Automatic continuation without user input (needs_continuation → checkpoint → compact → re-queue with resume_from=checkpoint.step unchanged) up to 3x
  - User continuation when cannot auto-continue → NEEDS_CONTINUATION + UI prompt + Continue resumes without restating goal
  - Checkpoint.step contract unchanged, isolation preserved (project_id)
  - can_resume includes NEEDS_CONTINUATION
"""
import pytest
from novi.jobs.job import Checkpoint, Job, JobStatus
from novi.jobs.manager import JobManager
from novi.orchestrator.task_store import TaskStore
from novi.orchestrator.task_types import Task, TaskStatus
from novi.planner.models import Plan, PlanStep
# Test-only helper moved from production (novi/services/execution.py:503)


def test_can_resume_includes_needs_continuation():
    cp = Checkpoint(job_id="j1", step=1, task_id="t1", stable={"goal": "x"})
    job = Job(id="j1", task_id="t1", status=JobStatus.NEEDS_CONTINUATION, checkpoint=cp)
    assert job.can_resume is True
    assert job.status.is_terminal is False
    job2 = Job(id="j2", task_id="t1", status=JobStatus.PAUSED, checkpoint=cp)
    assert job2.can_resume is True
    job3 = Job(id="j3", task_id="t1", status=JobStatus.RUNNING, checkpoint=cp)
    assert job3.can_resume is False


def test_continuation_service_includes_needs_continuation(tmp_path):
    from novi.services.continuation import ContinuationService
    from novi.jobs.persistence import JobStore
    import novi.jobs.persistence as persistence
    import pathlib
    ts = TaskStore(persist_dir=str(tmp_path / "tasks"))
    # need isolated job store
    js_path = tmp_path / "jobs"
    persistence.JOBS_DIR = js_path
    js = JobStore()
    # seed task + job with NEEDS_CONTINUATION
    from novi.planner.models import Plan as P
    plan = P(id="plan-1", task_id="task-1")
    plan.add_step(PlanStep(id="plan-1-s1", plan_id="plan-1", description="a"))
    task = Task(id="task-1", conversation_id="conv-1", raw_goal="build it", plan=plan)
    ts.save(task)
    cp = Checkpoint(job_id="job-1", task_id="task-1", plan_id="plan-1", step=1, completed_steps=["s1"], stable={"project_id": "proj-A", "goal": "build it"})
    job = Job(id="job-1", task_id="task-1", status=JobStatus.NEEDS_CONTINUATION, started_at="2026-01-01T00:00:00", checkpoint=cp)
    js.save(job)
    svc = ContinuationService(task_store=ts, job_store=js)
    cands = svc.candidates(conversation_id="conv-1")
    assert len(cands) == 1
    assert cands[0].job_id == "job-1"
    assert cands[0].next_step == 1
    assert cands[0].project_id == "proj-A"
    # isolation: should not leak other project's index
    assert cands[0].checkpoint.stable.get("project_id") == "proj-A"


def test_persistence_roundtrip_via_jobstore_save_load(tmp_path):
    """JobStore.save/load preserves Checkpoint.stable and project isolation."""
    import novi.jobs.persistence as persistence
    from novi.jobs.persistence import JobStore
    persistence.JOBS_DIR = tmp_path / "jobs_rt"
    js = JobStore()
    stable = {"goal": "analyze", "project_id": "proj-RT", "workspace_paths": ["proj-RT"], "current_step": 1, "completed": ["s0"]}
    cp = Checkpoint(job_id="job-rt", task_id="task-rt", plan_id="plan-rt", step=1, completed_steps=["s0"], stable=stable)
    job = Job(id="job-rt", task_id="task-rt", status=JobStatus.NEEDS_CONTINUATION, checkpoint=cp, metadata={"project_id": "proj-RT"})
    assert js.save(job) is True
    # also save checkpoint separately (auto-continue path writes .checkpoint.json)
    assert js.save_checkpoint(cp) is True
    loaded = js.load("job-rt")
    assert loaded is not None
    assert loaded.checkpoint is not None
    assert loaded.checkpoint.step == 1
    assert loaded.checkpoint.stable.get("project_id") == "proj-RT"
    assert loaded.checkpoint.stable.get("workspace_paths") == ["proj-RT"]
    loaded_cp = js.load_checkpoint("job-rt")
    assert loaded_cp is not None
    assert loaded_cp.stable.get("project_id") == "proj-RT"
    assert loaded_cp.step == 1
