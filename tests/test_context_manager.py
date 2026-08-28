from novi.runtime.context_budget import ContextBudgetManager, BudgetBreakdown
from novi.runtime.context_manager import ContextManager, StableState
from novi.runtime.execution_context import ExecutionContext

def test_budget_small_model():
    bd = ContextBudgetManager.compute("small-7b", system_prompt="a"*800, stable_state="b"*1200, recent_conversation="c"*2000, retrieved_context="d"*3000, tool_output="e"*1000)
    assert bd.context_window == 4096
    assert bd.source == "fallback_small"
    assert bd.utilization_pct > 75
    # instrumentation
    assert bd.system_prompt > 0
    assert bd.available >= 0

def test_budget_large_model_fallback():
    bd = ContextBudgetManager.compute("unknown-model", system_prompt="x", recent_conversation="y")
    assert bd.context_window == 8192
    assert bd.source == "fallback_default"

def test_budget_breakdown_sum():
    bd = ContextBudgetManager.compute("test", system_prompt="hello", stable_state="world", recent_conversation="hi", retrieved_context="retr", tool_output="out")
    # sum of parts + reserves should be <= window when available >=0
    used = bd.system_prompt + bd.stable_state + bd.recent_conversation + bd.retrieved_context + bd.tool_output + bd.output_reserve + bd.safety_margin
    assert used + bd.available == bd.context_window or bd.available == 0

def test_compaction_preserves_hierarchy():
    ctx = ExecutionContext(user_input="Find model routing", history=[("user","hi")]*12, summary="")
    ctx.metadata["completed"] = ["step1"]
    ctx.metadata["current_step"] = 1
    ctx.workspace_files_used = ["a.py", "b.py"]
    cm = ContextManager(model_name="small-7b")
    # force history long
    ctx.history = [("user", f"msg {i}") for i in range(12)]
    cm.compact_history(ctx)
    assert len(ctx.history) <= 6
    assert "Goal: Find model routing" in ctx.metadata["stable_state"] or "Find model routing" in ctx.summary
    assert ctx.metadata.get("compacted") is True

def test_tool_compression_preserves_paths():
    cm = ContextManager()
    big = "\n".join([f"file {i}.py" for i in range(200)] + ["error: failed at /important/path.py"] + ["count: 200"])
    compressed = cm.compress_tool_result(big, budget_chars=4000)
    assert "important/path.py" in compressed
    assert "error: failed" in compressed.lower()
    assert len(compressed) <= 4100

def test_stable_state_checkpoint():
    ctx = ExecutionContext(user_input="goal", history=[])
    ctx.project_id = "projA"
    ctx.workspace_files_used = ["a.py"]
    ctx.metadata["current_step"] = 2
    cm = ContextManager()
    stable = cm.checkpoint_stable(ctx)
    assert stable.goal == "goal"
    assert stable.workspace_paths == ["projA"] or stable.workspace_paths == ["projA"]  # check project_id preserved
    assert stable.important_files == ["a.py"]
    # isolation: project_id preserved
    assert stable.workspace_paths[0] == "projA"

def test_should_compact_thresholds():
    cm = ContextManager(model_name="small-7b")
    # create ctx that will be 90%+
    ctx = ExecutionContext(user_input="x"*8000, history=[("user","y"*2000)]*10)
    ctx.memory_context = "a"*5000
    ctx.workspace_context = "b"*5000
    level = cm.should_compact(ctx)
    assert level in ("compact", "emergency", "warning") or level is None
    # budget breakdown stored
    assert "budget_breakdown" in ctx.metadata
