"""Task 7: token accounting — prefer provider usage, nullable."""

import uuid
from types import SimpleNamespace
from novi.runtime.execution_context import ExecutionContext
from novi.runtime.agent_run import AgentRun, AgentRunStatus
from novi.runtime.agent_action import AgentAction, AgentActionType
from novi.runtime.agent_events import AgentEvent
from novi.runtime.trace import ExecutionTrace
from novi.services.execution import ExecutionCoordinator


def _make_run():
    ctx = ExecutionContext(user_input="hi")
    ctx.conversation_id = "conv-1"
    ctx.trace = ExecutionTrace(user_input="hi")
    run = AgentRun(id=f"run-{uuid.uuid4().hex[:8]}", conversation_id="conv-1", goal="hi", status=AgentRunStatus.RUNNING, context=ctx)
    return run


def test_token_usage_uses_provider_when_available(monkeypatch):
    """Provider usage_metadata with total_tokens propagates to run and ctx."""
    from novi.runtime.react_attempt import run_react_attempt
    from novi.runtime.trace import ExecutionTrace

    class FakeChunk:
        def __init__(self, content, usage=None, response_usage=None):
            self.content = content
            self.additional_kwargs = {}
            if usage is not None:
                self.usage_metadata = usage
            if response_usage is not None:
                self.response_metadata = {"usage": response_usage}

        def __add__(self, other):
            c = FakeChunk((self.content or "") + (other.content or ""))
            # propagate usage from the later chunk if present
            if hasattr(other, "usage_metadata"):
                c.usage_metadata = getattr(other, "usage_metadata")
            if hasattr(other, "response_metadata"):
                c.response_metadata = getattr(other, "response_metadata")
            return c

    class FakeRunnable:
        def stream(self, msgs):
            # first token without usage, second carries usage
            yield FakeChunk("Hello ")
            yield FakeChunk("world", usage={"total_tokens": 123})

        def invoke(self, msgs):
            return FakeChunk("Hello world", usage={"total_tokens": 123})

    class FakeExecutor:
        def extract_calls(self, ai): return []
        def compute_diff(self, *a, **kw): return None
        def tools_for_mode(self, **kw): return []
        def execute(self, *a, **kw): return SimpleNamespace(output="ok", success=True, diff=None, structured=None)

    class FakeTracer:
        def finalize(self, *a, **kw): pass
        def debug(self, *a, **kw): pass
        def record_tool(self, **kw): pass

    class FakeRetrieval:
        def recommend_when_model_answered(self, ctx):
            from novi.runtime.retrieval import RecoveryAction
            return SimpleNamespace(action=RecoveryAction.NONE)
        def recommend_after_tool(self, *a, **kw):
            from novi.runtime.retrieval import RecoveryAction
            return SimpleNamespace(action=RecoveryAction.NONE)
        def commit_recovery(self, *a, **kw): raise AssertionError

    class FakeReg:
        def get_tool_names(self, caps): return []

    ctx = ExecutionContext(user_input="hi")
    ctx.trace = ExecutionTrace(user_input="hi")
    ctx.activated_skills = []
    ctx.allowed_tools = []
    ctx.retrieval_coordinator = None
    ctx.conversation_id = "conv-1"
    ctx.run_id = "run-1"

    events = list(run_react_attempt(
        ctx=ctx, runnable=FakeRunnable(), tool_executor=FakeExecutor(),
        tracer=FakeTracer(), retrieval_executor=FakeRetrieval(), capability_registry=FakeReg(),
        scan_skills=lambda t, a: [], skill_block=lambda s: "", bind_runnable=lambda c, t: FakeRunnable(),
        stop_probe=lambda: False, step_budget=1, base_msgs=[]
    ))
    # provider usage should have been applied to ctx
    assert getattr(ctx, "token_usage", None) == 123
    assert ctx.metadata.get("token_usage") == 123

    # Now via coordinator, run.token_usage should be propagated (prefer provider)
    run = _make_run()
    # inject the same ctx into run
    run.context = ctx
    # Ensure trace steps sum is different to prove preference
    ctx.trace.steps = []
    from novi.runtime.trace import StepTrace
    # if provider is preferred, trace sum should be ignored
    ctx.trace.steps.append(StepTrace(step=0, tokens_generated=999))

    coord = ExecutionCoordinator()

    def fake(ctx2, r, emit):
        # Already has ctx.token_usage=123; just return FINISH
        return AgentAction(type=AgentActionType.FINISH, message="done")

    from novi.runtime.context_manager import ContextManager
    orig = ContextManager.should_compact
    ContextManager.should_compact = lambda self, ctx: None  # type: ignore
    try:
        coord._run_react = fake  # type: ignore
        coord._run_graph = fake  # type: ignore
        events2 = []
        coord.execute(run, lambda e: events2.append(e))
        assert run.token_usage == 123, f"expected provider 123, got {run.token_usage}"
    finally:
        ContextManager.should_compact = orig  # type: ignore


