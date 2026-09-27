"""WebSocket event delivery regression tests."""

import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from novi.runtime.agent_loop import AgentLoop
from novi.runtime.run_contracts import (
    ModelSnapshot, ModelTurn, RunEventType, RunRequest, RunState, RunStatus, ToolCall
)
from novi.runtime.transcript import ContentBlock, ContentBlockType, MessageRole, TranscriptMessage
from novi.services.run_service import RunService
from novi.services.run_store import RunStore
from novi.services.run_transport import event_to_socket_message


def _make_test_state():
    req = RunRequest(
        conversation_id="c1", user_message_id="u1", user_text="hello",
        model=ModelSnapshot(provider="ollama", model="test", supports_tools=True),
    )
    transcript = (TranscriptMessage(
        id="u1", role=MessageRole.USER,
        blocks=(ContentBlock(type=ContentBlockType.TEXT, text="hello"),)
    ),)
    return RunState(id="r1", request=req, transcript=transcript)


def test_event_to_socket_message_thinking():
    """THINKING events map to WebSocket 'thinking' type."""
    from novi.runtime.run_contracts import RunEvent
    event = RunEvent(
        id="e1", sequence=1, run_id="r1", conversation_id="c1",
        type=RunEventType.THINKING, payload={"text": "Calling search…"},
        timestamp=datetime.now(timezone.utc)
    )
    msg = event_to_socket_message(event)
    assert msg is not None
    assert msg["type"] == "thinking"
    assert msg["text"] == "Calling search…"
    assert msg["runId"] == "r1"
    assert msg["conversationId"] == "c1"


def test_event_to_socket_message_status():
    """STATUS events map to WebSocket 'status' type."""
    from novi.runtime.run_contracts import RunEvent
    event = RunEvent(
        id="e1", sequence=1, run_id="r1", conversation_id="c1",
        type=RunEventType.STATUS, payload={"text": "Executing search…"},
        timestamp=datetime.now(timezone.utc)
    )
    msg = event_to_socket_message(event)
    assert msg is not None
    assert msg["type"] == "status"
    assert msg["text"] == "Executing search…"


def test_event_to_socket_message_all_types():
    """Verify all event types map to WebSocket messages."""
    from novi.runtime.run_contracts import RunEvent
    base = {"id": "e1", "sequence": 1, "run_id": "r1", "conversation_id": "c1", "timestamp": datetime.now(timezone.utc)}
    
    mappings = {
        RunEventType.RUN_STARTED: "run_state",
        RunEventType.RUN_STATE_CHANGED: "run_state",
        RunEventType.MESSAGE_STARTED: "message_start",
        RunEventType.MESSAGE_DELTA: "token",
        RunEventType.MESSAGE_COMPLETED: "message_end",
        RunEventType.TOOL_REQUESTED: "tool_call",
        RunEventType.TOOL_STARTED: "tool.started",
        RunEventType.TOOL_COMPLETED: "tool_result",
        RunEventType.PERMISSION_REQUESTED: "permission_request",
        RunEventType.PERMISSION_RESOLVED: "permission_resolved",
        RunEventType.CONTEXT_COMPACTING: "status",
        RunEventType.CONTEXT_COMPACTED: "status",
        RunEventType.THINKING: "thinking",
        RunEventType.STATUS: "status",
        RunEventType.RUN_COMPLETED: "done",
        RunEventType.RUN_CANCELLED: "cancelled",
        RunEventType.RUN_FAILED: "error",
        RunEventType.RUN_BLOCKED: "error",
        RunEventType.RUN_INTERRUPTED: "error",
    }
    
    for ev_type, expected_ws_type in mappings.items():
        event = RunEvent(type=ev_type, payload={}, **base)
        msg = event_to_socket_message(event)
        assert msg is not None, f"{ev_type} should map to {expected_ws_type}"
        assert msg["type"] == expected_ws_type, f"{ev_type} -> {msg['type']}, expected {expected_ws_type}"


def test_agent_loop_emits_thinking_for_tools(tmp_path):
    """AgentLoop emits THINKING events for tool calls."""
    from novi.services.permission_service import (
        PermissionService, ToolAuthorizationPolicy, ToolDescriptor,
        AuthorizedToolDispatcher,
    )
    
    # Provider returns a tool call followed by a final response.
    class Provider:
        def __init__(self):
            self.calls = 0
        def stream(self, transcript):
            if self.calls == 0:
                self.calls += 1
                yield ModelTurn(calls=(ToolCall("c1", "report_progress", {"message": "thinking test"}),), is_complete=True)
            else:
                yield ModelTurn(text='done', is_complete=True)
    
    provider = Provider()
    perms = PermissionService(descriptors={"report_progress": ToolDescriptor("report_progress", effects=())}, policy=ToolAuthorizationPolicy())
    state = _make_test_state()
    
    loop = AgentLoop(provider, AuthorizedToolDispatcher(permission_service=perms, exposed_tools={"report_progress"}, run_id=state.id, executors={"report_progress": lambda args: args["message"]}))
    result = loop.run(state)
    
    # Check that THINKING events were emitted
    thinking_events = [e for e in result.events if e.type == RunEventType.THINKING]
    assert len(thinking_events) >= 1, "Should emit at least one THINKING event"
    assert any("report_progress" in e.payload.get("text", "") for e in thinking_events)


