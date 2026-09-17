"""Task 5: ExecutionCoordinator Outer Loop (Correct Control Flow).

Critical guard: Task 4 owns native streaming (message.started/delta/completed).
Task 5 MUST NOT re-emit message.* — it only handles run.started,
context.compacting/compacted, terminal run.completed/failed/cancelled,
and interprets AgentAction. It also appends PROGRESS/FINISH to history
and respects MAX_AGENT_ITERATIONS separate from ExecutionContext.max_steps.
"""

import uuid

from novi.runtime.agent_run import AgentRun, AgentRunStatus
from novi.runtime.agent_action import AgentAction, AgentActionType
from novi.runtime.agent_events import AgentEvent
from novi.runtime.execution_context import ExecutionContext
from novi.services.execution import ExecutionCoordinator, MAX_AGENT_ITERATIONS, AgentCancelled, ExpectedExecutionError


def _make_run():
    ctx = ExecutionContext(user_input="hi")
    ctx.conversation_id = "conv-1"
    run = AgentRun(id="run-1", conversation_id="conv-1", goal="hi", status=AgentRunStatus.RUNNING, context=ctx)
    return run


def test_execute_emits_exactly_one_terminal_event():
    run = _make_run()
    coord = ExecutionCoordinator()

    def fake_react(ctx, r, emit):
        # Simulate inner layer emitting message lifecycle for FINISH (ownership)
        mid = f"msg-{uuid.uuid4().hex[:8]}"
        emit(AgentEvent(type="message.started", run_id=r.id, conversation_id=r.conversation_id, message_id=mid))
        emit(AgentEvent(type="message.delta", run_id=r.id, conversation_id=r.conversation_id, message_id=mid, message="done"))
        emit(AgentEvent(type="message.completed", run_id=r.id, conversation_id=r.conversation_id, message_id=mid, message="done"))
        return AgentAction(type=AgentActionType.FINISH, message="done")

    coord._run_react = fake_react  # type: ignore[method-assign]
    coord._run_graph = fake_react  # type: ignore[method-assign]

    events = []
    coord.execute(run, lambda e: events.append(e))

    terminals = [e for e in events if e.type in ("run.completed", "run.failed", "run.cancelled")]
    assert len(terminals) == 1, f"expected exactly one terminal, got {terminals}"
    assert run.finished
    assert run.status == AgentRunStatus.COMPLETED
    assert events[0].type == "run.started"
    assert events[-1].type == "run.completed"
    # Ensure exactly one run.started
    assert sum(1 for e in events if e.type == "run.started") == 1
    # Ensure message lifecycle was not duplicated by coordinator (exactly one completed)
    assert sum(1 for e in events if e.type == "message.completed") == 1


