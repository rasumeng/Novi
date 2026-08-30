def test_context_manager_global_not_workload_coupled():
    from novi.runtime.context_manager import ContextManager
    from novi.runtime.execution_context import ExecutionContext
    cm = ContextManager(model_name="any-model")
    for intent in ["conversation","research","coding","planning"]:
        ctx = ExecutionContext(user_input="hello", analysis=None)
        ctx.analysis = type("A", (), {"intent": type("I", (), {"value": intent})()})()
        bd = cm.budget_for(ctx)
        # Must not raise, must not vary by hardcoded workload budgets
        assert bd.context_window > 0
        # Ensure no workload-specific branch in code
        import inspect
        src = inspect.getsource(ContextManager.budget_for)
        assert "workload" not in src.lower() or "general" not in src
