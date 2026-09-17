"""Task 8: Deterministic Integration Test — Architectural Contract.

Single deterministic test exercising PROGRESS → tools → CONTINUE → compaction
→ tools → PROGRESS → tools → FINISH and verifying ordering, terminal, history,
compaction preservation, message_id stability, and ephemeral status isolation.

Also explicit tests for message_id stability and ephemeral history/checkpoint.
"""

import uuid

import pytest

from novi.runtime.agent_run import AgentRun, AgentRunStatus
from novi.runtime.agent_action import AgentActionType
from novi.runtime.execution_context import ExecutionContext
from novi.runtime.agent_events import AgentEvent
from tests.helpers.fake_agentrun import make_fake_coordinator_with_actions


def _make_run(goal="fix bug", conv="conv-1"):
    ctx = ExecutionContext(user_input=goal)
    ctx.conversation_id = conv
    # Ensure trace exists for token paths (not needed here but mirrors other tests)
    try:
        from novi.runtime.trace import ExecutionTrace
        ctx.trace = ExecutionTrace(user_input=goal)
    except Exception:
        pass
    run = AgentRun(id=f"run-{uuid.uuid4().hex[:8]}", conversation_id=conv, goal=goal, status=AgentRunStatus.RUNNING, context=ctx)
    return run


def test_agentrun_multi_message_with_compaction_ordering():
    """Deterministic fake-model flow: PROGRESS → tools → CONTINUE → compaction → tools → PROGRESS → tools → FINISH"""
    ctx_run = _make_run(goal="fix bug", conv="conv-1")
    # Preserve original run id for compaction preservation check
    orig_id = ctx_run.id
    orig_iteration = ctx_run.iteration

    fake_actions = [
        ("progress", "I found the relevant files."),
        ("continue", None),
        ("progress", "Root cause identified."),
        ("finish", "Fixed and tested."),
    ]
    events: list[AgentEvent] = []
    coord = make_fake_coordinator_with_actions(fake_actions, force_compaction_on_cycle=1)
    coord.execute(ctx_run, lambda e: events.append(e))

    # 1. Terminal invariant: exactly one terminal, last event is terminal
    terminals = [e for e in events if e.type in ("run.completed", "run.failed", "run.cancelled")]
    assert len(terminals) == 1, f"expected exactly one terminal, got {terminals}"
    assert events[-1].type == "run.completed"
    assert ctx_run.status == AgentRunStatus.COMPLETED
    assert ctx_run.finished

    # 2. Message lifecycle ordering
    assert events[0].type == "run.started", f"first event must be run.started, got {events[0].type}"
    # Exactly one run.started
    assert sum(1 for e in events if e.type == "run.started") == 1

    # First message block: started/delta/completed share same message_id, stable
    m1_start = next(i for i, e in enumerate(events) if e.type == "message.started")
    m1_mid = events[m1_start].message_id
    assert m1_mid is not None
    m1_deltas = [e for e in events if e.type == "message.delta" and e.message_id == m1_mid]
    assert len(m1_deltas) >= 1, "expected at least one delta for first message"
    m1_end_idx = next(i for i, e in enumerate(events) if e.type == "message.completed" and e.message_id == m1_mid)
    assert m1_start < m1_end_idx
    assert all(e.message_id == m1_mid for e in m1_deltas)
    # All deltas of m1 share id, and completed shares id
    assert events[m1_end_idx].message_id == m1_mid

    # Tool activity after first message, before second
    tool_idx = next((i for i, e in enumerate(events) if e.type in ("tool.started", "tool.completed")), None)
    assert tool_idx is not None, "expected at least one tool event"
    assert m1_end_idx < tool_idx, "first message lifecycle must be before first tool activity"

    # Compaction between cycles when should_compact forced on cycle 1
    compact_idx = next(i for i, e in enumerate(events) if e.type == "context.compacting")
    compacted_idx = next(i for i, e in enumerate(events) if e.type == "context.compacted")
    assert compact_idx < compacted_idx, "compacting must precede compacted"
    assert tool_idx < compact_idx, "compaction must be after first tool batch"
    # Also ensure at least one tool after compaction? Our helper emits tools after compaction too
    # But main invariant: compaction before second message
    # Second message has different message_id
    m2_start = next(i for i, e in enumerate(events) if e.type == "message.started" and e.message_id != m1_mid)
    assert events[m2_start].message_id != m1_mid, "second message must have distinct message_id"
    m2_mid = events[m2_start].message_id
    m2_end_idx = next(i for i, e in enumerate(events) if e.type == "message.completed" and e.message_id == m2_mid)
    assert compacted_idx < m2_start < m2_end_idx, "second message must be after compaction"

    # Final message before run.completed
    final_end_idx = max(i for i, e in enumerate(events) if e.type == "message.completed")
    run_completed_idx = next(i for i, e in enumerate(events) if e.type == "run.completed")
    assert final_end_idx < run_completed_idx, "final message.completed must be before run.completed"
    # Exactly 3 message lifecycles: progress, progress, finish (continue has no message)
    assert sum(1 for e in events if e.type == "message.started") == 3
    assert sum(1 for e in events if e.type == "message.completed") == 3
    # Distinct ids across messages, stable within each
    mids = [e.message_id for e in events if e.type == "message.started"]
    assert len(set(mids)) == 3, f"expected 3 distinct message_ids, got {mids}"
    for mid in mids:
        deltas_for_mid = [e for e in events if e.type == "message.delta" and e.message_id == mid]
        assert len(deltas_for_mid) >= 1
        assert all(d.message_id == mid for d in deltas_for_mid)
        comp_for_mid = next(e for e in events if e.type == "message.completed" and e.message_id == mid)
        assert comp_for_mid.message_id == mid

    # 3. History invariants
    history = ctx_run.context.history
    # Ephemeral status never in history
    assert all("Still working" not in msg for _, msg in history), "ephemeral status must not be in history"
    # Progress and final in history
    assistant_msgs = [msg for role, msg in history if role == "assistant"]
    assert "I found the relevant files." in assistant_msgs, f"PROGRESS missing in history {assistant_msgs}"
    assert "Root cause identified." in assistant_msgs, f"second PROGRESS missing {assistant_msgs}"
    assert "Fixed and tested." in assistant_msgs, f"FINISH missing {assistant_msgs}"
    # CONTINUE has no message, so should not add None to history
    assert len(assistant_msgs) == 3

    # 4. Compaction preserved run (does not reset id/iteration beyond increment, checkpoint set)
    assert ctx_run.id == orig_id, "compaction must not reset run.id"
    assert ctx_run.id == events[0].run_id, "run.id must match events run_id"
    assert ctx_run.iteration > orig_iteration, "iteration should increment beyond compaction"
    # iteration increments once per non-terminal cycle before FINISH; with 4 actions, expect 3 increments
    # But at minimum >0 and checkpoint set
    assert ctx_run.checkpoint is not None, "checkpoint must be set after compaction"
    # checkpoint stable should not contain ephemeral
    stable_str = str(ctx_run.checkpoint.stable)
    assert "Still working" not in stable_str
    assert "Compacting" not in stable_str