def test_compaction_runs_after_progress_not_skipped(monkeypatch):
    run = _make_run()
    coord = ExecutionCoordinator()

    actions = [
        AgentAction(type=AgentActionType.PROGRESS, message="I found the relevant files."),
        AgentAction(type=AgentActionType.FINISH, message="Fixed and tested."),
    ]

    def fake_react(ctx, r, emit):
        act = actions.pop(0)
        # Inner layer emits message lifecycle (Task 4 ownership)
        mid = f"msg-{uuid.uuid4().hex[:8]}"
        emit(AgentEvent(type="message.started", run_id=r.id, conversation_id=r.conversation_id, message_id=mid))
        # Simulate chunked deltas
        for word in (act.message or "").split():
            emit(AgentEvent(type="message.delta", run_id=r.id, conversation_id=r.conversation_id, message_id=mid, message=word + " "))
        emit(AgentEvent(type="message.completed", run_id=r.id, conversation_id=r.conversation_id, message_id=mid, message=act.message))
        # Also emit a tool event to verify compaction after tools
        emit(AgentEvent(type="tool.started", run_id=r.id, conversation_id=r.conversation_id, tool="read_file", args={"path": "a.py"}))
        emit(AgentEvent(type="tool.completed", run_id=r.id, conversation_id=r.conversation_id, tool="read_file", result="ok"))
        return act

    coord._run_react = fake_react  # type: ignore
    coord._run_graph = fake_react  # type: ignore

    # Force compaction on first iteration only
    from novi.runtime.context_manager import ContextManager

    calls = {"n": 0}
    orig_should = ContextManager.should_compact

    def fake_should(self, ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            return "compact"
        return None

    monkeypatch.setattr(ContextManager, "should_compact", fake_should)
    # Don't actually truncate history in test — stub compact to avoid side effects
    orig_compact = ContextManager.compact_history
    orig_checkpoint = ContextManager.checkpoint_stable

    def fake_compact(self, ctx):
        ctx.metadata["compacted"] = True

    def fake_checkpoint(self, ctx):
        from novi.jobs.job import Checkpoint

        return Checkpoint(job_id="j1", task_id="t1", plan_id="", step=1, completed_steps=[])

    monkeypatch.setattr(ContextManager, "compact_history", fake_compact)
    monkeypatch.setattr(ContextManager, "checkpoint_stable", fake_checkpoint)

    events = []
    coord.execute(run, lambda e: events.append(e))

    # Terminal invariant still holds
    assert events[0].type == "run.started"
    assert events[-1].type == "run.completed"

    # Compaction events appear
    compacting = [e for e in events if e.type == "context.compacting"]
    compacted = [e for e in events if e.type == "context.compacted"]
    assert len(compacting) == 1 and len(compacted) == 1
    # Order: first message.completed < tool activity < compaction < second message lifecycle < run.completed
    idx_first_msg_done = next(i for i, e in enumerate(events) if e.type == "message.completed")
    idx_tool = next(i for i, e in enumerate(events) if e.type == "tool.started")
    idx_compacting = next(i for i, e in enumerate(events) if e.type == "context.compacting")
    idx_compacted = next(i for i, e in enumerate(events) if e.type == "context.compacted")
    # Second message started should be after compaction
    # Find second message.started (different message_id)
    msg_ids = [e.message_id for e in events if e.type == "message.started"]
    assert len(msg_ids) == 2 and msg_ids[0] != msg_ids[1]
    idx_second_start = next(i for i, e in enumerate(events) if e.type == "message.started" and e.message_id == msg_ids[1])

    assert idx_first_msg_done < idx_tool < idx_compacting < idx_compacted < idx_second_start

    # History: PROGRESS and FINISH in context.history
    assistant_msgs = [msg for role, msg in run.context.history if role == "assistant"]
    assert "I found the relevant files." in assistant_msgs
    assert "Fixed and tested." in assistant_msgs
    # Iteration incremented and checkpoint set
    assert run.iteration > 0
    assert run.checkpoint is not None


def test_separate_max_agent_iterations_from_max_steps():
    assert MAX_AGENT_ITERATIONS == 20
    ctx = ExecutionContext(user_input="x")
    # Internal max_steps default is 10, distinct from outer loop max
    assert ctx.max_steps != MAX_AGENT_ITERATIONS


def test_max_agent_iterations_triggers_failed():
    run = _make_run()
    coord = ExecutionCoordinator()

    def fake_continue(ctx, r, emit):
        # Never finishing — emit nothing, return CONTINUE
        return AgentAction(type=AgentActionType.CONTINUE)

    coord._run_react = fake_continue  # type: ignore
    coord._run_graph = fake_continue  # type: ignore

    # Make should_compact always None to isolate iteration limit
    from novi.runtime.context_manager import ContextManager

    orig = ContextManager.should_compact
    ContextManager.should_compact = lambda self, ctx: None  # type: ignore
    try:
        events = []
        # Speed up: set iteration close to max
        run.iteration = MAX_AGENT_ITERATIONS - 1
        coord.execute(run, lambda e: events.append(e))
        assert run.status == AgentRunStatus.FAILED
        terminals = [e for e in events if e.type in ("run.completed", "run.failed", "run.cancelled")]
        assert len(terminals) == 1
        assert terminals[0].type == "run.failed"
        assert "Max agent iterations exceeded" in (terminals[0].error or "")
    finally:
        ContextManager.should_compact = orig  # type: ignore


def test_agent_cancelled_maps_to_cancelled():
    run = _make_run()
    coord = ExecutionCoordinator()

    def fake_cancel(ctx, r, emit):
        raise AgentCancelled("user stopped")

    coord._run_react = fake_cancel  # type: ignore
    coord._run_graph = fake_cancel  # type: ignore

    events = []
    coord.execute(run, lambda e: events.append(e))

    assert run.status == AgentRunStatus.CANCELLED
    assert run.finished
    assert any(e.type == "run.cancelled" for e in events)
    assert len([e for e in events if e.type in ("run.completed", "run.failed", "run.cancelled")]) == 1


def test_expected_error_maps_to_failed():
    run = _make_run()
    coord = ExecutionCoordinator()

    def fake_expected(ctx, r, emit):
        raise ExpectedExecutionError("permission denied")

    coord._run_react = fake_expected  # type: ignore
    coord._run_graph = fake_expected  # type: ignore

    events = []
    coord.execute(run, lambda e: events.append(e))

    assert run.status == AgentRunStatus.FAILED
    assert any(e.type == "run.failed" and "permission denied" in (e.error or "") for e in events)
    assert len([e for e in events if e.type in ("run.completed", "run.failed", "run.cancelled")]) == 1


def test_unexpected_exception_logs_and_reraises():
    run = _make_run()
    coord = ExecutionCoordinator()

    def fake_unexpected(ctx, r, emit):
        raise RuntimeError("boom")

    coord._run_react = fake_unexpected  # type: ignore
    coord._run_graph = fake_unexpected  # type: ignore

    events = []
    try:
        coord.execute(run, lambda e: events.append(e))
        assert False, "should have raised"
    except RuntimeError as e:
        assert str(e) == "boom"

    assert run.status == AgentRunStatus.FAILED
    assert any(e.type == "run.failed" for e in events)
    assert len([e for e in events if e.type in ("run.completed", "run.failed", "run.cancelled")]) == 1


def test_execute_does_not_reemit_message_events():
    """Coordinator must NOT re-emit message.* — inner layer already did."""
    run = _make_run()
    coord = ExecutionCoordinator()

    def fake_progress(ctx, r, emit):
        # Inner emits exactly one message cycle for PROGRESS
        mid = f"msg-{uuid.uuid4().hex[:8]}"
        emit(AgentEvent(type="message.started", run_id=r.id, conversation_id=r.conversation_id, message_id=mid))
        emit(AgentEvent(type="message.delta", run_id=r.id, conversation_id=r.conversation_id, message_id=mid, message="progress "))
        emit(AgentEvent(type="message.completed", run_id=r.id, conversation_id=r.conversation_id, message_id=mid, message="progress"))
        return AgentAction(type=AgentActionType.PROGRESS, message="progress")

    def fake_finish(ctx, r, emit):
        mid = f"msg-{uuid.uuid4().hex[:8]}"
        emit(AgentEvent(type="message.started", run_id=r.id, conversation_id=r.conversation_id, message_id=mid))
        emit(AgentEvent(type="message.delta", run_id=r.id, conversation_id=r.conversation_id, message_id=mid, message="done "))
        emit(AgentEvent(type="message.completed", run_id=r.id, conversation_id=r.conversation_id, message_id=mid, message="done"))
        return AgentAction(type=AgentActionType.FINISH, message="done")

    seq = [fake_progress, fake_finish]
    idx = {"i": 0}

    def dispatch(ctx, r, emit):
        fn = seq[idx["i"]]
        idx["i"] += 1
        return fn(ctx, r, emit)

    coord._run_react = dispatch  # type: ignore
    coord._run_graph = dispatch  # type: ignore

    from novi.runtime.context_manager import ContextManager

    orig = ContextManager.should_compact
    ContextManager.should_compact = lambda self, ctx: None  # type: ignore
    try:
        events = []
        coord.execute(run, lambda e: events.append(e))
        # Two message cycles: PROGRESS and FINISH — exactly 2 completed, not 4
        assert sum(1 for e in events if e.type == "message.completed") == 2
        # And message_ids stable within each, distinct across messages
        mids = [e.message_id for e in events if e.type == "message.started"]
        assert len(mids) == 2 and mids[0] != mids[1]
        # No extra message.* injected by coordinator
        for e in events:
            if e.type.startswith("message."):
                assert e.message_id is not None
    finally:
        ContextManager.should_compact = orig  # type: ignore


def test_token_usage_nullable():
    run = _make_run()
    assert run.token_usage is None
    run.token_usage = 42
    assert run.token_usage == 42
    # Execute should not fail when token_usage is None
    coord = ExecutionCoordinator()

    def fake(ctx, r, emit):
        return AgentAction(type=AgentActionType.FINISH, message="done")

    coord._run_react = fake  # type: ignore
    coord._run_graph = fake  # type: ignore
    from novi.runtime.context_manager import ContextManager

    orig = ContextManager.should_compact
    ContextManager.should_compact = lambda self, ctx: None  # type: ignore
    try:
        events = []
        run2 = _make_run()
        assert run2.token_usage is None
        coord.execute(run2, lambda e: events.append(e))
        # Still None if no provider supplied
        assert run2.token_usage is None or isinstance(run2.token_usage, int)
    finally:
        ContextManager.should_compact = orig  # type: ignore
