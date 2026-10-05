from types import SimpleNamespace

from novi.runtime.provider_adapter import LangChainTurnProvider, transcript_to_messages
from novi.runtime.run_contracts import RunImage, ToolCall
from novi.runtime.transcript import ContentBlock, ContentBlockType, MessageRole, TranscriptMessage


def test_user_image_attachment_becomes_multimodal_message(tmp_path):
    image = tmp_path / "pixel.png"
    image.write_bytes(b"image-bytes")
    transcript = (TranscriptMessage(id="u", role=MessageRole.USER, blocks=(
        ContentBlock(type=ContentBlockType.TEXT, text="describe this"),
        ContentBlock.image(RunImage(id="pixel", name="pixel.png",
                                   media_type="image/png", path=str(image))),
    )),)
    (message,) = transcript_to_messages(transcript)
    assert message.content[0]["text"].endswith("describe this")
    assert message.content[1]["type"] == "image_url"
    assert message.content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def _image_message(tmp_path, *names):
    blocks = [ContentBlock(type=ContentBlockType.TEXT, text="what does this say?")]
    for index, name in enumerate(names):
        path = tmp_path / name
        path.write_bytes(b"image-bytes")
        blocks.append(ContentBlock.image(RunImage(
            id=f"att-{index}", name=name, media_type="image/png", path=str(path))))
    (message,) = transcript_to_messages((TranscriptMessage(
        id="u", role=MessageRole.USER, blocks=tuple(blocks)),))
    return message


def test_image_message_tells_model_to_answer_from_what_it_sees(tmp_path):
    text = _image_message(tmp_path, "image.png").content[0]["text"]
    assert "what does this say?" in text
    assert "image.png" in text
    # It must forbid going off to look for the content elsewhere.
    assert "directly" in text.lower()
    assert "do not read files" in text.lower()
    assert "search" in text.lower()


def test_image_message_framing_names_every_attachment(tmp_path):
    text = _image_message(tmp_path, "one.png", "two.png").content[0]["text"]
    assert "2 images" in text
    assert "one.png" in text and "two.png" in text


def test_image_message_framing_survives_empty_user_text(tmp_path):
    path = tmp_path / "solo.png"
    path.write_bytes(b"image-bytes")
    (message,) = transcript_to_messages((TranscriptMessage(
        id="u", role=MessageRole.USER, blocks=(
            ContentBlock.image(RunImage(id="a", name="solo.png",
                                       media_type="image/png", path=str(path))),
        )),))
    assert "solo.png" in message.content[0]["text"]


def test_text_only_message_gets_no_image_framing():
    (message,) = transcript_to_messages((_user("just text"),))
    assert message.content == "just text"


def test_missing_image_fails_before_calling_provider(tmp_path):
    missing = tmp_path / "missing.png"
    transcript = (TranscriptMessage(id="u", role=MessageRole.USER, blocks=(
        ContentBlock(type=ContentBlockType.TEXT, text="describe this"),
        ContentBlock.image(RunImage(id="missing", name="missing.png",
                                   media_type="image/png", path=str(missing))),
    )),)

    import pytest
    with pytest.raises(FileNotFoundError, match="missing.png"):
        transcript_to_messages(transcript)


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
    final_turn = next(t for t in turns if t.is_complete)

    assert final_turn.text.startswith("{")
    assert final_turn.calls == (ToolCall("call-1", "read_file", {"path": "a.txt"}),)
    assert final_turn.input_tokens == 12
    assert final_turn.output_tokens == 4


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


def test_finalized_native_tool_calls_single_chunk():
    # Provider returns a single finalized AIMessage chunk with tool_calls dict
    chunk = SimpleNamespace(
        content="",
        tool_calls=[{"id": "call-f1", "name": "finish_task", "args": {"summary": "done", "outcome": "completed"}}],
        tool_call_chunks=[],
        usage_metadata=None,
        response_metadata={"finish_reason": "tool_calls"},
    )
    turn = list(LangChainTurnProvider(StreamingModel([chunk])).stream((_user("hi"),)))[0]
    assert turn.calls == (ToolCall("call-f1", "finish_task", {"summary": "done", "outcome": "completed"}),)
    assert turn.text == ""


def test_streamed_partial_tool_call_chunks_accumulated():
    # Simulate OpenAI-style streaming: args split across chunks with index
    c1 = SimpleNamespace(
        content="",
        tool_call_chunks=[{"name": "finish_task", "args": '{"summary":', "id": "call-2", "index": 0, "type": "tool_call_chunk"}],
        tool_calls=[],
        response_metadata={},
    )
    c2 = SimpleNamespace(
        content="",
        tool_call_chunks=[{"name": None, "args": ' "hello"', "id": None, "index": 0, "type": "tool_call_chunk"}],
        tool_calls=[],
        response_metadata={},
    )
    c3 = SimpleNamespace(
        content="",
        tool_call_chunks=[{"name": None, "args": ', "outcome": "completed"}', "id": None, "index": 0, "type": "tool_call_chunk"}],
        tool_calls=[],
        response_metadata={},
    )
    turn = list(LangChainTurnProvider(StreamingModel([c1, c2, c3])).stream((_user("hi"),)))[0]
    assert turn.calls == (ToolCall("call-2", "finish_task", {"summary": "hello", "outcome": "completed"}),)


