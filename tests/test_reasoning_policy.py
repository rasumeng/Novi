"""Provider reasoning and prose must not change native tool semantics."""

from types import SimpleNamespace

import pytest

from novi.runtime.agent_loop import AgentLoop
from novi.runtime.provider_adapter import LangChainTurnProvider
from novi.runtime.run_contracts import RunRequest, RunState, RunStatus, ToolResult, ToolResultStatus
from novi.runtime.transcript import MessageRole


class StreamingModel:
    def __init__(self, turns):
        self.turns = iter(turns)

    def stream(self, messages):
        yield from next(self.turns)


class RecordingDispatcher:
    def __init__(self):
        self.calls = []

    def dispatch(self, call):
        self.calls.append(call)
        return ToolResult(call_id=call.id, status=ToolResultStatus.SUCCEEDED, output="ok")


def state(text="hello"):
    return RunState(id="r1", request=RunRequest(
        conversation_id="c1", user_message_id="u1", user_text=text))


@pytest.mark.parametrize("reasoning", ["", "Consider the requested operation."])
def test_reasoning_does_not_gate_native_tool_calls(reasoning):
    model = StreamingModel([
        [SimpleNamespace(content="", additional_kwargs={"reasoning_content": reasoning},
                         tool_calls=[{"id": "c1", "name": "echo", "args": {"value": "hello"}}])],
        [SimpleNamespace(content="Done.")],
    ])
    dispatcher = RecordingDispatcher()
    result = AgentLoop(LangChainTurnProvider(model), dispatcher).run(state("Echo hello"))
    assert result.state.status is RunStatus.COMPLETED
    assert result.state.terminal_reason == "natural_completion"
    assert [(call.name, call.arguments) for call in dispatcher.calls] == [("echo", {"value": "hello"})]
    assert any(message.role is MessageRole.TOOL for message in result.state.transcript)


def test_prose_containing_tool_json_stays_text():
    text = '{"name":"echo","arguments":{"value":"hello"}}'
    dispatcher = RecordingDispatcher()
    model = StreamingModel([[SimpleNamespace(content=text)]])
    result = AgentLoop(LangChainTurnProvider(model), dispatcher).run(state())
    assert result.state.status is RunStatus.COMPLETED
    assert dispatcher.calls == []
    assert result.state.transcript[-1].blocks[0].text == text


def test_reasoning_alone_does_not_count_as_a_final_answer():
    model = StreamingModel([[SimpleNamespace(content="", additional_kwargs={"reasoning_content": "Thinking"})]])
    result = AgentLoop(LangChainTurnProvider(model), RecordingDispatcher()).run(state())
    assert result.state.status is RunStatus.FAILED
    assert result.state.terminal_reason == "empty_model_turn"
