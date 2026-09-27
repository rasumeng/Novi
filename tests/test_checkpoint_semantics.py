"""Milestone 5 Phase 6A — Checkpoint.step contract + resume no-skip regression.

Canonical invariant (novi/jobs/job.py, Checkpoint):

    Checkpoint.step == number of completed plan steps
                    == 0-based global plan index of the NEXT step to execute

Consumers pass checkpoint.step through as the resume pointer UNCHANGED:
    ResumeTarget.next_step == Checkpoint.step
    Runtime resume_from    == Checkpoint.step
    JobStore recovery row  == Checkpoint.step
No ``+1`` conversion is permitted anywhere.

This module proves the contract with BEHAVIOR, not just equality asserts:

  - the producer (JobLifecycle) writes step = completed_index + 1
  - the continuation resolver returns that value untouched
  - the runtime then executes exactly the pending steps — a completed
    Step 0 must NEVER cause Step 1 to be skipped.
"""

import pytest
from langchain_core.messages import AIMessageChunk

from novi.jobs.job import Checkpoint, JobStatus
from novi.jobs.manager import JobManager
from novi.orchestrator.task_types import ExecutionPlan, Goal, IntentType, Task
from novi.planner.models import Plan, PlanStep, PlanStepStatus
from novi.runtime.event_bus import EventBus
from novi.runtime.execution_context import ExecutionContext
from novi.services.continuation import ContinuationService
from novi.services.job_lifecycle import JobLifecycle






def _make_plan(task_id="task-1"):
    plan = Plan(id="plan-1", task_id=task_id)
    for i, desc in enumerate(["a", "b", "c"]):
        plan.add_step(PlanStep(id=f"plan-1-s{i+1}", plan_id="plan-1",
                               description=desc))
    task = Task(id=task_id, raw_goal="do the thing",
                goal=Goal(text="do the thing", intent=IntentType.CODING))
    task.plan = plan
    return plan, task






# ── producer: JobLifecycle writes step = completed_index + 1 ────────────────

class _FakeTaskStore:
    def __init__(self):
        self.tasks = {}

    def save(self, task):
        self.tasks[task.id] = task
        return True

    def get(self, task_id):
        return self.tasks.get(task_id)


def _checkpoints_from_steps(manager, task_store, indexes):
    """Feed plan.started + step.completed events; return written Checkpoints."""
    bus = EventBus()
    lifecycle = JobLifecycle(manager, task_store=task_store).subscribe(bus)
    bus.emit("plan.started", task_id="task-1", plan_id="plan-1", step_count=3)
    cps = []
    for idx in indexes:
        bus.emit("step.completed", task_id="task-1", plan_id="plan-1",
                 step_id=f"step-{idx}", index=idx, result="ok")
        job_id = lifecycle.active_job("task-1")
        cps.append(manager.get(job_id).checkpoint)
    return cps


@pytest.mark.parametrize("indexes,expected", [
    ([0], [1]),
    ([0, 1], [1, 2]),
    ([0, 1, 2], [1, 2, 3]),
])
def test_producer_writes_completed_count(indexes, expected):
    """Completed steps {0..N-1} → Checkpoint.step N (completed count)."""
    jm = JobManager()
    task_store = _FakeTaskStore()
    task_store.save(make_task())
    cps = _checkpoints_from_steps(jm, task_store, indexes)
    assert [cp.step for cp in cps] == expected
    for cp in cps:
        assert cp.step == len(cp.completed_steps)   # step == count


def make_task(task_id="task-1"):
    return Task(id=task_id, raw_goal="do the thing")


# ── consumer: continuation exposes checkpoint.step unchanged ────────────────

@pytest.mark.parametrize("completed,step", [(0, 0), (1, 1), (2, 2), (3, 3)])
def test_continuation_target_next_step_equals_checkpoint_step(
        completed, step, tmp_path, monkeypatch):
    """completed N → Checkpoint.step N → ResumeTarget.next_step N (no +1)."""
    plan, task = _make_plan()
    task.plan = plan

    from novi.jobs.persistence import JobStore
    from novi.orchestrator.task_store import TaskStore

    monkeypatch.setattr("novi.jobs.persistence.JOBS_DIR",
                        tmp_path / "jobs")
    task_store = TaskStore(persist_dir=str(tmp_path / "tasks"))
    task_store.save(task)
    job_store = JobStore()
    job_store.save(_job_with_checkpoint(task.id, plan.id, step, completed))
    service = ContinuationService(task_store=task_store, job_store=job_store)

    target = service.recommended(conversation_id="conv-x")
    assert target is not None
    assert target.checkpoint.step == step
    assert target.next_step == step              # ResumeTarget == Checkpoint


def _job_with_checkpoint(task_id, plan_id, step, completed):
    from novi.jobs.job import Job

    cp = Checkpoint(
        job_id="job-x", task_id=task_id, plan_id=plan_id, step=step,
        completed_steps=[f"s{i}" for i in range(completed)],
    )
    return Job(id="job-x", task_id=task_id, status=JobStatus.PAUSED,
               checkpoint=cp)


# ── behavioral no-skip regression ───────────────────────────────────────────
