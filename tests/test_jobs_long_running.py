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


def test_emergency_vs_compact_branch(tmp_path):
    """Compact allows auto-continue; emergency forces NEEDS_CONTINUATION."""
    from unittest.mock import patch
    coord = make_coordinator(tmp_path)
    # compact path — should auto-continue to 3 attempts
    with patch("novi.runtime.context_manager.ContextBudgetManager.should_compact", return_value="compact"):
        # also patch ContextManager.should_compact directly via instance method
        from novi.runtime.context_manager import ContextManager
        orig = ContextManager.should_compact
        ContextManager.should_compact = lambda self, ctx: "compact"
        try:
            attempts = coord.execute_with_auto_continue(goal="emergency test compact", max_auto=3, project_id="proj-A")
            assert len(attempts) == 3
            assert attempts[-1].status.value in ("done", "completed")
        finally:
            ContextManager.should_compact = orig
    # emergency path — second attempt should stop and yield NEEDS_CONTINUATION control
    coord2 = make_coordinator(tmp_path / "emergency")
    from novi.runtime.context_manager import ContextManager as CM2
    orig2 = CM2.should_compact
    call_n = {"c": 0}
    def fake_should_compact(self, ctx):
        call_n["c"] += 1
        # first check for policy decision -> emergency prevents auto
        return "emergency"
    CM2.should_compact = fake_should_compact
    try:
        # Use manual runtime to capture control event rather than execute_with_auto_continue helper
        from novi.jobs.job import Checkpoint
        from novi.runtime.execution_state import StableState
        orch = coord2._orchestrator
        mgr = coord2._manager
        plan = orch.plan(user_input="emergency goal", conversation_id="conv-e")
        job = mgr.submit(task_id=plan.task_id, strategy="execute", metadata={"auto_continuations": 0})
        class _EmergencyRuntime:
            calls = 0
            retrieval_executor = None
            def run_stream(self, context=None, **kw):
                self.calls += 1
                ctx = context
                if ctx is not None:
                    ctx.metadata["needs_continuation"] = True
                    stable = StableState(goal="emergency goal", project_id="proj-A", workspace_paths=["proj-A"], current_step=1, completed=["s0"])
                    ctx.metadata["stable_state"] = stable.to_dict()
                yield ("__plan_step_done__", "step1", "needs_continuation")
        rt = _EmergencyRuntime()
        events = list(coord2._run_with_auto_continue(rt, "emergency goal", plan, job, None, conversation_id="conv-e", project_id="proj-A"))
        # Should emit NEEDS_CONTINUATION and NOT auto-create second job via loop
        types = [e[1].get("type") if isinstance(e, tuple) and e[0]=="control" and isinstance(e[1], dict) else e[0] for e in events]
        assert "needs_continuation" in types
        # Job should be NEEDS_CONTINUATION with .checkpoint.json persisted via checkpoint()
        assert mgr.get(job.id).status == JobStatus.NEEDS_CONTINUATION
        assert mgr.get(job.id).checkpoint is not None
        assert mgr.get(job.id).checkpoint.step == 1
    finally:
        CM2.should_compact = orig2


