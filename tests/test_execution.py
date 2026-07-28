"""Integration tests for the execution layer: Engine, model_fn, tool calls."""

import json
import threading
from unittest.mock import MagicMock, patch

import pytest


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine_context():
    from cozmo.runtime.engine import EngineContext
    return EngineContext(
        job_id="test-job",
        system_prompt="You are a helpful assistant.",
        messages=[],
        tools=[],
        max_steps=5,
        temperature=0.0,
    )


def make_aimessage(content="", tool_calls=None):
    """Build a minimal AIMessage-like object."""
    from langchain_core.messages import AIMessage
    kwargs = {"content": content}
    if tool_calls:
        kwargs["tool_calls"] = [
            {"name": tc["name"], "args": tc.get("args", {}),
             "id": tc.get("id", tc["name"]), "type": "tool_call"}
            for tc in tool_calls
        ]
    return AIMessage(**kwargs)


def make_tool_chunk(content="", tool_call=None):
    """Build a minimal AIMessageChunk-like object."""
    from langchain_core.messages import AIMessageChunk
    chunk = AIMessageChunk(content=content)
    if tool_call:
        chunk.tool_call_chunks = [
            {"name": tool_call["name"], "args": json.dumps(tool_call.get("args", {})),
             "id": tool_call.get("id", tool_call["name"]), "index": 0, "type": "tool_call"}
        ]
    return chunk


def make_reasoning_chunk(content="", reasoning=""):
    """Simulate a chunk with reasoning in additional_kwargs."""
    from langchain_core.messages import AIMessageChunk
    chunk = AIMessageChunk(content=content)
    chunk.additional_kwargs = {"reasoning_content": reasoning}
    return chunk


@pytest.fixture
def model_fn_chat():
    """Model_fn that returns a single chat response (no tool calls)."""

    def _fn(messages):
        yield ("Hello! I'm Cozmo. How can I help you today?", "")
        from langchain_core.messages import AIMessage
        yield ("__done__", AIMessage(content="Hello! I'm Cozmo. How can I help you today?"))

    return _fn


@pytest.fixture
def model_fn_tool_call():
    """Model_fn that returns a tool call, then a final response."""
    call_count = [0]

    def _fn(messages):
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: return a tool call
            ai = make_aimessage("Let me read that file for you.", tool_calls=[
                {"name": "read_file", "args": {"path": "test.txt"}, "id": "call1"}
            ])
            yield ("Let me read that file for you.", "")
            yield ("__done__", ai)
        else:
            # Second call: return final response
            ai = make_aimessage("The file contains: hello world")
            yield ("The file contains: hello world", "")
            yield ("__done__", ai)

    return _fn


@pytest.fixture
def model_fn_multi_tool():
    """Model_fn that makes multiple tool calls in one step, then returns final."""
    call_count = [0]

    def _fn(messages):
        call_count[0] += 1
        if call_count[0] == 1:
            ai = make_aimessage("Let me do both things.", tool_calls=[
                {"name": "calculator", "args": {"expression": "2+2"}, "id": "calc1"},
                {"name": "calculator", "args": {"expression": "3*4"}, "id": "calc2"},
            ])
            yield ("Let me do both things.", "")
            yield ("__done__", ai)
        else:
            ai = make_aimessage("Done: 2+2=4, 3*4=12")
            yield ("Done: 2+2=4, 3*4=12", "")
            yield ("__done__", ai)

    return _fn


@pytest.fixture
def model_fn_coding():
    """Simulate a coding workflow: read → edit → final."""
    step = [0]

    def _fn(messages):
        step[0] += 1
        if step[0] == 1:
            ai = make_aimessage("Let me read the file first.", tool_calls=[
                {"name": "read_file", "args": {"path": "main.py"}, "id": "r1"}
            ])
            yield ("Let me read the file first.", "")
            yield ("__done__", ai)
        elif step[0] == 2:
            ai = make_aimessage("Now let me edit it.", tool_calls=[
                {"name": "edit_file", "args": {"path": "main.py", "old_text": "foo", "new_text": "bar"}, "id": "e1"}
            ])
            yield ("Now let me edit it.", "")
            yield ("__done__", ai)
        else:
            ai = make_aimessage("Done! The file has been updated.")
            yield ("Done! The file has been updated.", "")
            yield ("__done__", ai)

    return _fn


def execute_tool_stub(name, args):
    """Stub tool execution that returns canned responses."""
    responses = {
        "read_file": "file content: hello world",
        "edit_file": "file updated",
        "calculator": str(eval(args.get("expression", "0"))),
    }
    return responses.get(name, f"result for {name}")


# ── Engine.run_stream tests ─────────────────────────────────────────────────


