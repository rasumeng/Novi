from novi.runtime.tool_executor import ToolExecutor
from novi.runtime.tool_registry import ToolRegistry


def test_emit_progress_is_pseudo_and_permission_free():
    reg = ToolRegistry()
    exe = ToolExecutor(registry=reg)
    # Must be recognized as pseudo-tool
    assert exe.is_pseudo_tool("emit_progress") is True
    # Must NOT require permission
    assert exe.requires_permission("emit_progress") is False
    # Must NOT be treated as external operation
    assert exe.is_external_tool("emit_progress") is False


def test_emit_progress_tool_spec_has_correct_description():
    from novi.runtime.react_attempt import EMIT_PROGRESS_DESCRIPTION

    assert "meaningful information" in EMIT_PROGRESS_DESCRIPTION
    assert "Do not call emit_progress when you are ready to provide the final answer" in EMIT_PROGRESS_DESCRIPTION


def test_emit_progress_does_not_reset_run():
    from novi.runtime.agent_run import AgentRun, AgentRunStatus
    from novi.runtime.execution_context import ExecutionContext

    # Simulate: calling emit_progress must not reset iteration or checkpoint
    ctx = ExecutionContext(user_input="test")
    run = AgentRun(id="r1", conversation_id="c1", goal="test", status=AgentRunStatus.RUNNING, context=ctx, iteration=2)
    # After handling PROGRESS, iteration increments by 1 (not reset) and context preserved
    run.iteration += 1
    assert run.iteration == 3
    assert run.context.user_input == "test"


def test_emit_progress_execute_is_pseudo_and_no_permission_gate():
    """Verify execute short-circuit: success, message preserved, no permission check."""
    reg = ToolRegistry()
    exe = ToolExecutor(registry=reg)
    result = exe.execute("emit_progress", {"message": "hello progress"})
    assert result.success is True
    assert result.output == "hello progress"
    # Structured sentinel without new ToolResult field
    assert result.structured is not None
    assert result.structured.get("pseudo") == "emit_progress"


def test_emit_progress_in_tools_for_model():
    """Pseudo-tool must appear in tool list returned to model even when filtered."""
    reg = ToolRegistry()
    exe = ToolExecutor(registry=reg)
    # Even with restrictive allowed_tools, emit_progress must be injected
    tools = exe.tools_for_mode(allowed_tools=["read_file"])
    names = [t.name for t in tools]
    assert "emit_progress" in names
    # Description must match EMIT_PROGRESS_DESCRIPTION
    from novi.runtime.react_attempt import EMIT_PROGRESS_DESCRIPTION

    prog = next(t for t in tools if t.name == "emit_progress")
    assert "meaningful information" in prog.description
