from types import SimpleNamespace

from novi.runtime.provider_adapter import LangChainTurnProvider, transcript_to_messages
from novi.runtime.run_contracts import ToolCall
from novi.runtime.transcript import ContentBlock, ContentBlockType, MessageRole, TranscriptMessage


class StreamingModel:
    def __init__(self, chunks):
        self.chunks = chunks
        self.seen = None

    def stream(self, messages):
        self.seen = messages
        yield from self.chunks


def test_provider_uses_only_native_tool_calls_and_usage_metadata():
    chunk = SimpleNamespace(
        content='{"name":"danger","arguments":{"x":1}}',
        tool_calls=[{"id": "call-1", "name": "read_file", "args": {"path": "a.txt"}}],
        usage_metadata={"input_tokens": 12, "output_tokens": 4},
        response_metadata={"finish_reason": "tool_calls"},
    )
    model = StreamingModel([chunk])

    turns = list(LangChainTurnProvider(model).stream((_user("hello"),)))

    assert turns[0].text.startswith("{")
    assert turns[0].calls == (ToolCall("call-1", "read_file", {"path": "a.txt"}),)
    assert turns[0].input_tokens == 12
    assert turns[0].output_tokens == 4


def test_json_prose_is_not_promoted_to_a_tool_call():
    model = StreamingModel([SimpleNamespace(
        content='{"name":"read_file","arguments":{"path":"secret"}}',
        tool_calls=[], usage_metadata=None, response_metadata={})])

    turn = list(LangChainTurnProvider(model).stream((_user("hello"),)))[0]

    assert turn.calls == ()


def test_transcript_preserves_assistant_call_ids_and_tool_results():
    transcript = (
        _user("inspect"),
        TranscriptMessage(id="a", role=MessageRole.ASSISTANT, blocks=(
            ContentBlock(type=ContentBlockType.TOOL_CALL, call_id="c1",
                         tool_name="read_file", arguments={"path": "a.txt"}),)),
        TranscriptMessage(id="t", role=MessageRole.TOOL, blocks=(
            ContentBlock(type=ContentBlockType.TOOL_RESULT, call_id="c1",
                         result={"status": "succeeded", "output": "contents"}),)),
    )

    messages = transcript_to_messages(transcript)

    assert messages[1].tool_calls[0]["id"] == "c1"
    assert messages[2].tool_call_id == "c1"
    assert messages[2].content == "contents"


def _user(text):
    return TranscriptMessage(id="u", role=MessageRole.USER,
        blocks=(ContentBlock(type=ContentBlockType.TEXT, text=text),))
