import inspect


def test_context_manager_global_not_workload_coupled():
    from novi.runtime.context_manager import ContextManager
    from novi.runtime.execution_context import ExecutionContext

    cm = ContextManager(model_name="any-model")
    windows = []
    for intent in ["conversation", "research", "coding", "planning"]:
        ctx = ExecutionContext(user_input="hello", analysis=None)
        ctx.analysis = type("A", (), {"intent": type("I", (), {"value": intent})()})()
        bd = cm.budget_for(ctx)
        # Must not raise, must not vary by hardcoded workload budgets
        assert bd.context_window > 0
        windows.append(bd.context_window)

    # Ensure no workload-specific branch in code — strict
    src = inspect.getsource(ContextManager.budget_for)
    assert "workload" not in src.lower()
    assert "WORKLOADS" not in src
    assert "_CAPABILITY_TO_WORKLOAD" not in src

    # Budget invariance across intents — windows must be equal
    assert len(set(windows)) == 1, f"windows vary by intent: {windows}"


def test_context_manager_budget_per_run_model_divergence():
    from novi.runtime.context_manager import ContextManager
    from novi.runtime.execution_context import ExecutionContext

    # cm default is small model (4096), ctx overrides to default (8192)
    cm = ContextManager(model_name="my-mini-model")
    ctx = ExecutionContext(user_input="hello", analysis=None)
    ctx.model_name = "any-model"
    bd = cm.budget_for(ctx)
    assert bd.context_window == 8192
    assert bd.model_name == "any-model"

    # ctx empty falls back to cm model
    ctx2 = ExecutionContext(user_input="hello", analysis=None)
    ctx2.model_name = ""
    bd2 = cm.budget_for(ctx2)
    assert bd2.context_window == 4096
    assert bd2.model_name == "my-mini-model"

    # reverse divergence: cm default 8192, ctx small 4096
    cm2 = ContextManager(model_name="any-model")
    ctx3 = ExecutionContext(user_input="hello", analysis=None)
    ctx3.model_name = "llama3:7b"
    bd3 = cm2.budget_for(ctx3)
    assert bd3.context_window == 4096


def test_context_manager_retrieved_aggregates_and_project_id():
    from novi.runtime.context_manager import ContextManager
    from novi.runtime.execution_context import ExecutionContext
    from novi.runtime.context_budget import estimate_tokens

    cm = ContextManager(model_name="any-model")

    ctx = ExecutionContext(user_input="hello", analysis=None)
    ctx.memory_context = "mem-" * 100
    ctx.project_context = "proj-" * 100
    ctx.workspace_context = "ws-" * 100
    ctx.grounding_text = "ground-" * 100
    extra = "extra-" * 50
    bd = cm.budget_for(ctx, extra_retrieved=extra)

    expected_retrieved = estimate_tokens(
        ctx.memory_context + ctx.project_context + ctx.workspace_context + ctx.grounding_text + extra
    )
    assert bd.retrieved_context == expected_retrieved
    assert bd.retrieved_context > 0

    # project_id wiring must not affect budget (StableState carries it, but budgeting does not branch)
    ctx_a = ExecutionContext(user_input="hello", analysis=None)
    ctx_a.memory_context = ctx.memory_context
    ctx_a.project_context = ctx.project_context
    ctx_a.workspace_context = ctx.workspace_context
    ctx_a.grounding_text = ctx.grounding_text
    ctx_a.project_id = "proj-123"
    bd_a = cm.budget_for(ctx_a, extra_retrieved=extra)

    ctx_b = ExecutionContext(user_input="hello", analysis=None)
    ctx_b.memory_context = ctx.memory_context
    ctx_b.project_context = ctx.project_context
    ctx_b.workspace_context = ctx.workspace_context
    ctx_b.grounding_text = ctx.grounding_text
    ctx_b.project_id = "proj-999"
    bd_b = cm.budget_for(ctx_b, extra_retrieved=extra)

    assert bd_a.context_window == bd_b.context_window
    assert bd_a.available == bd_b.available
    assert bd_a.retrieved_context == bd_b.retrieved_context

    # StableState correctly wires project_id
    ctx_a.project_id = "proj-xyz"
    st = cm.checkpoint_stable(ctx_a)
    assert st.project_id == "proj-xyz"
    ctx_b.project_id = ""
    st2 = cm.checkpoint_stable(ctx_b)
    assert st2.project_id == ""
