from novi.jobs.job import Job, JobStatus, Checkpoint

def test_checkpoint_stable_serialization():
    cp = Checkpoint(job_id="j1", step=2, task_id="t1", plan_id="p1", completed_steps=["s1"], stable={"goal": "Find routing", "important_files": ["a.py"], "project_id": "projA"})
    d = cp.to_dict()
    assert d["stable"]["goal"] == "Find routing"
    assert d["stable"]["project_id"] == "projA"
    # roundtrip via Job
    job = Job(id="j1", task_id="t1", status=JobStatus.NEEDS_CONTINUATION, checkpoint=cp)
    assert job.checkpoint.stable["goal"] == "Find routing"
    assert job.status == JobStatus.NEEDS_CONTINUATION
    assert not job.is_done or job.status.is_terminal is False  # NEEDS_CONTINUATION not terminal

def test_checkpoint_isolation_preserved():
    # Project A checkpoint should not be readable as B
    cpA = Checkpoint(job_id="jA", step=1, task_id="tA", stable={"project_id": "projA", "important_files": ["a.py"]})
    cpB = Checkpoint(job_id="jB", step=1, task_id="tB", stable={"project_id": "projB", "important_files": ["b.py"]})
    assert cpA.stable["project_id"] != cpB.stable["project_id"]
    # Simulate compaction preserves isolation
    from novi.runtime.context_manager import ContextManager, StableState
    from novi.runtime.execution_context import ExecutionContext
    ctxA = ExecutionContext(user_input="goal A", history=[])
    ctxA.project_id = "projA"
    ctxA.workspace_files_used = ["a.py"]
    cm = ContextManager()
    stableA = cm.checkpoint_stable(ctxA)
    ctxB = ExecutionContext(user_input="goal B", history=[])
    ctxB.project_id = "projB"
    ctxB.workspace_files_used = ["b.py"]
    stableB = cm.checkpoint_stable(ctxB)
    assert stableA.workspace_paths != stableB.workspace_paths
    assert "projA" in str(stableA.workspace_paths)
    assert "projB" in str(stableB.workspace_paths)
