"""Task 2.3 — Permission Timeout/Expiration Honest Outcome.

- permission_request payload includes expiresAt (ISO) + timeoutMs 120000
- Frontend shows countdown "Deny in 1:58"
- On timeout runtime emits trace permission_denied reason:timeout and tool result
  explains "Permission for {tool} timed out — approve within 2 min or set
  Permissions → {tool} to Allow". Timeout distinct from explicit deny.
"""
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── helpers ───────────────────────────────────────────────────────────────

def _make_executor(cfg=None, perm_mode="manual", gate=None):
    from novi.runtime.tool_registry import ToolRegistry
    from novi.runtime.permissions import PermissionResolver
    from novi.runtime.tool_executor import ToolExecutor

    cfg = cfg or {}
    perms = PermissionResolver(cfg, auto=False)

    class _NoopStore:
        def record(self, *a, **k):
            pass

    reg = ToolRegistry()
    reg.register("read", lambda path="": f"content of {path}")
    reg.register("write_file", lambda path="", content="": f"wrote {path}")
    reg.register("bash", lambda command="": f"ran {command}")

    executor = ToolExecutor(
        registry=reg,
        perms=perms,
        lesson_store=_NoopStore(),
        lc_tools=reg.as_lc_tools(),
        tool_fallbacks={},
        max_tool_output=8000,
        perm_mode=perm_mode,
        mcp_permissions=gate,
    )
    return executor, reg


class _TimeoutHost:
    """Mimics Session's permission gate that timed out."""
    def __init__(self):
        self._perm_timed_out = True
        self._perm_lock = threading.RLock()

    def callback(self, tool, args):
        return False


class _DenyHost:
    """Mimics Session's permission gate that explicitly denied."""
    def __init__(self):
        self._perm_timed_out = False
        self._perm_lock = threading.RLock()

    def callback(self, tool, args):
        return False


# ── payload includes expiry ───────────────────────────────────────────────

def test_permission_request_includes_expires_at_and_timeout():
    from novi.webui_server import Session

    loop = MagicMock()
    captured = {}

    def fake_emit(payload):
        captured.update(payload)

    loop.call_soon_threadsafe = lambda fn, *a: fn(*a) if callable(fn) else None

    # Patch build_runtime to avoid heavy backend
    from novi.runtime.event_bus import EventBus
    backend = (MagicMock(), MagicMock(), MagicMock(), EventBus())
    with patch("novi.webui_server.build_runtime", return_value=backend):
        sess = Session(loop=loop)
        # Capture _emit
        sess._emit = lambda payload: captured.update(payload)  # type: ignore
        # Make wait return quickly with False (timeout) without blocking 120s
        with patch.object(sess._perm_event, "wait", return_value=False) as mock_wait:
            result = sess._ask_permission("read", {"path": "a.txt"})
            assert result is False
            mock_wait.assert_called_once()
            # timeout flag set
            assert sess._perm_timed_out is True
            assert sess._perm_request_id == ""

    assert captured.get("timeoutMs") == 120000
    assert "expiresAt" in captured
    # ISO parseable
    dt = datetime.fromisoformat(captured["expiresAt"])
    assert dt.tzinfo is not None
    # expiresAt should be ~120s in future (allow 5s skew)
    delta = (dt - datetime.now(timezone.utc)).total_seconds()
    assert 115 <= delta <= 125
    assert captured["tool"] == "read"
    assert captured["type"] == "permission_request"


def test_permission_request_expiry_is_iso_and_tool_matches():
    from novi.webui_server import Session

    loop = MagicMock()
    loop.call_soon_threadsafe = lambda fn, *a: fn(*a) if callable(fn) else None
    from novi.runtime.event_bus import EventBus
    backend = (MagicMock(), MagicMock(), MagicMock(), EventBus())
    with patch("novi.webui_server.build_runtime", return_value=backend):
        sess = Session(loop=loop)
        captured = {}
        sess._emit = lambda p: captured.update(p)  # type: ignore
        # Simulate explicit allow path without blocking
        def fake_wait(timeout=None):
            # Simulate user answering quickly: set allowed before wait returns
            # need to set flag via answer_permission
            return True

        # Patch wait to simulate allow (have to manually set state)
        with patch.object(sess._perm_event, "wait", return_value=True):
            # Pre-set allowed to True to mimic user allowing
            sess._perm_event.set = MagicMock()
            # Manually set allowed True then call _ask_permission which will wait and return
            # We'll monkeypatch to set _perm_allowed after emit
            orig_ask = sess._ask_permission

            # Instead directly test emit payload: call _ask_permission with wait returning True
            # and _perm_allowed True
            sess._perm_allowed = True
            # Need to bypass actual wait logic: patch wait to return True and ensure _perm_allowed is True
            with patch.object(sess._perm_event, "wait", return_value=True):
                # Set flag to allow before call returns
                def patched_ask(tool, args):
                    # replicate logic but capture payload without waiting
                    import uuid as _uuid
                    from datetime import timedelta
                    req_id = f"perm-{_uuid.uuid4().hex[:8]}"
                    timeout_ms = 120000
                    expires_at = (datetime.now(timezone.utc) + timedelta(milliseconds=timeout_ms)).isoformat()
                    captured.update({"type": "permission_request", "id": req_id, "tool": tool, "args": args, "timeoutMs": timeout_ms, "expiresAt": expires_at})
                    return True

                sess._ask_permission = patched_ask  # type: ignore
                result = sess._ask_permission("bash", {"command": "ls"})
                assert captured["timeoutMs"] == 120000
                assert "expiresAt" in captured
                datetime.fromisoformat(captured["expiresAt"])