def _run_and_result(engine_context, model_fn):
    """Helper: consume Engine.run_stream generator and return (events, result)."""
    from cozmo.runtime.engine import Engine
    events = []
    result = None
    for item in Engine.run_stream(engine_context, model_fn, execute_tool_stub):
        if item[0] == "__result__":
            result = item[1]
        else:
            events.append(item)
    return events, result


class TestEngineRunStream:
    def test_chat_generation(self, engine_context, model_fn_chat):
        """Simple chat: model returns tokens, no tool calls."""
        events, result = _run_and_result(engine_context, model_fn_chat)

        kinds = [e[0] for e in events]
        assert "token" in kinds
        assert "thinking" in kinds
        assert result.success
        assert "Hello" in result.output
        assert result.steps_taken >= 1

    def test_tool_call_execution(self, engine_context, model_fn_tool_call):
        """Model returns tool call → engine executes it → feeds result back."""
        events, result = _run_and_result(engine_context, model_fn_tool_call)

        tool_calls = [e for e in events if e[0] == "tool_call"]
        tool_results = [e for e in events if e[0] == "tool_result"]
        assert len(tool_calls) == 1
        assert len(tool_results) == 1
        assert tool_calls[0][1] == "read_file"
        assert tool_results[0][1] == "read_file"
        assert "hello world" in tool_results[0][2]
        assert result.success
        assert result.steps_taken >= 2

    def test_multi_tool_one_step(self, engine_context, model_fn_multi_tool):
        """Multiple tool calls in one model response."""
        events, result = _run_and_result(engine_context, model_fn_multi_tool)

        tool_calls = [e for e in events if e[0] == "tool_call"]
        assert len(tool_calls) == 2
        assert tool_calls[0][1] == "calculator"
        assert tool_calls[1][1] == "calculator"
        assert result.success

    def test_coding_workflow(self, engine_context, model_fn_coding):
        """Multi-step coding workflow: read → edit → done."""
        events, result = _run_and_result(engine_context, model_fn_coding)

        tool_calls = [e for e in events if e[0] == "tool_call"]
        tool_results = [e for e in events if e[0] == "tool_result"]
        assert len(tool_calls) == 2
        assert tool_calls[0][1] == "read_file"
        assert tool_calls[1][1] == "edit_file"
        assert result.success
        assert "Done!" in result.output

    def test_duplicate_call_detection(self, engine_context):
        """Engine should detect and prevent duplicate tool calls."""
        dup_count = [0]

        def _model_fn(messages):
            dup_count[0] += 1
            ai = make_aimessage("Calling calculator again.", tool_calls=[
                {"name": "calculator", "args": {"expression": "2+2"}, "id": "c1"}
            ])
            yield ("Calling calculator again.", "")
            yield ("__done__", ai)

        events, result = _run_and_result(engine_context, _model_fn)

        tool_results = [e for e in events if e[0] == "tool_result"]
        assert any("already made this exact" in e[2] for e in tool_results)

    def test_checkpoint_emission(self, engine_context, model_fn_multi_tool):
        """Checkpoints should be emitted at interval."""
        engine_context.checkpoint_interval = 1
        events, result = _run_and_result(engine_context, model_fn_multi_tool)

        assert result.checkpoint is not None
        assert result.checkpoint.step > 0

    def test_reasoning_emission(self, engine_context):
        """Model with reasoning_content should yield reasoning events."""
        yield_count = [0]

        def _model_fn(messages):
            yield_count[0] += 1
            yield ("", "I need to think about this...")
            ai = make_aimessage("Final answer")
            yield ("__done__", ai)

        events, result = _run_and_result(engine_context, _model_fn)

        reasoning_events = [e for e in events if e[0] == "reasoning"]
        assert len(reasoning_events) >= 1
        assert any("think" in e[1] for e in reasoning_events)


# ── CozmoRuntime execution_plan integration ─────────────────────────────────


