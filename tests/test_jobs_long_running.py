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
from novi.services.execution import ExecutionCoordinator
from novi.planner.models import Plan, PlanStep


def _harness_orchestrator(task_store):
    from novi.orchestrator import Orchestrator
    from novi.orchestrator.intent import IntentDetector
    from novi.orchestrator.task_types import ExecutionPlan, ExecutionStrategy, Goal, IntentType

    class _FakeIntent(IntentDetector):
        def detect(self, user_input, history=None, has_images=False):
            return (IntentType.CODING, 1.0)

    class _Harness(Orchestrator):
        def __init__(self, ts):
            super().__init__(intent_detector=_FakeIntent(), task_store=ts)
        def plan(self, user_input, has_images=False, conversation_id=None, force_intent=None, **kw):
            task = self.task_store.get_or_create(
                conversation_id=conversation_id or "",
                goal_text=user_input[:500],
                intent=IntentType.CODING,
            )
            plan_obj = Plan(id=f"plan-{task.id}", task_id=task.id)
            plan_obj.add_step(PlanStep(id=f"plan-{task.id}-s1", plan_id=plan_obj.id, description="step1"))
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
    return _Harness(task_store)


def make_coordinator(tmp_path=None):
    # helper used by brief's test snippet
    import tempfile
    from pathlib import Path
    if tmp_path is None:
        tmp_path = Path(tempfile.mkdtemp())
    ts = TaskStore(persist_dir=str(Path(tmp_path) / "tasks"))
    jm = JobManager()
    orch = _harness_orchestrator(ts)
    return ExecutionCoordinator(orchestrator=orch, job_manager=jm, task_store=ts)


def test_job_auto_continues_on_needs_continuation(tmp_path):
    # Simulate runtime yielding needs_continuation; coordinator should auto-resume up to 3x
    coord = make_coordinator(tmp_path)
    attempts = coord.execute_with_auto_continue(goal="analyze project", max_auto=3)
    assert len(attempts) == 3
    assert attempts[-1].status in (JobStatus.DONE, JobStatus.COMPLETED, "done", "completed") or str(attempts[-1].status) == "done" or attempts[-1].status.value == "done"
    # No user "continue" required, checkpoint.step propagated unchanged
    assert attempts[1].checkpoint.step == 1
    # also first checkpoint step ==1
    assert attempts[0].checkpoint.step == 1
    # project_id isolation preserved
    assert attempts[1].checkpoint.stable.get("project_id") == "proj-A"


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


def test_auto_continuation_preserves_checkpoint_step_contract(tmp_path):
    coord = make_coordinator(tmp_path)
    attempts = coord.execute_with_auto_continue(goal="goal step contract", max_auto=2, project_id="proj-X")
    # step unchanged across auto-continue (first two attempts both step 1)
    assert attempts[0].checkpoint.step == attempts[1].checkpoint.step == 1
    # project_id preserved through stable
    for a in attempts:
        assert a.checkpoint.stable.get("project_id") == "proj-X"


def test_needs_continuation_user_continue_via_reopen(tmp_path):
    # User continuation path: when cannot auto-continue, job stays NEEDS_CONTINUATION and user "continue" resumes via reopen without restating goal
    ts = TaskStore(persist_dir=str(tmp_path / "tasks"))
    jm = JobManager()
    orch = _harness_orchestrator(ts)
    # create task/job with NEEDS_CONTINUATION
    from novi.planner.models import Plan as P
    task = ts.get_or_create(conversation_id="conv-1", goal_text="analyze project", intent=__import__("novi.orchestrator.task_types", fromlist=["IntentType"]).IntentType.CODING)
    plan = P(id="plan-1", task_id=task.id)
    plan.add_step(PlanStep(id="plan-1-s1", plan_id="plan-1", description="a"))
    task.plan = plan
    ts.update(task)
    cp = Checkpoint(job_id="job-orig", task_id=task.id, plan_id="plan-1", step=1, completed_steps=["s1"], stable={"project_id": "proj-A", "goal": "analyze project", "workspace_paths": ["proj-A"]})
    orig = Job(id="job-orig", task_id=task.id, status=JobStatus.NEEDS_CONTINUATION, checkpoint=cp)
    jm._jobs[orig.id] = orig
    assert orig.can_resume is True
    # user says "continue" → reopen creates new attempt with resume_from = checkpoint.step unchanged
    new_job = jm.reopen(orig.id)
    assert new_job is not None
    assert new_job.checkpoint.step == 1
    assert new_job.checkpoint.stable.get("project_id") == "proj-A"
    # reconstruct workspace via StableState.workspace_paths
    from novi.runtime.execution_state import StableState
    stable = StableState.from_dict(new_job.checkpoint.stable)
    assert "proj-A" in stable.workspace_paths
    assert stable.project_id == "proj-A"


def test_checkpoint_step_never_plus_one(tmp_path):
    # Ensure checkpoint.step contract: completed count == next index, never +1
    coord = make_coordinator(tmp_path)
    attempts = coord.execute_with_auto_continue(goal="step contract", max_auto=3)
    # first job's checkpoint step is 1 (1 completed), second job resumes from 1 and its checkpoint also 1 before second step completes
    assert attempts[0].checkpoint.step == 1
    assert attempts[1].checkpoint.step == 1
    # next_step logic in continuation service also unchanged
    from novi.services.continuation import ContinuationService
    from novi.jobs.persistence import JobStore
    import novi.jobs.persistence as persistence
    ts2 = TaskStore(persist_dir=str(tmp_path / "tasks2"))
    persistence.JOBS_DIR = tmp_path / "jobs2"
    js2 = JobStore()
    from novi.planner.models import Plan as P2
    plan = P2(id="plan-9", task_id="task-9")
    ts2.save(Task(id="task-9", conversation_id="conv-9", raw_goal="x", plan=plan))
    cp = Checkpoint(job_id="job-9", task_id="task-9", step=2, completed_steps=["s1","s2"], stable={"project_id": "proj-A"})
    js2.save(Job(id="job-9", task_id="task-9", status=JobStatus.NEEDS_CONTINUATION, checkpoint=cp, started_at="2026-01-01T00:00:00"))
    svc = ContinuationService(task_store=ts2, job_store=js2)
    tgt = svc.recommended(conversation_id="conv-9")
    assert tgt is not None
    assert tgt.next_step == 2
    assert tgt.checkpoint.step == 2
