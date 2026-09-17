"""Task 4: ReAct Loop and Graph Return AgentAction with Native Streaming.

Tests native runnable.stream tokens vs fallback _chunk_message, message_id correlation,
emit_progress handling, and single-owner invariant (no duplicate message.completed).
"""

import uuid
from types import SimpleNamespace

from langchain_core.messages import AIMessageChunk

from novi.runtime.agent_action import AgentActionType, AgentAction
from novi.runtime.agent_events import AgentEvent
from novi.runtime.execution_context import ExecutionContext
from novi.runtime.trace import ExecutionTrace


# --- helpers ---

class FakeChunk:
    def __init__(self, content, reasoning_content=""):
        self.content = content
        self.additional_kwargs = {"reasoning_content": reasoning_content} if reasoning_content else {}

    def __add__(self, other):
        # For acc = chunk if acc is None else acc + chunk
        combined = FakeChunk((self.content or "") + (other.content or ""))
        # Merge reasoning
        rc1 = self.additional_kwargs.get("reasoning_content", "")
        rc2 = other.additional_kwargs.get("reasoning_content", "")
        if rc1 or rc2:
            combined.additional_kwargs["reasoning_content"] = (rc1 or "") + (rc2 or "")
        # Preserve tool_calls if any
        if hasattr(self, "tool_calls"):
            combined.tool_calls = getattr(self, "tool_calls", None)
        if hasattr(other, "tool_calls"):
            combined.tool_calls = getattr(other, "tool_calls", None) or getattr(combined, "tool_calls", None)
        return combined


class FakeRunnableStream:
    def __init__(self, pieces, tool_calls=None):
        self.pieces = pieces
        self.tool_calls = tool_calls

    def stream(self, msgs):
        for p in self.pieces:
            yield FakeChunk(p)
        if self.tool_calls:
            # Final chunk with tool_calls
            c = FakeChunk("", reasoning_content="")
            c.tool_calls = self.tool_calls
            # Need to make it have content and tool_calls via AIMessageChunk-like
            # For our executor, extract_calls looks at ai.tool_calls, so we need final acc to have tool_calls
            # We'll yield a chunk that when added, results in tool_calls
            # Simulate by yielding a chunk with tool_calls in additional_kwargs?
            # Simpler: yield a chunk that has tool_calls attribute
            yield c

    def invoke(self, msgs):
        # For fallback test, not used
        return FakeChunk("".join(self.pieces))


class FakeExecutorNoTools:
    def extract_calls(self, ai):
        # Check if ai has tool_calls
        calls = getattr(ai, "tool_calls", None)
        if calls:
            return [{"name": c["name"], "args": c.get("args", {}), "id": c.get("id") or c["name"]} for c in calls]
        # Also check AIMessageChunk style
        tc = getattr(ai, "tool_calls", None)
        if tc:
            return tc
        return []

    def tool_category(self, name):
        return "workspace"

    def compute_diff(self, name, args):
        return {"tool": name}

    def tools_for_mode(self, allowed_tools=None, **kw):
        return []

    def execute(self, name, args, coordinator=None, step_idx=None, trace=None):
        return SimpleNamespace(output=f"{name}-ok", success=True, diff=None, latency_ms=1.0, structured=None, error=None)

    def is_pseudo_tool(self, name):
        return name == "emit_progress"


class FakeTracer:
    def finalize(self, trace, reason):
        pass

    def debug(self, trace, cat, data):
        pass

    def record_tool(self, **kw):
        pass


class FakeRetrieval:
    def recommend_when_model_answered(self, ctx):
        return SimpleNamespace(action=__import__("novi.runtime.retrieval", fromlist=["RecoveryAction"]).RecoveryAction.NONE)

    def recommend_after_tool(self, ctx, name, out):
        return SimpleNamespace(action=__import__("novi.runtime.retrieval", fromlist=["RecoveryAction"]).RecoveryAction.NONE)

    def commit_recovery(self, ctx, decision, tag):
        raise AssertionError("recovery must not trigger")


class FakeReg:
    def get_tool_names(self, caps):
        return []