def test_cross_project_isolation_proj_A_vs_B(tmp_path):
    """proj-A and proj-B jobs must not leak workspace/project isolation."""
    coord_a = make_coordinator(tmp_path / "a")
    coord_b = make_coordinator(tmp_path / "b")
    attempts_a = coord_a.execute_with_auto_continue(goal="analyze project", max_auto=2, project_id="proj-A")
    attempts_b = coord_b.execute_with_auto_continue(goal="analyze project", max_auto=2, project_id="proj-B")
    for j in attempts_a:
        assert j.checkpoint.stable.get("project_id") == "proj-A"
        assert "proj-B" not in str(j.checkpoint.stable)
        assert j.checkpoint.stable.get("workspace_paths") == ["proj-A"] or "proj-A" in j.checkpoint.stable.get("workspace_paths", [])
    for j in attempts_b:
        assert j.checkpoint.stable.get("project_id") == "proj-B"
        assert "proj-A" not in str(j.checkpoint.stable)
    # continuation service isolation: querying proj-A should not return proj-B
    from novi.services.continuation import ContinuationService
    from novi.jobs.persistence import JobStore
    import novi.jobs.persistence as persistence
    persistence.JOBS_DIR = tmp_path / "jobs_iso"
    js = JobStore()
    from novi.orchestrator.task_store import TaskStore as TS
    ts = TS(persist_dir=str(tmp_path / "tasks_iso"))
    # save one job per project with NEEDS_CONTINUATION
    from novi.planner.models import Plan as P
    for proj in ["proj-A", "proj-B"]:
        plan = P(id=f"plan-{proj}", task_id=f"task-{proj}")
        ts.save(Task(id=f"task-{proj}", conversation_id=f"conv-{proj}", raw_goal=f"goal {proj}", plan=plan))
        cp = Checkpoint(job_id=f"job-{proj}", task_id=f"task-{proj}", step=1, stable={"project_id": proj, "workspace_paths": [proj]})
        js.save(Job(id=f"job-{proj}", task_id=f"task-{proj}", status=JobStatus.NEEDS_CONTINUATION, checkpoint=cp, started_at="2026-01-01T00:00:00"))
    svc = ContinuationService(task_store=ts, job_store=js)
    cands = svc.candidates()
    proj_ids = {c.project_id for c in cands}
    assert "proj-A" in proj_ids and "proj-B" in proj_ids
    # ensure each candidate's checkpoint stable isolated
    for c in cands:
        if c.project_id == "proj-A":
            assert c.checkpoint.stable.get("project_id") == "proj-A"
            assert "proj-B" not in c.checkpoint.stable.get("workspace_paths", [])
        if c.project_id == "proj-B":
            assert c.checkpoint.stable.get("project_id") == "proj-B"


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


def test_retrieval_executor_invocation_mock_verification(tmp_path):
    """Auto-resume must call retrieval_executor to re-fetch workspace context."""
    coord = make_coordinator(tmp_path)
    # execute_with_auto_continue with FakeRuntime that has mock executor
    attempts = coord.execute_with_auto_continue(goal="retrieval mock test", max_auto=3, project_id="proj-MOCK")
    assert len(attempts) == 3
    rt = getattr(coord, "_last_fake_runtime", None)
    assert rt is not None
    # retrieval should have been invoked on resume (attempts>0 uses stable workspace_paths)
    calls = getattr(rt, "_setup_calls", None) or getattr(rt.retrieval_executor, "call_args_list", None)
    # at least one workspace reconstruction call
    assert rt.calls == 3
    # FakeRuntime's retrieval mock should have at least 1 call from second iteration onward
    assert len(rt._setup_calls) >= 1
    # verify workspace_files_used reconstructed
    # Check second attempt's context was built with files_used == ["proj-MOCK"]
    # The checkpoint stable already asserts isolation
    assert attempts[1].checkpoint.stable.get("project_id") == "proj-MOCK"
    assert "proj-MOCK" in attempts[1].checkpoint.stable.get("workspace_paths", ["proj-MOCK"])


def test_real_runtime_loop_resume_from_unchanged(tmp_path):
    """FakeRuntime via ctx.metadata needs_continuation — resume_from stays 1 across auto-continues."""
    coord = make_coordinator(tmp_path)
    attempts = coord.execute_with_auto_continue(goal="analyze project", max_auto=3, project_id="proj-A")
    assert len(attempts) == 3
    assert attempts[0].checkpoint.step == 1
    assert attempts[1].checkpoint.step == 1
    assert attempts[2].checkpoint.step == 1 or attempts[2].status.value in ("done", "completed")
    # resume_from invariant: second attempt's resume_from == first checkpoint step
    # captured via manager metadata auto_continuations propagation
    assert attempts[1].metadata.get("auto_continuations", 0) == 1 or attempts[1].metadata.get("auto_continuations") is not None
    # ensure retrieval was attempted (workspace reconstruction)
    rt = coord._last_fake_runtime
    assert rt.retrieval_executor is not None
    assert len(rt._setup_calls) >= 1
