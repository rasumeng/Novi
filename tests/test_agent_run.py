from novi.runtime.agent_run import AgentRunStatus, AgentRun


def test_agent_run_status_is_typed_enum():
    assert AgentRunStatus.RUNNING.value == "running"
    assert AgentRunStatus.COMPLETED.value == "completed"
    assert AgentRunStatus.FAILED.value == "failed"
    assert AgentRunStatus.CANCELLED.value == "cancelled"
    # PAUSED must NOT exist in beta
    assert not hasattr(AgentRunStatus, "PAUSED")


def test_agent_run_finished_invariant():
    ctx = {"user_input": "hello"}
    run = AgentRun(id="run-1", conversation_id="conv-1", goal="hello", status=AgentRunStatus.RUNNING, context=ctx)
    assert not run.finished
    run.status = AgentRunStatus.COMPLETED
    assert run.finished
    run.status = AgentRunStatus.FAILED
    assert run.finished
    run.status = AgentRunStatus.CANCELLED
    assert run.finished


def test_agent_run_token_usage_nullable():
    ctx = {"user_input": "hi"}
    run = AgentRun(id="r", conversation_id="c", goal="hi", status=AgentRunStatus.RUNNING, context=ctx, token_usage=None)
    assert run.token_usage is None
    run.token_usage = 42
    assert run.token_usage == 42