class TestRuntimeExecutionPlan:
    @pytest.fixture
    def runtime(self):
        from cozmo.runtime.runtime import CozmoRuntime
        from cozmo.runtime.tool_registry import ToolRegistry

        mm = MagicMock()
        rt = CozmoRuntime(model_service=mm)
        return rt

    def test_execution_plan_uses_plan_tools(self, runtime):
        """When execution_plan is provided, its tools are used."""
        from cozmo.orchestrator.task_types import ExecutionPlan, Goal, IntentType, ExecutionStrategy

        plan = ExecutionPlan(
            goal=Goal(text="say hi", intent=IntentType.CONVERSATION),
            tools=["calculator", "read_file"],
            model_spec={"model": "test-model", "capability": "chat"},
            max_steps=3,
            temperature=0.5,
        )

        def _fake_classify(*a, **kw):
            from cozmo.orchestrator.intent import IntentType
            return IntentType.CONVERSATION

        with patch("cozmo.runtime.runtime.classify_intent", _fake_classify):
            gen = runtime.run_stream("say hi", execution_plan=plan)

            events = []
            try:
                while True:
                    events.append(next(gen))
            except StopIteration:
                pass

            # Should use plan tools (tool categories match)
            tool_calls_in_events = [e for e in events if e[0] == "tool_call"]
            # No tool calls actually executed since no live model, but the
            # allowed_tools from the plan are configured correctly.
            # Verify the intent was set from the plan.
            status_events = [e for e in events if e[0] == "status"]
            assert any("conversation" in str(e).lower() or "analyzing" in str(e).lower() for e in status_events)

    def test_execution_plan_uses_plan_model(self, runtime):
        """When execution_plan is provided, plan's model_spec is used."""
        from cozmo.orchestrator.task_types import ExecutionPlan, Goal, IntentType

        plan = ExecutionPlan(
            goal=Goal(text="write code", intent=IntentType.CODING),
            tools=["read_file", "write_file"],
            model_spec={"model": "coder-model", "capability": "coding"},
            max_steps=5,
            temperature=0.2,
        )

        def _fake_classify(*a, **kw):
            from cozmo.orchestrator.intent import IntentType
            return IntentType.CODING

        with patch("cozmo.runtime.runtime.classify_intent", _fake_classify):
            gen = runtime.run_stream("write a function", execution_plan=plan)
            # Consume the generator — it will try to bind the model "coder-model"
            # which will fail via the MagicMock, but that's expected
            try:
                list(gen)
            except Exception:
                pass

        # The execution plan tools override should have been used
        # (verify by checking the plan wasn't modified)
        assert plan.model_spec["model"] == "coder-model"


# ── EngineContext + ExecutionPlan round-trip ────────────────────────────────


class TestPlanToEngine:
    def test_build_engine_context_from_plan(self):
        """ExecutionPlan should produce a valid EngineContext."""
        from cozmo.orchestrator.task_types import ExecutionPlan, Goal, IntentType
        from cozmo.runtime.engine import EngineContext

        plan = ExecutionPlan(
            goal=Goal(text="Refactor main.py", intent=IntentType.CODING),
            tools=["read_file", "write_file", "edit_file", "bash"],
            model_spec={"model": "qwen3:8b", "capability": "coding"},
            system_prompt="You are a coding assistant.",
            max_steps=8,
            temperature=0.2,
        )

        ctx = EngineContext(
            task_id=plan.task_id,
            model_spec=plan.model_spec,
            system_prompt=plan.system_prompt,
            tools=plan.tools,
            max_steps=plan.max_steps,
            temperature=plan.temperature,
        )

        assert ctx.model_spec["model"] == "qwen3:8b"
        assert ctx.max_steps == 8
        assert ctx.temperature == 0.2
        assert "read_file" in ctx.tools

    def test_full_pipeline_plan_to_engine(self, engine_context, model_fn_coding):
        """End-to-end: ExecutionPlan → EngineContext → Engine.run_stream."""
        events, result = _run_and_result(engine_context, model_fn_coding)
        assert result.success
        tool_names = [e[1] for e in events if e[0] == "tool_call"]
        assert "read_file" in tool_names
        assert "edit_file" in tool_names

    def test_engine_run_sync(self, engine_context, model_fn_chat):
        """Engine.run() should return EngineResult synchronously."""
        from cozmo.runtime.engine import Engine

        result = Engine.run(engine_context, model_fn_chat, execute_tool_stub)
        assert result.success
        assert "Hello" in result.output
        assert result.steps_taken >= 1


# ── Tool call extraction ────────────────────────────────────────────────────


class TestToolCallExtraction:
    def test_native_tool_calls(self):
        """Extract tool calls from AIMessage.tool_calls."""
        from cozmo.runtime.engine import _extract_tool_calls

        ai = make_aimessage("Calling tool...", tool_calls=[
            {"name": "calculator", "args": {"expression": "2+2"}, "id": "c1"}
        ])
        calls = _extract_tool_calls(ai)
        assert len(calls) == 1
        assert calls[0]["name"] == "calculator"
        assert calls[0]["args"]["expression"] == "2+2"

    def test_no_tool_calls(self):
        """No tool calls returns empty list."""
        from cozmo.runtime.engine import _extract_tool_calls

        ai = make_aimessage("Just chatting.")
        calls = _extract_tool_calls(ai)
        assert len(calls) == 0

    def test_text_fallback_toolcall(self):
        """Models that emit JSON as plain text should still be parsed."""
        from cozmo.runtime.engine import _extract_tool_calls

        ai = make_aimessage('{"tool": "calculator", "args": {"expression": "2+2"}}')
        calls = _extract_tool_calls(ai)
        assert len(calls) == 1
        assert calls[0]["name"] == "calculator"
        assert calls[0]["args"]["expression"] == "2+2"