def test_message_id_stable_within_one_message():
    """Explicit: started/delta/completed share id; separate messages differ."""
    run = _make_run(goal="hello", conv="conv-mid")
    fake_actions = [
        ("progress", "first message here"),
        ("progress", "second message here"),
        ("finish", "final answer"),
    ]
    events: list[AgentEvent] = []
    coord = make_fake_coordinator_with_actions(fake_actions, force_compaction_on_cycle=None)
    coord.execute(run, lambda e: events.append(e))

    # Collect message lifecycles
    started = [e for e in events if e.type == "message.started"]
    completed = [e for e in events if e.type == "message.completed"]
    deltas = [e for e in events if e.type == "message.delta"]

    assert len(started) == 3, f"expected 3 messages, got {started}"
    assert len(completed) == 3
    # Each started has unique id
    mids = [e.message_id for e in started]
    assert len(set(mids)) == 3
    assert None not in mids

    for mid in mids:
        # All deltas for this mid share same mid
        d_for_mid = [d for d in deltas if d.message_id == mid]
        assert len(d_for_mid) >= 1, f"expected deltas for {mid}"
        assert all(d.message_id == mid for d in d_for_mid)
        # Completed shares same mid
        comp = next(c for c in completed if c.message_id == mid)
        assert comp.message_id == mid
        # Started matches too
        start = next(s for s in started if s.message_id == mid)
        assert start.message_id == mid

    # Cross-message: ensure deltas of different mids do not intermix (all stable)
    # At least check that first message's deltas are all before second message's started? Not strictly required but stable
    # Verify distinct
    assert mids[0] != mids[1] != mids[2]
    assert mids[0] != mids[2]


def test_ephemeral_status_never_in_history_or_checkpoint():
    """Ephemeral status events must never enter history or checkpoint stable."""
    run = _make_run(goal="ephemeral test", conv="conv-ephem")
    run.context.history = []  # start empty
    fake_actions = [
        ("progress", "Progress one."),
        ("finish", "Done."),
    ]
    events: list[AgentEvent] = []
    # Force compaction on first cycle to ensure checkpoint path also filtered
    coord = make_fake_coordinator_with_actions(fake_actions, force_compaction_on_cycle=0)
    coord.execute(run, lambda e: events.append(e))

    # Status events were emitted (ephemeral) but must not be in history
    status_events = [e for e in events if e.type == "status"]
    assert len(status_events) >= 1, "expected at least one ephemeral status event"
    for se in status_events:
        assert se.message == "Still working" or "Still working" in (se.message or "")

    history_text = " ".join(msg for _, msg in run.context.history)
    assert "Still working" not in history_text
    assistant_msgs = [msg for role, msg in run.context.history if role == "assistant"]
    assert "Progress one." in assistant_msgs
    assert "Done." in assistant_msgs
    assert all("Still working" not in m for m in assistant_msgs)
    assert all("Compacting context" not in m for m in assistant_msgs)

    # Checkpoint stable must exist and not contain ephemeral
    assert run.checkpoint is not None
    stable = run.checkpoint.stable
    stable_str = str(stable)
    assert "Still working" not in stable_str
    assert "Compacting" not in stable_str
    # Also ensure status not in context.compacting detail
    # Compaction events exist but history still clean
    assert any(e.type == "context.compacting" for e in events)
    assert any(e.type == "context.compacted" for e in events)
