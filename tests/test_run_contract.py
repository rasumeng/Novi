from dataclasses import FrozenInstanceError

import pytest

from novi.runtime.run_contracts import EVENT_TYPES, RunEvent, RunEventType, RunImage, RunRequest, RunState, RunStatus
from novi.runtime.transcript import ContentBlock, ContentBlockType, MessageRole, TranscriptMessage


def test_request_requires_explicit_conversation_and_message_identity():
    with pytest.raises(ValueError):
        RunRequest(conversation_id="", user_message_id="m1", user_text="hello")
    with pytest.raises(ValueError):
        RunRequest(conversation_id="c1", user_message_id="", user_text="hello")


def test_run_image_rejects_non_image_media_type():
    with pytest.raises(ValueError, match="image media type"):
        RunImage(id="a", name="notes.txt", media_type="text/plain", path="C:/notes.txt")


def test_request_rejects_legacy_image_dicts():
    with pytest.raises(TypeError, match="RunImage"):
        RunRequest(conversation_id="c", user_message_id="u", user_text="see this",
                   images=({"id": "legacy"},))  # type: ignore[arg-type]


def test_run_has_all_honest_terminal_states():
    request = RunRequest(conversation_id="c1", user_message_id="m1", user_text="hello")
    for status in (
        RunStatus.COMPLETED, RunStatus.BLOCKED, RunStatus.FAILED,
        RunStatus.CANCELLED, RunStatus.INTERRUPTED,
    ):
        assert RunState(id="r1", request=request, status=status).finished
    assert not RunState(id="r1", request=request, status=RunStatus.AWAITING_PERMISSION).finished


def test_event_is_strict_and_has_no_tuple_protocol():
    event = RunEvent(
        id="e1", sequence=1, run_id="r1", conversation_id="c1",
        type=RunEventType.RUN_STARTED, payload={"status": "running"})
    assert event.to_dict()["type"] == "run.started"
    with pytest.raises(TypeError):
        _ = event[0]  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        event.sequence = 2  # type: ignore[misc]


def test_canonical_events_are_complete():
    assert EVENT_TYPES == {item.value for item in RunEventType}
    assert "permission.requested" in EVENT_TYPES
    assert "run.interrupted" in EVENT_TYPES


def test_transcript_round_trip_keeps_call_identity_and_visibility():
    message = TranscriptMessage(
        id="a1", role=MessageRole.ASSISTANT, visible_to_user=False,
        source="model", trust="untrusted",
        blocks=(ContentBlock(type=ContentBlockType.TOOL_CALL, call_id="call-1",
                             tool_name="read_file", arguments={"path": "a.py"}),))
    assert TranscriptMessage.from_dict(message.to_dict()) == message


def test_transcript_round_trip_keeps_typed_image():
    image = RunImage(id="img", name="photo.png", media_type="image/png",
                     path="C:/uploads/img.png")
    message = TranscriptMessage(id="u", role=MessageRole.USER,
                                blocks=(ContentBlock.image(image),))
    assert TranscriptMessage.from_dict(message.to_dict()) == message