def test_mixed_text_plus_finish_task_stream():
    # Provider streams text first, then finish_task tool call via chunks
    text_chunk = SimpleNamespace(content="Sure, ", tool_call_chunks=[], tool_calls=[], response_metadata={})
    tc1 = SimpleNamespace(
        content="",
        tool_call_chunks=[{"name": "finish_task", "args": '{"summary": "done', "id": "call-3", "index": 0, "type": "tool_call_chunk"}],
        tool_calls=[],
        response_metadata={},
    )
    tc2 = SimpleNamespace(content="", tool_call_chunks=[{"name": None, "args": '", "outcome": "completed"}', "id": None, "index": 0, "type": "tool_call_chunk"}], tool_calls=[], response_metadata={})
    turns = list(LangChainTurnProvider(StreamingModel([text_chunk, tc1, tc2])).stream((_user("hi"),)))
    final_turn = next(t for t in turns if t.is_complete)
    assert final_turn.text == "Sure, "
    assert final_turn.calls == (ToolCall("call-3", "finish_task", {"summary": "done", "outcome": "completed"}),)


def test_malformed_incomplete_tool_arguments_safely_skipped():
    # Incomplete JSON args should not produce a canonical ToolCall and must not crash
    c1 = SimpleNamespace(
        content="",
        tool_call_chunks=[{"name": "finish_task", "args": '{"summary":', "id": "call-mal", "index": 0, "type": "tool_call_chunk"}],
        tool_calls=[],
        response_metadata={},
    )
    turn = list(LangChainTurnProvider(StreamingModel([c1])).stream((_user("hi"),)))[0]
    assert turn.calls == ()
    # Malformed JSON
    c2 = SimpleNamespace(
        content="",
        tool_call_chunks=[{"name": "read_file", "args": '{"path": }', "id": "call-bad", "index": 0, "type": "tool_call_chunk"}],
        tool_calls=[],
        response_metadata={},
    )
    turn2 = list(LangChainTurnProvider(StreamingModel([c2])).stream((_user("hi"),)))[0]
    assert turn2.calls == ()


def test_ordinary_text_json_not_promoted():
    # Text that looks like JSON must remain text, not become a tool call
    chunk = SimpleNamespace(
        content='Here is JSON: {"name":"finish_task","arguments":{"summary":"hi"}}',
        tool_calls=[],
        tool_call_chunks=[],
        response_metadata={},
    )
    turn = list(LangChainTurnProvider(StreamingModel([chunk])).stream((_user("hi"),)))[0]
    assert turn.calls == ()
    assert '{"name":"finish_task"' in turn.text


def test_streamed_tool_calls_with_partial_ids_and_names_across_chunks():
    # Simulate provider that sends id/name only on first chunk, args deltas after
    c1 = SimpleNamespace(
        content="",
        tool_call_chunks=[{"name": "read_file", "args": '{"path": "', "id": "call-4", "index": 0, "type": "tool_call_chunk"}],
        tool_calls=[],
        response_metadata={},
    )
    c2 = SimpleNamespace(content="", tool_call_chunks=[{"name": None, "args": 'a.txt"', "id": None, "index": 0, "type": "tool_call_chunk"}], tool_calls=[], response_metadata={})
    c3 = SimpleNamespace(content="", tool_call_chunks=[{"name": None, "args": '}', "id": None, "index": 0, "type": "tool_call_chunk"}], tool_calls=[], response_metadata={})
    turn = list(LangChainTurnProvider(StreamingModel([c1, c2, c3])).stream((_user("hi"),)))[0]
    assert turn.calls == (ToolCall("call-4", "read_file", {"path": "a.txt"}),)


def test_additional_kwargs_native_tool_calls():
    chunk = SimpleNamespace(
        content="",
        tool_calls=[],
        tool_call_chunks=[],
        additional_kwargs={"tool_calls": [{"id": "call-5", "function": {"name": "finish_task", "arguments": '{"summary":"ok","outcome":"completed"}'}, "type": "function"}]},
        response_metadata={},
    )
    turn = list(LangChainTurnProvider(StreamingModel([chunk])).stream((_user("hi"),)))[0]
    assert turn.calls == (ToolCall("call-5", "finish_task", {"summary": "ok", "outcome": "completed"}),)


def test_text_streaming_preserved_separately_from_tool_calls():
    # Ensure text and tool call streams are not conflated
    chunks = [
        SimpleNamespace(content="Hello ", tool_call_chunks=[], tool_calls=[], response_metadata={}),
        SimpleNamespace(content="world", tool_call_chunks=[], tool_calls=[], response_metadata={}),
        SimpleNamespace(
            content="",
            tool_call_chunks=[{"name": "finish_task", "args": '{"summary":"done","outcome":"completed"}', "id": "call-6", "index": 0, "type": "tool_call_chunk"}],
            tool_calls=[],
            response_metadata={},
        ),
    ]
    turns = list(LangChainTurnProvider(StreamingModel(chunks)).stream((_user("hi"),)))
    final_turn = next(t for t in turns if t.is_complete)
    assert final_turn.text == "Hello world"
    assert final_turn.calls[0].name == "finish_task"


def _user(text):
    return TranscriptMessage(id="u", role=MessageRole.USER,
        blocks=(ContentBlock(type=ContentBlockType.TEXT, text=text),))
