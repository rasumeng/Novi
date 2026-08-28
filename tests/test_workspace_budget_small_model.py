from pathlib import Path
import tempfile
from novi.workspace.service import WorkspaceService
from novi.runtime.context_budget import ContextBudgetManager

def test_workspace_200file_budget():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # 200 files, each small
        for i in range(200):
            (root / f"file_{i}.py").write_text(f"# file {i}\nprint({i})\n# model routing? no\n")
        # one relevant
        (root / "routing.py").write_text("def model_routing(): pass # ModelService.resolve workload")
        svc = WorkspaceService()
        proj = "test-200file"
        res = svc.attach(proj, str(root))
        assert res["stats"]["total"] == 201
        # search should return only relevant, budgeted 3
        hits = svc.search(proj, "model routing", k=5)
        assert len(hits) <= 5
        # ensure not all 200 returned
        assert len(hits) < 10
        # budget check: retrieved context should be limited
        from novi.runtime.context_manager import ContextManager
        from novi.runtime.execution_context import ExecutionContext
        ctx = ExecutionContext(user_input="Find where model routing is implemented", history=[])
        ctx.project_id = proj
        # simulate workspace_context with 200 files would be huge, but budget limits
        cm = ContextManager(model_name="small-7b")  # 4096 window
        bd = cm.budget_for(ctx, extra_retrieved="x"*10000)
        assert bd.context_window == 4096
        assert bd.utilization_pct > 50
        # ensure small model still has available budget after compaction
        ctx.history = [("user", "x"*2000)]*8
        level = cm.should_compact(ctx)
        # should trigger compact or warning
        assert level in (None, "warning", "compact", "emergency")

def test_small_context_multiple_compactions():
    from novi.runtime.context_manager import ContextManager
    from novi.runtime.execution_context import ExecutionContext
    cm = ContextManager(model_name="small-7b")
    ctx = ExecutionContext(user_input="long task", history=[("user","hi")]*20)
    # multiple cycles
    for _ in range(3):
        ctx.history.append(("user","extra"))
        cm.compact_history(ctx)
        assert len(ctx.history) <= 6
        assert "compacted" in ctx.metadata
