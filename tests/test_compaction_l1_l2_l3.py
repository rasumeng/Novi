def test_compaction_preserves_project_isolation():
    from novi.runtime.context_manager import ContextManager
    from novi.runtime.execution_context import ExecutionContext
    cm = ContextManager(model_name="test-8k")
    ctx = ExecutionContext(user_input="Find routing", project_id="proj-A")
    ctx.workspace_context = "file: proj-A/src/foo.py content..."
    ctx.history = [("u","x"*1000)]*20
    # Fill until 88% -> should be compact (needs large retrieved to exceed 85%)
    bd = cm.budget_for(ctx, extra_retrieved="y"*16000)
    assert bd.utilization_pct > 85
    cm.compact_history(ctx)
    assert ctx.metadata["stable_state"]
    assert ctx.metadata["stable_state"]  # project_id preserved in stable
    assert "proj-A" in str(ctx.metadata.get("stable_state"))
    # also check stable_state_text if present
    if "stable_state_text" in ctx.metadata:
        assert "proj-A" in str(ctx.metadata.get("stable_state_text"))
    # Reconstruction does not leak proj-B
    assert "proj-B" not in str(ctx.workspace_context)
    # history truncated to 6, summary bounded
    assert len(ctx.history) == 6
    assert ctx.metadata.get("compacted") is True
    # L3 checkpoint preserves isolation
    stable = cm.checkpoint_stable(ctx)
    assert stable.project_id == "proj-A"
    assert "proj-B" not in stable.to_text()


def test_l1_compress_tool_result_keeps_paths_and_truncates():
    from novi.runtime.context_manager import ContextManager
    cm = ContextManager()
    big = "file: proj-A/src/foo.py\n" + "x"*5000 + "\nError: failed to load /tmp/foo.py\ncount: 42\n" + "y"*5000
    out = cm.compress_tool_result(big, budget_chars=4000)
    assert len(out) < len(big)
    assert len(out) <= 5000  # head+tail+keep within budget roughly
    assert "foo.py" in out
    assert "Error" in out or "error" in out.lower()
    assert "count" in out.lower()
    # short text not truncated
    small = "hello world"
    assert cm.compress_tool_result(small, budget_chars=4000) == small


def test_l1_wired_into_tool_executor():
    # Verify ToolExecutor._sanitize calls compress_tool_result for >4000
    import inspect
    from novi.runtime.tool_executor import ToolExecutor
    src = inspect.getsource(ToolExecutor._sanitize)
    assert "compress_tool_result" in src
    assert "4000" in src


def test_l2_history_truncation_and_stable_not_discarded():
    from novi.runtime.context_manager import ContextManager
    from novi.runtime.execution_context import ExecutionContext
    cm = ContextManager(model_name="test-8k")
    ctx = ExecutionContext(user_input="goal", project_id="proj-X")
    ctx.history = [(f"u{i}", f"msg-{i}") for i in range(10)]
    ctx.metadata["completed"] = ["step1"]
    cm.compact_history(ctx)
    assert len(ctx.history) == 6
    assert ctx.metadata["stable_state"] is not None
    # never discard StableState — still has project_id
    assert ctx.metadata["stable_state"]["project_id"] == "proj-X"
    # summary exists
    assert ctx.summary
    # second compaction still preserves
    cm.compact_history(ctx)
    assert "proj-X" in str(ctx.metadata["stable_state"])


def test_l2_thresholds_75_85_90():
    from novi.runtime.context_manager import ContextManager
    from novi.runtime.execution_context import ExecutionContext
    from novi.runtime.context_budget import ContextBudgetManager, BudgetBreakdown
    # direct budget level checks via should_compact
    cm = ContextManager(model_name="test-8k")
    # warning at ~75%
    # craft utilization by extra_retrieved sizing
    ctx = ExecutionContext(user_input="hi", project_id="proj-A")
    ctx.history = [("u","x"*100)]*5
    bd = cm.budget_for(ctx)
    assert ContextBudgetManager.should_compact(bd) is None or bd.utilization_pct < 75
    # force high utilization
    ctx2 = ExecutionContext(user_input="Find routing", project_id="proj-A")
    ctx2.history = [("u","x"*2000)]*20
    bd2 = cm.budget_for(ctx2, extra_retrieved="y"*12000)
    lvl = ContextBudgetManager.should_compact(bd2)
    assert lvl in ("compact", "emergency", "warning")
    # explicit breakdown boundaries
    assert ContextBudgetManager.should_compact(BudgetBreakdown(8192, 800,1200,0,0,0,1024,512,0,74.9)) is None
    assert ContextBudgetManager.should_compact(BudgetBreakdown(8192, 800,1200,0,0,0,1024,512,0,75.0)) == "warning"
    assert ContextBudgetManager.should_compact(BudgetBreakdown(8192, 800,1200,0,0,0,1024,512,0,85.0)) == "compact"
    assert ContextBudgetManager.should_compact(BudgetBreakdown(8192, 800,1200,0,0,0,1024,512,0,90.0)) == "emergency"


def test_l3_checkpoint_stable_persists_to_job_checkpoint():
    from novi.runtime.context_manager import ContextManager
    from novi.runtime.execution_context import ExecutionContext
    from novi.jobs.job import Checkpoint
    cm = ContextManager(model_name="any-model")
    ctx = ExecutionContext(user_input="Find routing", project_id="proj-A", conversation_id="conv-1")
    ctx.workspace_files_used = ["proj-A/src/foo.py"]
    ctx.metadata["errors"] = ["err1"]
    stable = cm.checkpoint_stable(ctx)
    assert stable.project_id == "proj-A"
    assert stable.conversation_id == "conv-1"
    cp = Checkpoint(job_id="j1", step=2, task_id="t1", plan_id="p1")
    cp.stable_state = stable
    assert cp.stable["project_id"] == "proj-A"
    assert cp.stable_state.project_id == "proj-A"
    # bounded messages/tool_states already but stable must survive
    d = cp.to_dict()
    assert d["stable"]["goal"] == "Find routing"


def test_runtime_injects_stable_state_text_when_truncated():
    import inspect
    from novi.runtime.runtime import NoviRuntime
    src = inspect.getsource(NoviRuntime._system_prompt)
    assert "stable_state_text" in src.lower()
    src2 = inspect.getsource(NoviRuntime.run_stream)
    assert "stable_state" in src2.lower()
    assert "compact" in src2.lower()