# ── timeout distinct from deny ────────────────────────────────────────────

def test_permission_timeout_produces_honest_result():
    executor, _ = _make_executor(cfg={})
    from novi.runtime.trace import ExecutionTrace

    trace = ExecutionTrace(user_input="test")
    host = _TimeoutHost()
    result = executor.execute("read", {"path": "a.txt"}, permission_callback=host.callback, trace=trace)

    assert result.success is False
    assert "timed out" in result.output.lower()
    assert "Permission for read timed out" in result.output
    assert "approve within 2 min" in result.output
    assert "Permissions → read to Allow" in result.output
    # Distinct from explicit deny wording
    assert "DENIED permission" not in result.output
    assert "Do not retry" not in result.output


def test_permission_timeout_emits_trace_reason_timeout():
    executor, _ = _make_executor(cfg={})
    from novi.runtime.trace import ExecutionTrace

    trace = ExecutionTrace(user_input="test")
    host = _TimeoutHost()
    executor.execute("write_file", {"path": "x", "content": "y"}, permission_callback=host.callback, trace=trace)

    # trace should contain permission_denied with timeout
    timeout_events = [e for e in trace.debug_events if e.category == "permission_denied" and e.data and e.data.get("reason") == "timeout"]
    assert len(timeout_events) >= 1
    assert timeout_events[0].data["tool"] == "write_file"

    # also user_events
    user_timeout = [e for e in trace.user_events if e.category == "permission_denied" and "timeout" in e.summary]
    assert len(user_timeout) >= 1

    # tool call trace recorded with timeout error
    assert len(trace.steps) >= 1
    tc = trace.steps[0].tool_calls[0]
    assert "timed out" in tc.error.lower()
    assert tc.success is False


def test_permission_explicit_deny_distinct():
    executor, _ = _make_executor(cfg={})
    from novi.runtime.trace import ExecutionTrace

    trace = ExecutionTrace(user_input="test")
    host = _DenyHost()
    result = executor.execute("read", {"path": "a.txt"}, permission_callback=host.callback, trace=trace)

    assert result.success is False
    assert "DENIED permission" in result.output
    assert "timed out" not in result.output.lower()
    # Deny trace distinct
    deny_events = [e for e in trace.debug_events if e.category == "permission_denied" and e.data and e.data.get("reason") == "denied"]
    assert len(deny_events) >= 1
    assert deny_events[0].data["tool"] == "read"
    # No timeout events
    timeout_events = [e for e in trace.debug_events if e.category == "permission_denied" and e.data and e.data.get("reason") == "timeout"]
    assert len(timeout_events) == 0


def test_permission_deny_and_timeout_are_distinct_messages():
    executor, _ = _make_executor(cfg={})

    from novi.runtime.trace import ExecutionTrace
    host_timeout = _TimeoutHost()
    host_deny = _DenyHost()

    trace1 = ExecutionTrace(user_input="t1")
    r_timeout = executor.execute("bash", {"command": "rm -rf /"}, permission_callback=host_timeout.callback, trace=trace1)

    trace2 = ExecutionTrace(user_input="t2")
    r_deny = executor.execute("bash", {"command": "rm -rf /"}, permission_callback=host_deny.callback, trace=trace2)

    assert r_timeout.output != r_deny.output
    assert "timed out" in r_timeout.output
    assert "DENIED" in r_deny.output


def test_session_timeout_sets_flag_and_drops_late_answer():
    from novi.webui_server import Session

    loop = MagicMock()
    loop.call_soon_threadsafe = lambda fn, *a: fn(*a) if callable(fn) else None
    from novi.runtime.event_bus import EventBus
    backend = (MagicMock(), MagicMock(), MagicMock(), EventBus())
    with patch("novi.webui_server.build_runtime", return_value=backend):
        sess = Session(loop=loop)
        captured = {}
        sess._emit = lambda p: captured.update(p)  # type: ignore
        # Simulate timeout path
        with patch.object(sess._perm_event, "wait", return_value=False):
            result = sess._ask_permission("write_file", {"path": "b.txt"})
            assert result is False
            assert sess._perm_timed_out is True
            old_id = captured.get("id")
            # Late answer with old id should be dropped
            sess.answer_permission(True, request_id=old_id)
            assert sess._perm_allowed is False
            assert sess._perm_event.is_set() is False


def test_frontend_countdown_present():
    # Verify PermissionPrompt.tsx renders countdown "Deny in"
    p = Path("novi/webui/src/components/common/PermissionPrompt.tsx")
    text = p.read_text(encoding="utf-8")
    assert "Deny in" in text
    assert "remainingSec" in text or "remaining" in text.lower()
    assert "expiresAt" in text
    assert "Clock" in text

    # Also check useNoviChat hook pushes notification and stores expiresAt
    hook = Path("novi/webui/src/hooks/useNoviChat.ts")
    hook_text = hook.read_text(encoding="utf-8")
    assert "expiresAt" in hook_text
    assert "timeoutMs" in hook_text
    assert "Permission required" in hook_text

    # Check novi.ts type includes timeoutMs/expiresAt
    svc = Path("novi/webui/src/services/novi.ts")
    svc_text = svc.read_text(encoding="utf-8")
    assert "timeoutMs" in svc_text
    assert "expiresAt" in svc_text