def test_agent_loop_emits_thinking_for_external_tool(tmp_path):
    """AgentLoop emits THINKING/STATUS for external tool execution."""
    from novi.services.permission_service import (
        PermissionService, ToolAuthorizationPolicy, ToolDescriptor,
        AuthorizedToolDispatcher,
    )
    
    class Provider:
        def __init__(self):
            self.calls = 0
        def stream(self, transcript):
            if self.calls == 0:
                self.calls += 1
                yield ModelTurn(calls=(ToolCall("c1", "read_file", {"path": "test.txt"}),), is_complete=True)
            else:
                yield ModelTurn(text='done', is_complete=True)
    
    provider = Provider()
    perms = PermissionService(descriptors={"read_file": ToolDescriptor("read_file", effects=("read",))}, policy=ToolAuthorizationPolicy(tool_rules={"read_file": "allow"}))
    state = _make_test_state()
    
    loop = AgentLoop(provider, AuthorizedToolDispatcher(permission_service=perms, exposed_tools={"read_file"}, run_id=state.id, executors={"read_file": lambda args: "content"}))
    result = loop.run(state)
    
    # Check that THINKING and STATUS events were emitted for external tool
    thinking_events = [e for e in result.events if e.type == RunEventType.THINKING]
    status_events = [e for e in result.events if e.type == RunEventType.STATUS]
    
    assert len(thinking_events) >= 1, "Should emit THINKING events"
    assert any("read_file" in e.payload.get("text", "") for e in thinking_events), "THINKING should mention tool name"
    assert len(status_events) >= 1, "Should emit STATUS events"
    assert any("read_file" in e.payload.get("text", "") for e in status_events), "STATUS should mention tool name"


def test_conversational_run_with_tool_failure_completes(tmp_path):
    """Conversational run with tool failure completes without missing_finish_task."""
    from novi.services.permission_service import (
        PermissionService, ToolAuthorizationPolicy, ToolDescriptor,
        AuthorizedToolDispatcher,
    )
    
    class Provider:
        def __init__(self):
            self.calls = 0
        def stream(self, transcript):
            if self.calls == 0:
                self.calls += 1
                # Tool fails
                yield ModelTurn(calls=(ToolCall("c1", "search", {"query": "test"}),), is_complete=True)
            else:
                # Model responds with text about failure
                yield ModelTurn(text="The search tool failed, but I can still help.", is_complete=True)
    
    provider = Provider()
    perms = PermissionService(descriptors={"search": ToolDescriptor("search", effects=("read",))}, policy=ToolAuthorizationPolicy(tool_rules={"search": "allow"}))
    state = _make_test_state()
    
    loop = AgentLoop(provider, AuthorizedToolDispatcher(permission_service=perms, exposed_tools={"search"}, run_id=state.id, executors={"search": lambda args: (_ for _ in ()).throw(Exception("search failed"))}))
    result = loop.run(state)
    
    # Should complete conversationally, not fail with missing_finish_task
    assert result.state.status == RunStatus.COMPLETED
    assert result.state.terminal_reason == "natural_completion"


def test_run_service_emits_thinking_events(tmp_path):
    """RunService correctly delivers THINKING events to subscribers."""
    from novi.services.permission_service import (
        PermissionService, ToolAuthorizationPolicy, ToolDescriptor,
        AuthorizedToolDispatcher,
    )
    
    received = []
    
    class Provider:
        def __init__(self):
            self.calls = 0
        def stream(self, transcript):
            if self.calls == 0:
                self.calls += 1
                yield ModelTurn(calls=(ToolCall("c1", "report_progress", {"message": "thinking"}),), is_complete=True)
            else:
                yield ModelTurn(text='done', is_complete=True)
    
    provider = Provider()
    perms = PermissionService(descriptors={"report_progress": ToolDescriptor("report_progress", effects=())}, policy=ToolAuthorizationPolicy())
    
    def factory(state, cancelled):
        return AgentLoop(provider, AuthorizedToolDispatcher(permission_service=perms, exposed_tools={"report_progress"}, run_id=state.id, executors={"report_progress": lambda args: args["message"]}), cancelled=cancelled)
    
    store = RunStore(tmp_path)
    svc = RunService(store, factory)
    
    svc.listen(received.append)
    svc.start(RunRequest(conversation_id="c1", user_message_id="u1", user_text="test"))
    
    # Check that THINKING events were delivered
    thinking_events = [e for e in received if e.type == RunEventType.THINKING]
    assert len(thinking_events) >= 1, "RunService should deliver THINKING events"
    ws_msgs = [event_to_socket_message(e) for e in thinking_events]
    assert all(m and m["type"] == "thinking" for m in ws_msgs), "THINKING events should map to WebSocket 'thinking'"