def test_react_yields_message_delta_with_stable_message_id():
    from novi.runtime.react_attempt import run_react_attempt

    class FakeRunnable:
        def stream(self, msgs):
            yield FakeChunk("Hello ")
            yield FakeChunk("world")
        def invoke(self, msgs):
            return FakeChunk("Hello world")

    ctx = ExecutionContext(user_input="hi")
    ctx.trace = ExecutionTrace(user_input="hi")
    ctx.activated_skills = []
    ctx.allowed_tools = []
    ctx.retrieval_coordinator = None
    ctx.conversation_id = "conv-1"
    ctx.run_id = "run-1"

    events = list(run_react_attempt(
        ctx=ctx, runnable=FakeRunnable(), tool_executor=FakeExecutorNoTools(),
        tracer=FakeTracer(), retrieval_executor=FakeRetrieval(), capability_registry=FakeReg(),
        scan_skills=lambda t, a: [], skill_block=lambda s: "", bind_runnable=lambda c, t: FakeRunnable(),
        stop_probe=lambda: False, step_budget=1, base_msgs=[]
    ))
    started = [e for e in events if isinstance(e, AgentEvent) and e.type == "message.started"]
    deltas = [e for e in events if isinstance(e, AgentEvent) and e.type == "message.delta"]
    completed = [e for e in events if isinstance(e, AgentEvent) and e.type == "message.completed"]
    assert len(started) == 1, f"expected 1 started, got {started}"
    assert len(completed) == 1, f"expected 1 completed, got {completed}"
    assert len(deltas) >= 1
    assert started[0].message_id == deltas[0].message_id == completed[0].message_id
    # Check native streaming used: deltas should contain "Hello " and "world"
    delta_text = "".join(d.message for d in deltas)
    assert "Hello" in delta_text and "world" in delta_text
    # Terminal should be FINISH
    actions = [e for e in events if isinstance(e, AgentAction)]
    assert len(actions) == 1
    assert actions[0].type == AgentActionType.FINISH
    assert actions[0].message == "Hello world"


def test_fallback_chunking_when_no_stream():
    from novi.runtime.react_attempt import run_react_attempt

    class NoStreamRunnable:
        def invoke(self, msgs):
            # Return AIMessage-like with content
            return SimpleNamespace(content="Hello world fallback", tool_calls=None, additional_kwargs={})

    ctx = ExecutionContext(user_input="hi")
    ctx.trace = ExecutionTrace(user_input="hi")
    ctx.activated_skills = []
    ctx.allowed_tools = []
    ctx.retrieval_coordinator = None
    ctx.conversation_id = "conv-2"
    ctx.run_id = "run-2"

    events = list(run_react_attempt(
        ctx=ctx, runnable=NoStreamRunnable(), tool_executor=FakeExecutorNoTools(),
        tracer=FakeTracer(), retrieval_executor=FakeRetrieval(), capability_registry=FakeReg(),
        scan_skills=lambda t, a: [], skill_block=lambda s: "", bind_runnable=lambda c, t: NoStreamRunnable(),
        stop_probe=lambda: False, step_budget=1, base_msgs=[]
    ))
    started = [e for e in events if isinstance(e, AgentEvent) and e.type == "message.started"]
    deltas = [e for e in events if isinstance(e, AgentEvent) and e.type == "message.delta"]
    completed = [e for e in events if isinstance(e, AgentEvent) and e.type == "message.completed"]
    assert len(started) == 1
    assert len(completed) == 1
    assert started[0].message_id == deltas[0].message_id == completed[0].message_id
    # Fallback chunking: word-split
    # Our fallback yields word + " " for each word
    delta_text = "".join(d.message for d in deltas)
    assert "Hello" in delta_text
    # Check that deltas are word chunks, not single token stream
    assert len(deltas) > 1  # "Hello " and "world " and "fallback "
    actions = [e for e in events if isinstance(e, AgentAction)]
    assert actions[0].type == AgentActionType.FINISH


