def test_stable_state_survives_compaction():
    from novi.runtime.execution_state import StableState
    from novi.runtime.execution_context import ExecutionContext

    ctx = ExecutionContext(user_input="Analyze auth tests", project_id="proj-1", conversation_id="conv-1")
    ctx.history = [("u", "a")] * 20
    s = StableState.from_context(ctx)
    s.completed = ["read auth.py", "found bug"]
    s.important_files = ["novi/runtime/runtime.py:123"]
    d = s.to_dict()
    s2 = StableState.from_dict(d)
    assert s2.goal == s.goal
    assert s2.project_id == "proj-1"
    assert s2.completed == ["read auth.py", "found bug"]
    # Bounded
    assert len(s2.to_text(1200)) <= 1200


def test_stable_state_conversation_id_roundtrip():
    from novi.runtime.execution_state import StableState
    from novi.runtime.execution_context import ExecutionContext

    ctx = ExecutionContext(user_input="goal", project_id="proj-1", conversation_id="conv-42")
    s = StableState.from_context(ctx)
    assert s.conversation_id == "conv-42"
    assert s.project_id == "proj-1"
    d = s.to_dict()
    s2 = StableState.from_dict(d)
    assert s2.conversation_id == "conv-42"
    assert s2.project_id == "proj-1"
    assert len(s2.to_text(1200)) <= 1200


def test_checkpoint_stable_typed_property():
    from novi.jobs.job import Checkpoint
    from novi.runtime.execution_state import StableState

    s = StableState(goal="Find routing", project_id="projA", conversation_id="conv-1", important_files=["a.py"])
    cp = Checkpoint(job_id="j1", step=2, task_id="t1", plan_id="p1")
    cp.stable_state = s
    assert cp.stable["goal"] == "Find routing"
    assert cp.stable["project_id"] == "projA"
    # typed getter
    s2 = cp.stable_state
    assert s2 is not None
    assert s2.goal == "Find routing"
    assert s2.project_id == "projA"
    # to_dict preserves stable
    d = cp.to_dict()
    assert d["stable"]["goal"] == "Find routing"


def test_stable_state_not_workload_coupled():
    import inspect
    from novi.runtime.execution_state import StableState

    src = inspect.getsource(StableState.from_context)
    assert "workload" not in src.lower()


def test_stable_from_context_preserves_errors_and_discoveries():
    from novi.runtime.execution_state import StableState
    from novi.runtime.execution_context import ExecutionContext

    ctx = ExecutionContext(user_input="fix auth", project_id="proj-x", conversation_id="conv-x")
    ctx.workspace_files_used = ["novi/runtime/runtime.py:10", "novi/jobs/job.py:81"]
    ctx.metadata["errors"] = ["error A", "error B"]
    ctx.metadata["completed"] = ["step 1"]
    ctx.metadata["current_step"] = 3
    s = StableState.from_context(ctx)
    assert "novi/runtime/runtime.py:10" in s.discoveries
    assert "novi/runtime/runtime.py:10" in s.important_files
    assert "error A" in s.errors
    assert s.completed == ["step 1"]
    assert s.current_step == 3
    assert s.project_id == "proj-x"


def test_reexport_from_context_manager():
    from novi.runtime.context_manager import StableState as CMState
    from novi.runtime.execution_state import StableState as ESState

    assert CMState is ESState