def test_token_usage_via_response_metadata(monkeypatch):
    """response_metadata usage also propagates."""
    from novi.runtime.react_attempt import run_react_attempt
    from novi.runtime.trace import ExecutionTrace

    class FakeChunk:
        def __init__(self, content, resp=None):
            self.content = content
            self.additional_kwargs = {}
            if resp is not None:
                self.response_metadata = resp
        def __add__(self, other):
            c = FakeChunk((self.content or "") + (other.content or ""))
            if hasattr(other, "response_metadata"):
                c.response_metadata = getattr(other, "response_metadata")
            return c

    class FakeRunnable:
        def stream(self, msgs):
            yield FakeChunk("Hi ", resp={"usage": {"total_tokens": 77}})

    class FakeExecutor:
        def extract_calls(self, ai): return []
        def compute_diff(self, *a, **kw): return None
        def tools_for_mode(self, **kw): return []
        def execute(self, *a, **kw): return SimpleNamespace(output="ok", success=True, diff=None, structured=None)
    class FakeTracer:
        def finalize(self, *a, **kw): pass
        def debug(self, *a, **kw): pass
        def record_tool(self, **kw): pass
    class FakeRetrieval:
        def recommend_when_model_answered(self, ctx):
            from novi.runtime.retrieval import RecoveryAction
            return SimpleNamespace(action=RecoveryAction.NONE)
        def recommend_after_tool(self, *a, **kw):
            from novi.runtime.retrieval import RecoveryAction
            return SimpleNamespace(action=RecoveryAction.NONE)
        def commit_recovery(self, *a, **kw): raise AssertionError
    class FakeReg:
        def get_tool_names(self, caps): return []

    ctx = ExecutionContext(user_input="hi")
    ctx.trace = ExecutionTrace(user_input="hi")
    ctx.activated_skills = []
    ctx.allowed_tools = []
    ctx.retrieval_coordinator = None
    ctx.conversation_id = "conv-2"
    ctx.run_id = "run-2"

    events = list(run_react_attempt(
        ctx=ctx, runnable=FakeRunnable(), tool_executor=FakeExecutor(),
        tracer=FakeTracer(), retrieval_executor=FakeRetrieval(), capability_registry=FakeReg(),
        scan_skills=lambda t, a: [], skill_block=lambda s: "", bind_runnable=lambda c, t: FakeRunnable(),
        stop_probe=lambda: False, step_budget=1, base_msgs=[]
    ))
    assert getattr(ctx, "token_usage", None) == 77
    assert ctx.metadata.get("token_usage") == 77