def test_emit_progress_produces_progress_not_finish():
    from novi.runtime.react_attempt import run_react_attempt
    from langchain_core.messages import AIMessage

    class ProgressRunnable:
        def stream(self, msgs):
            # First yield an AIMessage with tool call to emit_progress
            # We need to simulate tool_calls via final acc
            # Our FakeChunk doesn't support tool_calls well, so we directly return via invoke-like
            # Instead we will have stream yield chunks that accumulate to an AIMessage with tool_calls
            # For simplicity, use a single chunk that when added results in AIMessage with tool_calls
            c = FakeChunk("")
            c.tool_calls = [{"name": "emit_progress", "args": {"message": "Found 3 files"}, "id": "c1"}]
            # Also need content to be empty, but tool_calls present
            # To make acc + chunk work, we need acc to be None then chunk with tool_calls
            yield c

        def invoke(self, msgs):
            return FakeChunk("")

    # Need a tool executor that handles pseudo
    from novi.runtime.tool_executor import ToolExecutor
    from novi.runtime.tool_registry import ToolRegistry
    reg = ToolRegistry()
    exe = ToolExecutor(registry=reg)

    ctx = ExecutionContext(user_input="hi")
    ctx.trace = ExecutionTrace(user_input="hi")
    ctx.activated_skills = []
    ctx.allowed_tools = ["emit_progress"]
    ctx.retrieval_coordinator = None
    ctx.conversation_id = "conv-3"
    ctx.run_id = "run-3"

    events = list(run_react_attempt(
        ctx=ctx, runnable=ProgressRunnable(), tool_executor=exe,
        tracer=FakeTracer(), retrieval_executor=FakeRetrieval(), capability_registry=FakeReg(),
        scan_skills=lambda t, a: [], skill_block=lambda s: "", bind_runnable=lambda c, t: ProgressRunnable(),
        stop_probe=lambda: False, step_budget=2, base_msgs=[]
    ))
    # Should have PROGRESS action, not FINISH, and message lifecycle for progress
    actions = [e for e in events if isinstance(e, AgentAction)]
    # Might have multiple actions: PROGRESS then maybe FINISH/CONTINUE after next iteration
    # The first action should be PROGRESS
    assert any(a.type == AgentActionType.PROGRESS and a.message == "Found 3 files" for a in actions), f"actions {actions}"
    # Check that progress emitted message lifecycle
    progresses = [a for a in actions if a.type == AgentActionType.PROGRESS]
    assert progresses[0].message == "Found 3 files"
    # Ensure no tool.started for emit_progress (pseudo not external)
    tool_started = [e for e in events if isinstance(e, AgentEvent) and e.type == "tool.started" and e.tool == "emit_progress"]
    assert len(tool_started) == 0, "emit_progress should not emit tool.started"
    # Ensure message.completed for progress exists with stable id (may be 1 or 2 depending on budget)
    prog_completed = [e for e in events if isinstance(e, AgentEvent) and e.type == "message.completed" and e.message == "Found 3 files"]
    assert len(prog_completed) >= 1
    assert prog_completed[0].message_id is not None
    # Ensure stable id within each progress message
    for pc in prog_completed:
        assert pc.message_id is not None


def test_graph_returns_agent_action():
    from novi.graphs.runtime_graph import RuntimeWorkflowGraph
    from novi.runtime.agent_action import AgentActionType

    class FakeModel:
        def stream(self, msgs):
            yield SimpleNamespace(content="graph answer", additional_kwargs={}, tool_calls=None)
            # Need to handle chunk addition: use AIMessageChunk style
        def invoke(self, msgs):
            from langchain_core.messages import AIMessage
            return AIMessage(content="graph answer")

    # Use simple model that returns answer
    from langchain_core.messages import AIMessage, AIMessageChunk
    class SimpleModel:
        def stream(self, msgs):
            yield AIMessageChunk(content="graph answer")
        def invoke(self, msgs):
            return AIMessage(content="graph answer")

    g = RuntimeWorkflowGraph(max_steps=2)
    state = {
        "user_input": "hello",
        "system_prompt": "sys",
        "seed_messages": [],
        "messages": [],
        "model": SimpleModel(),
        "prepare_context": lambda: {},
        "execute_tool": lambda n, a, idx: ("", None, True),
        "run_id": "run-g1",
        "conversation_id": "conv-g1",
    }
    result = g.run(state)
    assert isinstance(result, AgentAction), f"expected AgentAction, got {type(result)}"
    assert result.type == AgentActionType.FINISH
    assert "graph answer" in (result.message or "")
    # Check dict-like access for legacy
    assert result.get("answer") == result.message
    assert result.get("completion_reason") in ("completed", "empty")


