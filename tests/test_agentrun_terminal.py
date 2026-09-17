"""Task 7: terminal invariants — exactly-one-terminal, ephemeral status not in history."""

import uuid
import pytest
from novi.runtime.agent_run import AgentRun, AgentRunStatus
from novi.runtime.agent_action import AgentAction, AgentActionType
from novi.runtime.agent_events import AgentEvent
from novi.runtime.execution_context import ExecutionContext
from novi.services.execution import ExecutionCoordinator, AgentCancelled, ExpectedExecutionError
from novi.runtime.trace import ExecutionTrace


def _make_run():
    ctx = ExecutionContext(user_input="hello")
    ctx.conversation_id = "conv-1"
    ctx.trace = ExecutionTrace(user_input="hello")
    run = AgentRun(id=f"run-{uuid.uuid4().hex[:8]}", conversation_id="conv-1", goal="hello", status=AgentRunStatus.RUNNING, context=ctx)
    return run


def _patch_no_compact(monkeypatch):
    from novi.runtime.context_manager import ContextManager
    monkeypatch.setattr(ContextManager, "should_compact", lambda self, ctx: None)
    monkeypatch.setattr(ContextManager, "compact_history", lambda self, ctx: None)
    # checkpoint stable not needed when no compaction, but stub anyway
    from novi.jobs.job import Checkpoint
    monkeypatch.setattr(ContextManager, "checkpoint_stable", lambda self, ctx: Checkpoint(job_id="j1", task_id="t1", plan_id="", step=1, completed_steps=[]))


def test_finish_emits_completed(monkeypatch):
    _patch_no_compact(monkeypatch)
    run = _make_run()
    coord = ExecutionCoordinator()

    def fake(ctx, r, emit):
        mid = f"msg-{uuid.uuid4().hex[:8]}"
        emit(AgentEvent(type="message.started", run_id=r.id, conversation_id=r.conversation_id, message_id=mid))
        emit(AgentEvent(type="message.delta", run_id=r.id, conversation_id=r.conversation_id, message_id=mid, message="done"))
        emit(AgentEvent(type="message.completed", run_id=r.id, conversation_id=r.conversation_id, message_id=mid, message="done"))
        return AgentAction(type=AgentActionType.FINISH, message="done")

    coord._run_react = fake  # type: ignore
    coord._run_graph = fake  # type: ignore
    events = []
    coord.execute(run, lambda e: events.append(e))
    terminals = [e for e in events if e.type in ("run.completed", "run.failed", "run.cancelled")]
    assert len(terminals) == 1
    assert terminals[0].type == "run.completed"
    assert run.status == AgentRunStatus.COMPLETED
    assert run.finished
    assert events[0].type == "run.started"
    assert events[-1].type == "run.completed"


def test_cancellation_never_emits_completed(monkeypatch):
    _patch_no_compact(monkeypatch)
    run = _make_run()
    coord = ExecutionCoordinator()

    def fake(ctx, r, emit):
        raise AgentCancelled("user stopped")

    coord._run_react = fake  # type: ignore
    coord._run_graph = fake  # type: ignore
    events = []
    coord.execute(run, lambda e: events.append(e))
    assert run.status == AgentRunStatus.CANCELLED
    assert run.finished
    terminals = [e for e in events if e.type in ("run.completed", "run.failed", "run.cancelled")]
    assert len(terminals) == 1
    assert terminals[0].type == "run.cancelled"
    assert not any(e.type == "run.completed" for e in events)
    assert not any(e.type == "run.failed" for e in events)


def test_failure_never_emits_completed(monkeypatch):
    _patch_no_compact(monkeypatch)
    run = _make_run()
    coord = ExecutionCoordinator()

    def fake(ctx, r, emit):
        raise ExpectedExecutionError("permission denied terminal")

    coord._run_react = fake  # type: ignore
    coord._run_graph = fake  # type: ignore
    events = []
    coord.execute(run, lambda e: events.append(e))
    assert run.status == AgentRunStatus.FAILED
    assert run.finished
    terminals = [e for e in events if e.type in ("run.completed", "run.failed", "run.cancelled")]
    assert len(terminals) == 1
    assert terminals[0].type == "run.failed"
    assert "permission denied" in (terminals[0].error or "")
    assert not any(e.type == "run.completed" for e in events)
    assert not any(e.type == "run.cancelled" for e in events)


def test_unexpected_exception_logs_and_reraises_internal_error(monkeypatch):
    _patch_no_compact(monkeypatch)
    run = _make_run()
    coord = ExecutionCoordinator()

    def fake(ctx, r, emit):
        raise RuntimeError("boom")

    coord._run_react = fake  # type: ignore
    coord._run_graph = fake  # type: ignore
    events = []
    with pytest.raises(RuntimeError, match="boom"):
        coord.execute(run, lambda e: events.append(e))
    assert run.status == AgentRunStatus.FAILED
    terminals = [e for e in events if e.type in ("run.completed", "run.failed", "run.cancelled")]
    assert len(terminals) == 1
    assert terminals[0].type == "run.failed"
    assert terminals[0].error == "Internal error"
    assert not any(e.type == "run.completed" for e in events)