def test_token_usage_none_when_unavailable(monkeypatch):
    """Fake model without usage -> run.token_usage stays None (nullable tolerant)."""
    from novi.runtime.react_attempt import run_react_attempt
    from novi.runtime.trace import ExecutionTrace

    class FakeChunk:
        def __init__(self, content):
            self.content = content
            self.additional_kwargs = {}
        def __add__(self, other):
            return FakeChunk((self.content or "") + (other.content or ""))

    class NoUsageRunnable:
        def stream(self, msgs):
            yield FakeChunk("Hello ")
            yield FakeChunk("world")
        def invoke(self, msgs):
            return FakeChunk("Hello world")

    class FakeExecutor:
        def extract_calls(self, ai): return []
        def compute_diff(self, *a, **kw): return None
        def tools_for_mode(self, **kw): return []
        def execute(self, *a, **kw): return SimpleNamespace(output="ok", success=True, diff=None, structured=None)
    class FakeTracer:
        def finalize(self, *a, **kw): pass
        def debug(self, *a, **kw): pass
        def record_tool(self, **kw): pass
    class FakeRetrieval:
        def recommend_when_model_answered(self, ctx):
            from novi.runtime.retrieval import RecoveryAction
            return SimpleNamespace(action=RecoveryAction.NONE)
        def recommend_after_tool(self, *a, **kw):
            from novi.runtime.retrieval import RecoveryAction
            return SimpleNamespace(action=RecoveryAction.NONE)
        def commit_recovery(self, *a, **kw): raise AssertionError
    class FakeReg:
        def get_tool_names(self, caps): return []

    ctx = ExecutionContext(user_input="hi")
    ctx.trace = ExecutionTrace(user_input="hi")
    ctx.activated_skills = []
    ctx.allowed_tools = []
    ctx.retrieval_coordinator = None
    ctx.conversation_id = "conv-3"
    ctx.run_id = "run-3"

    events = list(run_react_attempt(
        ctx=ctx, runnable=NoUsageRunnable(), tool_executor=FakeExecutor(),
        tracer=FakeTracer(), retrieval_executor=FakeRetrieval(), capability_registry=FakeReg(),
        scan_skills=lambda t, a: [], skill_block=lambda s: "", bind_runnable=lambda c, t: NoUsageRunnable(),
        stop_probe=lambda: False, step_budget=1, base_msgs=[]
    ))
    assert getattr(ctx, "token_usage", None) is None
    # coordinator keeps None, not trace sum
    run = _make_run()
    run.context = ctx
    # Add trace tokens to prove they are NOT used as fallback
    from novi.runtime.trace import StepTrace
    ctx.trace.steps.append(StepTrace(step=0, tokens_generated=55))
    from novi.runtime.context_manager import ContextManager
    orig = ContextManager.should_compact
    ContextManager.should_compact = lambda self, ctx: None  # type: ignore
    try:
        coord = ExecutionCoordinator()
        def fake(ctx2, r, emit):
            return AgentAction(type=AgentActionType.FINISH, message="done")
        coord._run_react = fake  # type: ignore
        coord._run_graph = fake  # type: ignore
        events2 = []
        coord.execute(run, lambda e: events2.append(e))
        assert run.token_usage is None, f"expected None when provider absent, got {run.token_usage}"
    finally:
        ContextManager.should_compact = orig  # type: ignore


def test_run_token_usage_nullable_int_tolerant():
    run = _make_run()
    assert run.token_usage is None
    run.token_usage = 42
    assert run.token_usage == 42
    run.token_usage = None
    assert run.token_usage is None


