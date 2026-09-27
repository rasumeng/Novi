"""All requests use the same natural completion contract, without keyword routing."""

import pytest

from novi.runtime.agent_loop import AgentLoop
from novi.runtime.run_contracts import ModelSnapshot, ModelTurn, RunRequest, RunState, RunStatus


@pytest.mark.parametrize("text", ["hello", "Explain cause and effect", "Write a poem", "What time is it?"])
@pytest.mark.parametrize("supports_tools", [True, False, None])
def test_request_completes_from_provider_answer_without_kind_classifier(text, supports_tools):
    class Provider:
        def stream(self, transcript):
            assert transcript[0].blocks[0].text == text
            yield ModelTurn(text="Here is my answer.", is_complete=True)

    class Dispatcher:
        def dispatch(self, call):
            raise AssertionError("A plain response must not dispatch tools")

    request = RunRequest(conversation_id="c1", user_message_id="u1", user_text=text,
                         model=ModelSnapshot(provider="fake", model="test", supports_tools=supports_tools))
    result = AgentLoop(Provider(), Dispatcher()).run(RunState(id="r1", request=request))
    assert result.state.status is RunStatus.COMPLETED
    assert result.state.terminal_reason == "natural_completion"
    assert result.state.tool_calls == 0
