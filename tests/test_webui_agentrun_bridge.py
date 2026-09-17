"""Task 6: WebSocket Session Bridge — mapping and run.completed→done invariant."""

import uuid
import threading
import time

from novi.webui_server import Session
from novi.runtime.agent_events import AgentEvent
from novi.runtime.agent_run import AgentRun, AgentRunStatus
from novi.runtime.execution_context import ExecutionContext
from novi.services.execution import ExecutionCoordinator, AgentCancelled, ExpectedExecutionError


def test_map_agent_event_only_run_completed_to_done():
    sess = Session.__new__(Session)  # bypass init
    assert sess._map_agent_event_to_ws(AgentEvent(type="message.completed", run_id="r1", conversation_id="c1", message_id="m1"))["type"] == "message_end"
    assert sess._map_agent_event_to_ws(AgentEvent(type="run.completed", run_id="r1", conversation_id="c1"))["type"] == "done"
    assert sess._map_agent_event_to_ws(AgentEvent(type="message.completed", run_id="r1", conversation_id="c1", message_id="m1"))["type"] != "done"


def test_map_message_started_to_message_start():
    sess = Session.__new__(Session)
    m = sess._map_agent_event_to_ws(AgentEvent(type="message.started", run_id="r1", conversation_id="c1", message_id="m1"))
    assert m["type"] == "message_start"
    assert m["messageId"] == "m1"
    assert m["runId"] == "r1"


def test_map_delta_to_token():
    sess = Session.__new__(Session)
    m = sess._map_agent_event_to_ws(AgentEvent(type="message.delta", run_id="r1", conversation_id="c1", message_id="m1", message="hello "))
    assert m["type"] == "token"
    assert m["text"] == "hello "
    assert m["messageId"] == "m1"


def test_legacy_runtime_tokens_are_forwarded_as_message_lifecycle():
    """AgentRun's compatibility bridge must not drop runtime.run_stream tokens."""
    class RuntimeStub:
        def run_stream(self, **kwargs):
            yield ("token", "The weather is sunny.")
            yield ("__plan_step_done__", "The weather is sunny.", "completed", True)

    coord = ExecutionCoordinator()
    coord._runtime = RuntimeStub()
    run = _make_run()
    events = []
    action = coord._run_react(run.context, run, events.append)
    assert action.type.value == "finish"
    assert [e.type for e in events] == ["message.started", "message.delta", "message.completed"]
    assert events[1].message == "The weather is sunny."
    assert events[0].message_id == events[1].message_id == events[2].message_id


def test_map_run_failed_to_error():
    sess = Session.__new__(Session)
    m = sess._map_agent_event_to_ws(AgentEvent(type="run.failed", run_id="r1", conversation_id="c1", error="boom"))
    assert m["type"] == "error"
    assert m["text"] == "boom"
    assert m["runId"] == "r1"


def test_map_run_cancelled():
    sess = Session.__new__(Session)
    m = sess._map_agent_event_to_ws(AgentEvent(type="run.cancelled", run_id="r1", conversation_id="c1"))
    assert m["type"] == "cancelled"
    assert m["runId"] == "r1"


def test_map_tool_events_same_type():
    sess = Session.__new__(Session)
    m = sess._map_agent_event_to_ws(AgentEvent(type="tool.started", run_id="r1", conversation_id="c1", tool="read", args={"path": "a.py"}))
    assert m["type"] == "tool.started"
    assert m["tool"] == "read"
    m2 = sess._map_agent_event_to_ws(AgentEvent(type="tool.completed", run_id="r1", conversation_id="c1", tool="read", result="ok"))
    assert m2["type"] == "tool.completed"
    assert m2["result"] == "ok"


def test_map_context_compacting_to_status():
    sess = Session.__new__(Session)
    m = sess._map_agent_event_to_ws(AgentEvent(type="context.compacting", run_id="r1", conversation_id="c1"))
    assert m["type"] == "status"
    assert "Compacting" in m["text"]
    m2 = sess._map_agent_event_to_ws(AgentEvent(type="context.compacted", run_id="r1", conversation_id="c1"))
    assert m2["type"] == "status"


def test_map_status_to_status():
    sess = Session.__new__(Session)
    m = sess._map_agent_event_to_ws(AgentEvent(type="status", run_id="r1", conversation_id="c1", message="thinking..."))
    assert m["type"] == "status"
    assert m["text"] == "thinking..."


def test_only_run_completed_maps_to_done_invariant():
    """Ensure no other event type maps to done."""
    sess = Session.__new__(Session)
    for et in ["message.started", "message.delta", "message.completed", "run.failed", "run.cancelled", "tool.started", "tool.completed", "context.compacting", "context.compacted", "status", "reasoning"]:
        e = AgentEvent(type=et, run_id="r1", conversation_id="c1", message_id="m1" if et.startswith("message") else None, message="x" if et in ("message.delta", "status") else None)
        mapped = sess._map_agent_event_to_ws(e)
        if mapped is not None:
            assert mapped["type"] != "done", f"event {et} should not map to done, got {mapped}"


def test_unknown_event_returns_none():
    sess = Session.__new__(Session)
    assert sess._map_agent_event_to_ws(AgentEvent(type="unknown.event", run_id="r1", conversation_id="c1")) is None


