"""Task 8 helper: deterministic fake coordinator for lifecycle contract.

Provides make_fake_coordinator_with_actions that stubs _run_react/_run_graph
to return sequenced AgentActions and emits tool/message events with correct
message_id, and monkeypatches ContextManager.should_compact to force compaction
on specified cycle.

Reused by test_agentrun_lifecycle.py; also usable by earlier terminal/coordinator
tests if needed (kept separate from execution_test_helpers to avoid coupling).
"""

from __future__ import annotations

import uuid
from typing import List, Tuple, Union

from novi.runtime.agent_action import AgentAction, AgentActionType
from novi.runtime.agent_events import AgentEvent
from novi.services.execution import ExecutionCoordinator


def make_fake_coordinator_with_actions(
    fake_actions: List[Union[Tuple[str, str | None], AgentAction]],
    force_compaction_on_cycle: int | None = None,
):
    """Create a fake coordinator that drives a deterministic action sequence.

    Args:
        fake_actions: list of ("progress"|"continue"|"finish", message) or AgentAction.
            Example: [("progress","msg1"), ("continue",None), ("progress","msg2"), ("finish","done")]
        force_compaction_on_cycle: 0-indexed iteration after which ContextManager.should_compact
            should return "compact". ``None`` means never compact. ``0`` means compact after
            first action, ``1`` after second, etc. The helper patches
            ContextManager.should_compact / compact_history / checkpoint_stable globally
            to deterministic stubs (mirroring prior tasks' FakeOrchestrator pattern).

    Returns:
        ExecutionCoordinator with _run_react/_run_graph stubbed.

    The stub emits:
        - For PROGRESS/FINISH with message: message.started, one or more message.delta
          (word-split, stable message_id), message.completed, then tool.started/tool.completed
          plus an ephemeral status event ("Still working") that must NOT enter history.
        - For CONTINUE: tool.started/tool.completed + ephemeral status (no message lifecycle).

    Message_id is stable within one message and distinct across messages (uuid).
    Tool events are always emitted so ordering invariants can be verified.

    Monkeypatching: patches ContextManager.should_compact to return "compact" exactly once
    when internal call count -1 == force_compaction_on_cycle (so force=1 => compact after
    the second action). Stub implementations of compact_history / checkpoint_stable preserve
    run identity, increment run.iteration via coordinator, and set run.checkpoint without
    injecting ephemeral status into history or checkpoint.stable.
    """
    from novi.runtime.context_manager import ContextManager
    from novi.jobs.job import Checkpoint

    # Preserve originals for potential cleanup (attached to coordinator)
    orig_should = ContextManager.should_compact
    orig_compact = ContextManager.compact_history
    orig_checkpoint = ContextManager.checkpoint_stable

    # Clone actions queue (allow mutation via pop)
    queue: list = list(fake_actions)

    # Per-coordinator compaction call counter
    call_state = {"n": 0}

    def fake_should(self, ctx):
        call_state["n"] += 1
        if force_compaction_on_cycle is not None and (call_state["n"] - 1) == force_compaction_on_cycle:
            return "compact"
        return None

    def fake_compact(self, ctx):
        # Mark compacted; preserve StableState without ephemeral status
        try:
            ctx.metadata["compacted"] = True
            from novi.common.execution_state import StableState

            stable = StableState.from_context(ctx)
            ctx.metadata["stable_state"] = stable.to_dict()
            ctx.metadata["stable_state_text"] = stable.to_text()
        except Exception:
            try:
                ctx.metadata["compacted"] = True
            except Exception:
                pass
        # Never append ephemeral text to history

    def fake_checkpoint(self, ctx):
        try:
            from novi.common.execution_state import StableState

            stable = StableState.from_context(ctx)
            stable_dict = stable.to_dict()
        except Exception:
            stable_dict = {}
        # Ensure ephemeral not in stable
        return Checkpoint(job_id="j1", task_id="t1", plan_id="", step=1, completed_steps=[], stable=stable_dict)

    # Apply monkeypatches globally (Task 8 spec: helper monkeypatches ContextManager)
    ContextManager.should_compact = fake_should  # type: ignore[method-assign, assignment]
    ContextManager.compact_history = fake_compact  # type: ignore[method-assign, assignment]
    ContextManager.checkpoint_stable = fake_checkpoint  # type: ignore[method-assign, assignment]

    coord = ExecutionCoordinator()

    def _fake_run(ctx, run, emit):  # type: ignore[no-untyped-def]
        # Pop next action; if empty, finish
        if queue:
            nxt = queue.pop(0)
        else:
            nxt = ("finish", "done")

        if isinstance(nxt, AgentAction):
            atype = nxt.type
            amsg = nxt.message
        elif isinstance(nxt, tuple):
            raw_t = str(nxt[0]).lower() if nxt[0] else "continue"
            amsg = nxt[1] if len(nxt) > 1 else None
            if raw_t == "progress":
                atype = AgentActionType.PROGRESS
            elif raw_t == "finish":
                atype = AgentActionType.FINISH
            else:
                atype = AgentActionType.CONTINUE
        else:
            atype = AgentActionType.CONTINUE
            amsg = None

        # Emit events with correct message_id contract
        if atype in (AgentActionType.PROGRESS, AgentActionType.FINISH) and amsg:
            mid = f"msg-{uuid.uuid4().hex[:8]}"
            try:
                run.message_seq += 1  # type: ignore[attr-defined]
            except Exception:
                pass
            emit(AgentEvent(type="message.started", run_id=run.id, conversation_id=run.conversation_id, message_id=mid))
            # Chunk into word deltas (stable id)
            words = amsg.split()
            if words:
                for w in words:
                    emit(AgentEvent(type="message.delta", run_id=run.id, conversation_id=run.conversation_id, message_id=mid, message=w + " "))
            else:
                emit(AgentEvent(type="message.delta", run_id=run.id, conversation_id=run.conversation_id, message_id=mid, message=amsg))
            emit(AgentEvent(type="message.completed", run_id=run.id, conversation_id=run.conversation_id, message_id=mid, message=amsg))
            # Tool batch after message (so message lifecycle before tools)
            emit(AgentEvent(type="tool.started", run_id=run.id, conversation_id=run.conversation_id, tool="read_file", args={"path": "a.py"}, call_id=f"call-{uuid.uuid4().hex[:6]}"))
            emit(AgentEvent(type="tool.completed", run_id=run.id, conversation_id=run.conversation_id, tool="read_file", result="ok", call_id=f"call-{uuid.uuid4().hex[:6]}"))
            # Ephemeral status (must never enter history/checkpoint)
            emit(AgentEvent(type="status", run_id=run.id, conversation_id=run.conversation_id, message="Still working", phase="thinking", detail="Still working"))
        elif atype == AgentActionType.CONTINUE:
            emit(AgentEvent(type="tool.started", run_id=run.id, conversation_id=run.conversation_id, tool="search", args={"query": "test"}, call_id=f"call-{uuid.uuid4().hex[:6]}"))
            emit(AgentEvent(type="tool.completed", run_id=run.id, conversation_id=run.conversation_id, tool="search", result="results", call_id=f"call-{uuid.uuid4().hex[:6]}"))
            emit(AgentEvent(type="status", run_id=run.id, conversation_id=run.conversation_id, message="Still working", phase="thinking", detail="Still working"))
        else:
            # No message, no tools? still emit ephemeral to test filtering
            emit(AgentEvent(type="status", run_id=run.id, conversation_id=run.conversation_id, message="Still working", phase="thinking", detail="Still working"))

        return AgentAction(type=atype, message=amsg if atype != AgentActionType.CONTINUE else None)

    coord._run_react = _fake_run  # type: ignore[method-assign, assignment]
    coord._run_graph = _fake_run  # type: ignore[method-assign, assignment]

    # Attach originals for introspection/cleanup if caller wants
    coord._fake_orig_should = orig_should  # type: ignore[attr-defined]
    coord._fake_orig_compact = orig_compact  # type: ignore[attr-defined]
    coord._fake_orig_checkpoint = orig_checkpoint  # type: ignore[attr-defined]
    coord._fake_call_state = call_state  # type: ignore[attr-defined]

    return coord