def test_every_run_emits_exactly_one_terminal_variants(monkeypatch):
    """Parametric check: FINISH, cancelled, failed each yield exactly one terminal."""
    from novi.runtime.context_manager import ContextManager
    from novi.jobs.job import Checkpoint

    def run_with_fake(fake_fn, expect_type):
        run = _make_run()
        coord = ExecutionCoordinator()
        # stub compaction off
        orig_should = ContextManager.should_compact
        orig_compact = ContextManager.compact_history
        orig_checkpoint = ContextManager.checkpoint_stable
        ContextManager.should_compact = lambda self, ctx: None  # type: ignore
        ContextManager.compact_history = lambda self, ctx: None  # type: ignore
        ContextManager.checkpoint_stable = lambda self, ctx: Checkpoint(job_id="j1", task_id="t1", plan_id="", step=1)  # type: ignore
        try:
            coord._run_react = fake_fn  # type: ignore
            coord._run_graph = fake_fn  # type: ignore
            events = []
            try:
                coord.execute(run, lambda e: events.append(e))
            except Exception:
                pass
            terminals = [e for e in events if e.type in ("run.completed", "run.failed", "run.cancelled")]
            assert len(terminals) == 1, f"expected one terminal, got {terminals}"
            assert terminals[0].type == expect_type
            assert run.finished
            return events, run
        finally:
            ContextManager.should_compact = orig_should  # type: ignore
            ContextManager.compact_history = orig_compact  # type: ignore
            ContextManager.checkpoint_stable = orig_checkpoint  # type: ignore

    def fake_finish(ctx, r, emit):
        return AgentAction(type=AgentActionType.FINISH, message="done")

    def fake_cancel(ctx, r, emit):
        raise AgentCancelled("stop")

    def fake_expected(ctx, r, emit):
        raise ExpectedExecutionError("perm")

    run_with_fake(fake_finish, "run.completed")
    run_with_fake(fake_cancel, "run.cancelled")
    run_with_fake(fake_expected, "run.failed")


def test_ephemeral_status_never_in_history_or_checkpoint(monkeypatch):
    """Compaction/status events must NOT enter history; only PROGRESS/FINISH do."""
    from novi.runtime.context_manager import ContextManager
    from novi.jobs.job import Checkpoint

    run = _make_run()
    # ensure history starts empty
    run.context.history = []

    coord = ExecutionCoordinator()

    actions = [
        AgentAction(type=AgentActionType.PROGRESS, message="I found the relevant files."),
        AgentAction(type=AgentActionType.FINISH, message="Fixed and tested."),
    ]

    def fake(ctx, r, emit):
        act = actions.pop(0)
        mid = f"msg-{uuid.uuid4().hex[:8]}"
        emit(AgentEvent(type="message.started", run_id=r.id, conversation_id=r.conversation_id, message_id=mid))
        emit(AgentEvent(type="message.delta", run_id=r.id, conversation_id=r.conversation_id, message_id=mid, message=act.message or ""))
        emit(AgentEvent(type="message.completed", run_id=r.id, conversation_id=r.conversation_id, message_id=mid, message=act.message))
        # Ephemeral status that coordinator would NOT put in history, but inner layer might emit status
        emit(AgentEvent(type="status", run_id=r.id, conversation_id=r.conversation_id, message="Still working", phase="thinking", detail="Still working"))
        emit(AgentEvent(type="tool.started", run_id=r.id, conversation_id=r.conversation_id, tool="read_file", args={"p": "a.py"}))
        emit(AgentEvent(type="tool.completed", run_id=r.id, conversation_id=r.conversation_id, tool="read_file", result="ok"))
        return act

    coord._run_react = fake  # type: ignore
    coord._run_graph = fake  # type: ignore

    calls = {"n": 0}
    def fake_should(self, ctx):
        calls["n"] += 1
        return "compact" if calls["n"] == 1 else None

    def fake_compact(self, ctx):
        ctx.metadata["compacted"] = True
        # Ensure compact does NOT add ephemeral text to history
        # (real impl stores stable_state only)

    def fake_checkpoint(self, ctx):
        return Checkpoint(job_id="j1", task_id="t1", plan_id="", step=1, completed_steps=[], stable={"goal": "hello"})

    monkeypatch.setattr(ContextManager, "should_compact", fake_should)
    monkeypatch.setattr(ContextManager, "compact_history", fake_compact)
    monkeypatch.setattr(ContextManager, "checkpoint_stable", fake_checkpoint)

    events = []
    coord.execute(run, lambda e: events.append(e))

    # Terminal invariant
    assert events[-1].type == "run.completed"
    # Compaction events present but ephemeral
    assert any(e.type == "context.compacting" for e in events)
    assert any(e.type == "context.compacted" for e in events)

    # History must contain PROGRESS and FINISH only, not status/ephemeral
    assistant_msgs = [m for role, m in run.context.history if role == "assistant"]
    assert "I found the relevant files." in assistant_msgs
    assert "Fixed and tested." in assistant_msgs
    assert all("Still working" not in m for m in assistant_msgs)
    assert all("Compacting context" not in m for m in assistant_msgs)

    # Checkpoint stable exists but history/ephemeral not in checkpoint stable
    assert run.checkpoint is not None
    stable = run.checkpoint.stable
    stable_str = str(stable)
    assert "Still working" not in stable_str
    assert "Compacting" not in stable_str

    # Verify status events never вошли history via direct check
    history_text = " ".join(m for _, m in run.context.history)
    assert "Still working" not in history_text