def test_start_run_creates_agentrun_and_calls_execute(monkeypatch):
    """Session.start_run should create AgentRun with run- prefix and call coordinator.execute via thread."""
    import asyncio

    # Avoid building real backend — construct minimal Session manually
    sess = Session.__new__(Session)
    # Minimal runtime stub
    class FakeRuntime:
        def __init__(self):
            self.history = []
        def set_config(self, **kw):
            pass
    sess.runtime = FakeRuntime()
    sess.current_conv_id = "conv-42"
    sess.stop_flag = threading.Event()
    sess._resolve_attachments = lambda x: []
    sess.loop = asyncio.new_event_loop()
    # Capture emits
    emitted = []
    sess._emit = lambda payload: emitted.append(payload)
    sess.current_job_id = ""
    sess.current_task_id = ""
    sess.current_run = None
    # Fake coordinator
    calls = {}
    class FakeCoord:
        job_id = ""
        task_id = ""
        def execute(self, run, emit, runtime=None):
            calls["run"] = run
            calls["emit"] = emit
            # Emit a couple events to verify emit maps
            emit(AgentEvent(type="message.started", run_id=run.id, conversation_id=run.conversation_id, message_id="m1"))
            emit(AgentEvent(type="run.completed", run_id=run.id, conversation_id=run.conversation_id))
    sess.coordinator = FakeCoord()
    # Need _map method already present
    # Call start_run
    sess.start_run("hello world")
    # Wait for thread to finish
    if hasattr(sess, "_worker") and sess._worker is not None:
        sess._worker.join(timeout=2)
    # Assertions
    assert "run" in calls, "coordinator.execute was not called"
    run = calls["run"]
    assert isinstance(run, AgentRun)
    assert run.id.startswith("run-")
    assert run.conversation_id == "conv-42"
    assert run.goal == "hello world"
    assert run.status == AgentRunStatus.RUNNING
    assert run.context.user_input == "hello world"
    # current_run cleared after execution
    assert sess.current_run is None
    # emitted should contain mapped ws messages
    types = [p["type"] for p in emitted]
    assert "message_start" in types
    assert "done" in types
    # Ensure message.completed not mapped to done (if we emitted message.completed it would be message_end)
    sess.loop.close()


def test_start_run_handles_agent_cancelled(monkeypatch):
    import asyncio
    sess = Session.__new__(Session)
    class FakeRuntime:
        history = []
        def set_config(self, **kw): pass
    sess.runtime = FakeRuntime()
    sess.current_conv_id = "c1"
    sess.stop_flag = threading.Event()
    sess._resolve_attachments = lambda x: []
    sess.loop = asyncio.new_event_loop()
    emitted = []
    sess._emit = lambda p: emitted.append(p)
    sess.current_job_id = ""
    sess.current_task_id = ""
    sess.current_run = None
    class FakeCoord:
        job_id = ""
        task_id = ""
        def execute(self, run, emit, runtime=None):
            raise AgentCancelled("stop")
    sess.coordinator = FakeCoord()
    sess.start_run("hi")
    if sess._worker:
        sess._worker.join(timeout=2)
    assert any(p["type"] == "cancelled" for p in emitted)
    assert sess.current_run is None
    sess.loop.close()


def test_start_run_handles_expected_error():
    import asyncio
    sess = Session.__new__(Session)
    class FakeRuntime:
        history = []
        def set_config(self, **kw): pass
    sess.runtime = FakeRuntime()
    sess.current_conv_id = "c1"
    sess.stop_flag = threading.Event()
    sess._resolve_attachments = lambda x: []
    sess.loop = asyncio.new_event_loop()
    emitted = []
    sess._emit = lambda p: emitted.append(p)
    sess.current_job_id = ""
    sess.current_task_id = ""
    sess.current_run = None
    class FakeCoord:
        job_id = ""
        task_id = ""
        def execute(self, run, emit, runtime=None):
            raise ExpectedExecutionError("denied")
    sess.coordinator = FakeCoord()
    sess.start_run("hi")
    if sess._worker:
        sess._worker.join(timeout=2)
    assert any(p["type"] == "error" and "denied" in p.get("text","") for p in emitted)
    assert sess.current_run is None
    sess.loop.close()


def test_start_run_handles_unexpected_error_reraises():
    import asyncio
    sess = Session.__new__(Session)
    class FakeRuntime:
        history = []
        def set_config(self, **kw): pass
    sess.runtime = FakeRuntime()
    sess.current_conv_id = "c1"
    sess.stop_flag = threading.Event()
    sess._resolve_attachments = lambda x: []
    sess.loop = asyncio.new_event_loop()
    emitted = []
    sess._emit = lambda p: emitted.append(p)
    sess.current_job_id = ""
    sess.current_task_id = ""
    sess.current_run = None
    class FakeCoord:
        job_id = ""
        task_id = ""
        def execute(self, run, emit, runtime=None):
            raise RuntimeError("boom")
    sess.coordinator = FakeCoord()
    sess.start_run("hi")
    if sess._worker:
        sess._worker.join(timeout=2)
    # Should have emitted error with Internal error
    assert any(p["type"] == "error" and "Internal error" in p.get("text","") for p in emitted)
    assert sess.current_run is None
    sess.loop.close()
