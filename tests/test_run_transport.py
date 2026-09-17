from datetime import datetime, timezone

from novi.runtime.run_contracts import RunEvent, RunEventType
from novi.services.run_transport import event_to_socket_message


def event(kind, payload=None, sequence=1):
    return RunEvent(id=f"e{sequence}", sequence=sequence, run_id="r1",
        conversation_id="c1", type=kind, payload=payload or {},
        timestamp=datetime.now(timezone.utc))


def test_message_and_tool_events_keep_stable_ids_and_cursor():
    message = event_to_socket_message(event(RunEventType.MESSAGE_DELTA,
        {"message_id": "m1", "content": "hello"}, sequence=4))
    tool = event_to_socket_message(event(RunEventType.TOOL_COMPLETED,
        {"call_id": "call-1", "name": "read_file", "status": "failed",
         "error": "missing"}, sequence=5))

    assert message == {"type": "token", "text": "hello", "messageId": "m1",
                       "runId": "r1", "conversationId": "c1", "sequence": 4}
    assert tool["id"] == "call-1"
    assert tool["status"] == "failed"
    assert tool["sequence"] == 5


def test_permission_event_exposes_exact_request_and_expiry():
    message = event_to_socket_message(event(RunEventType.PERMISSION_REQUESTED, {
        "request_id": "p1", "name": "write_file", "arguments": {"path": "a"},
        "argument_digest": "digest", "effects": ["write"],
        "expires_at": "2026-09-16T00:00:00+00:00"}))

    assert message["type"] == "permission_request"
    assert message["id"] == "p1"
    assert message["digest"] == "digest"
    assert message["effects"] == ["write"]


def test_terminal_events_are_never_inferred_from_message_completion():
    assert event_to_socket_message(event(RunEventType.MESSAGE_COMPLETED,
        {"message_id": "m1", "content": "partial"}))["type"] == "message_end"
    assert event_to_socket_message(event(RunEventType.RUN_COMPLETED))["type"] == "done"
    assert event_to_socket_message(event(RunEventType.RUN_CANCELLED))["type"] == "cancelled"
    assert event_to_socket_message(event(RunEventType.RUN_FAILED,
        {"reason": "provider_error"}))["type"] == "error"


def test_run_started_establishes_projection_identity_and_sequence():
    message = event_to_socket_message(event(RunEventType.RUN_STARTED,
        {"status": "running"}, sequence=1))
    assert message == {"type": "run_state", "status": "running",
                       "runId": "r1", "conversationId": "c1", "sequence": 1}