def test_token_usage_uses_input_output_sum(monkeypatch):
    """When usage has input_tokens/output_tokens but no total, sum is used."""
    from novi.runtime.react_attempt import run_react_attempt
    from novi.runtime.trace import ExecutionTrace

    class FakeChunk:
        def __init__(self, content, usage):
            self.content = content
            self.additional_kwargs = {}
            self.usage_metadata = usage
        def __add__(self, other):
            c = FakeChunk((self.content or "") + (other.content or ""), usage=getattr(other, "usage_metadata", None))
            return c

    class FakeRunnable:
        def stream(self, msgs):
            yield FakeChunk("hi", usage={"input_tokens": 10, "output_tokens": 20})

    class FakeExecutor:
        def extract_calls(self, ai): return []
        def compute_diff(self, *a, **kw): return None
        def tools_for_mode(self, **kw): return []
        def execute(self, *a, **kw): return SimpleNamespace(output="ok", success=True, diff=None, structured=None)
    class FakeTracer:
        def finalize(self, *a, **kw): pass
        def debug(self, *a, **kw): pass
        def record_tool(self, **kw): pass
    class FakeRetrieval:
        def recommend_when_model_answered(self, ctx):
            from novi.runtime.retrieval import RecoveryAction
            return SimpleNamespace(action=RecoveryAction.NONE)
        def recommend_after_tool(self, *a, **kw):
            from novi.runtime.retrieval import RecoveryAction
            return SimpleNamespace(action=RecoveryAction.NONE)
        def commit_recovery(self, *a, **kw): raise AssertionError
    class FakeReg:
        def get_tool_names(self, caps): return []

    ctx = ExecutionContext(user_input="hi")
    ctx.trace = ExecutionTrace(user_input="hi")
    ctx.activated_skills = []
    ctx.allowed_tools = []
    ctx.retrieval_coordinator = None
    ctx.conversation_id = "conv-4"
    ctx.run_id = "run-4"

    list(run_react_attempt(
        ctx=ctx, runnable=FakeRunnable(), tool_executor=FakeExecutor(),
        tracer=FakeTracer(), retrieval_executor=FakeRetrieval(), capability_registry=FakeReg(),
        scan_skills=lambda t, a: [], skill_block=lambda s: "", bind_runnable=lambda c, t: FakeRunnable(),
        stop_probe=lambda: False, step_budget=1, base_msgs=[]
    ))
    assert getattr(ctx, "token_usage", None) == 30


def test_invoke_path_propagates_usage():
    """Invoke (no stream) path also captures usage_metadata."""
    from novi.runtime.react_attempt import run_react_attempt
    from novi.runtime.trace import ExecutionTrace
    from types import SimpleNamespace

    class NoStreamRunnable:
        def invoke(self, msgs):
            ch = SimpleNamespace(content="hello", additional_kwargs={}, usage_metadata={"total_tokens": 999}, response_metadata={})
            ch.content = "hello"
            ch.usage_metadata = {"total_tokens": 999}
            ch.response_metadata = {}
            ch.additional_kwargs = {}
            return ch

    class FakeExecutor:
        def extract_calls(self, ai): return []
        def compute_diff(self, *a, **kw): return None
        def tools_for_mode(self, **kw): return []
        def execute(self, *a, **kw): return SimpleNamespace(output="ok", success=True, diff=None, structured=None)
    class FakeTracer:
        def finalize(self, *a, **kw): pass
        def debug(self, *a, **kw): pass
        def record_tool(self, **kw): pass
    class FakeRetrieval:
        def recommend_when_model_answered(self, ctx):
            from novi.runtime.retrieval import RecoveryAction
            return SimpleNamespace(action=RecoveryAction.NONE)
        def recommend_after_tool(self, *a, **kw):
            from novi.runtime.retrieval import RecoveryAction
            return SimpleNamespace(action=RecoveryAction.NONE)
        def commit_recovery(self, *a, **kw): raise AssertionError
    class FakeReg:
        def get_tool_names(self, caps): return []

    ctx = ExecutionContext(user_input="hi")
    ctx.trace = ExecutionTrace(user_input="hi")
    ctx.activated_skills = []
    ctx.allowed_tools = []
    ctx.retrieval_coordinator = None
    ctx.conversation_id = "conv-5"
    ctx.run_id = "run-5"

    list(run_react_attempt(
        ctx=ctx, runnable=NoStreamRunnable(), tool_executor=FakeExecutor(),
        tracer=FakeTracer(), retrieval_executor=FakeRetrieval(), capability_registry=FakeReg(),
        scan_skills=lambda t, a: [], skill_block=lambda s: "", bind_runnable=lambda c, t: NoStreamRunnable(),
        stop_probe=lambda: False, step_budget=1, base_msgs=[]
    ))
    assert getattr(ctx, "token_usage", None) == 999