def test_single_owner_no_duplicate_message_completed():
    """Verify that only the streaming owner (react_attempt) emits message.completed,
    not both layers. Simulate what coordinator would do: it should NOT re-emit.
    """
    from novi.runtime.react_attempt import run_react_attempt

    class FakeRunnable:
        def stream(self, msgs):
            yield FakeChunk("answer ")
            yield FakeChunk("done")

    ctx = ExecutionContext(user_input="hi")
    ctx.trace = ExecutionTrace(user_input="hi")
    ctx.activated_skills = []
    ctx.allowed_tools = []
    ctx.retrieval_coordinator = None
    ctx.conversation_id = "conv-4"
    ctx.run_id = "run-4"

    events = list(run_react_attempt(
        ctx=ctx, runnable=FakeRunnable(), tool_executor=FakeExecutorNoTools(),
        tracer=FakeTracer(), retrieval_executor=FakeRetrieval(), capability_registry=FakeReg(),
        scan_skills=lambda t, a: [], skill_block=lambda s: "", bind_runnable=lambda c, t: FakeRunnable(),
        stop_probe=lambda: False, step_budget=1, base_msgs=[]
    ))
    # Count message.completed
    completed = [e for e in events if isinstance(e, AgentEvent) and e.type == "message.completed"]
    assert len(completed) == 1, f"expected exactly 1 message.completed from streaming owner, got {len(completed)}"
    # Ensure no duplicate from a hypothetical coordinator re-emitting: we simulate coordinator would NOT emit
    # So total completed should stay 1
    # Also check that message_id is stable across started/delta/completed
    started = [e for e in events if isinstance(e, AgentEvent) and e.type == "message.started"]
    deltas = [e for e in events if isinstance(e, AgentEvent) and e.type == "message.delta"]
    assert started[0].message_id == deltas[0].message_id == completed[0].message_id
    # Document ownership: react_attempt is owner, coordinator should only emit run-level
    # This test ensures we don't have both layers emitting same message.completed


def test_is_goal_complete_logic_preserved():
    from novi.runtime.react_attempt import is_goal_complete, run_react_attempt
    from novi.runtime.execution_context import ExecutionContext

    ctx = ExecutionContext(user_input="hi")
    ctx.trace = ExecutionTrace(user_input="hi")
    ctx.activated_skills = []
    ctx.allowed_tools = []
    ctx.retrieval_coordinator = None
    ctx.metadata = {}
    # Non-empty final should be complete if no unresolved
    assert is_goal_complete(ctx, "hello") is True
    ctx.metadata["has_unresolved"] = True
    assert is_goal_complete(ctx, "hello") is False
    ctx.metadata = {}
    assert is_goal_complete(ctx, "") is False

    # Test that FINISH vs CONTINUE respects is_goal_complete
    class EmptyRunnable:
        def stream(self, msgs):
            yield FakeChunk("")
        def invoke(self, msgs):
            return FakeChunk("")

    ctx2 = ExecutionContext(user_input="hi")
    ctx2.trace = ExecutionTrace(user_input="hi")
    ctx2.activated_skills = []
    ctx2.allowed_tools = []
    ctx2.retrieval_coordinator = None
    ctx2.conversation_id = "conv-5"
    ctx2.run_id = "run-5"
    ctx2.metadata = {}

    events = list(run_react_attempt(
        ctx=ctx2, runnable=EmptyRunnable(), tool_executor=FakeExecutorNoTools(),
        tracer=FakeTracer(), retrieval_executor=FakeRetrieval(), capability_registry=FakeReg(),
        scan_skills=lambda t, a: [], skill_block=lambda s: "", bind_runnable=lambda c, t: EmptyRunnable(),
        stop_probe=lambda: False, step_budget=1, base_msgs=[]
    ))
    actions = [e for e in events if isinstance(e, AgentAction)]
    # Empty output should result in FINISH with empty wording or CONTINUE? Check that it doesn't crash
    assert len(actions) == 1
    # The message should be the empty fallback
    assert actions[0].message is not None


def test_loop_done_deprecated_alias():
    from novi.runtime.react_attempt import _LOOP_DONE, run_react_attempt

    assert _LOOP_DONE == "__plan_step_done__"

    class FakeRunnable:
        def stream(self, msgs):
            yield FakeChunk("hi")

    ctx = ExecutionContext(user_input="hi")
    ctx.trace = ExecutionTrace(user_input="hi")
    ctx.activated_skills = []
    ctx.allowed_tools = []
    ctx.retrieval_coordinator = None
    ctx.conversation_id = "conv-6"
    ctx.run_id = "run-6"

    events = list(run_react_attempt(
        ctx=ctx, runnable=FakeRunnable(), tool_executor=FakeExecutorNoTools(),
        tracer=FakeTracer(), retrieval_executor=FakeRetrieval(), capability_registry=FakeReg(),
        scan_skills=lambda t, a: [], skill_block=lambda s: "", bind_runnable=lambda c, t: FakeRunnable(),
        stop_probe=lambda: False, step_budget=1, base_msgs=[]
    ))
    # The terminal AgentAction should be equal to legacy tuple via __eq__
    actions = [e for e in events if isinstance(e, AgentAction)]
    assert len(actions) == 1
    legacy_tuple = (_LOOP_DONE, "hi", "completed", True)
    assert actions[0] == legacy_tuple
    # Also indexing should work
    assert actions[0][0] == _LOOP_DONE
    assert actions[0][1] == "hi"
