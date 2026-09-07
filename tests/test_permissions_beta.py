"""Task 7 — Permissions end-to-end (allow/deny/timeout/cancel/persist).

Boundaries: permission boundaries authoritative; timeout ≠ deny ≠ cancel;
messages honest about what happened and whether the action was performed.
"""
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── helpers ───────────────────────────────────────────────────────────────

def _make_executor(cfg=None, perm_mode="manual"):
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
    )
    return executor


class _CancelHost:
    """Mimics Session after stop(): permission wait resolved by cancellation."""

    def __init__(self):
        self._perm_timed_out = False
        self._perm_cancelled = True
        self._perm_lock = threading.RLock()

    def callback(self, tool, args):
        return False


class _TimeoutHost:
    def __init__(self):
        self._perm_timed_out = True
        self._perm_cancelled = False
        self._perm_lock = threading.RLock()

    def callback(self, tool, args):
        return False


class _DenyHost:
    def __init__(self):
        self._perm_timed_out = False
        self._perm_cancelled = False
        self._perm_lock = threading.RLock()

    def callback(self, tool, args):
        return False


def _make_session():
    from novi.webui_server import Session
    from novi.runtime.event_bus import EventBus

    loop = MagicMock()
    loop.call_soon_threadsafe = lambda fn, *a: fn(*a) if callable(fn) else None
    backend = (MagicMock(), MagicMock(), MagicMock(), EventBus())
    with patch("novi.webui_server.build_runtime", return_value=backend):
        sess = Session(loop=loop)
    return sess


# ── 1. allow executes ─────────────────────────────────────────────────────

def test_allow_executes():
    executor = _make_executor(cfg={"permissions": {"read": "allow"}})
    result = executor.execute("read", {"path": "a.txt"})
    assert result.success is True
    assert "content of a.txt" in result.output


# ── 2. deny blocks with explanation ───────────────────────────────────────

def test_deny_blocks_with_explanation():
    executor = _make_executor(cfg={"permissions": {"bash": "deny"}})
    result = executor.execute("bash", {"command": "rm -rf /"})
    assert result.success is False
    assert "DENIED permission" in result.output
    assert "timed out" not in result.output.lower()
    assert "cancel" not in result.output.lower()


# ── 3. timeout returns expired message ────────────────────────────────────

def test_timeout_returns_expired_message():
    from novi.runtime.trace import ExecutionTrace

    executor = _make_executor(cfg={})
    trace = ExecutionTrace(user_input="test")
    host = _TimeoutHost()
    result = executor.execute("read", {"path": "a.txt"},
                              permission_callback=host.callback, trace=trace)
    assert result.success is False
    assert "timed out" in result.output.lower()
    assert "not performed" in result.output.lower()
    # Timeout trace distinct from deny/cancel
    reasons = [e.data.get("reason") for e in trace.debug_events
               if e.category == "permission_denied" and e.data]
    assert "timeout" in reasons
    assert "denied" not in reasons
    assert "cancelled" not in reasons


def test_session_timeout_emits_permission_timeout_event():
    sess = _make_session()
    emitted = []
    sess._emit = lambda p: emitted.append(p)  # type: ignore
    with patch.object(sess._perm_event, "wait", return_value=False):
        assert sess._ask_permission("read", {"path": "a.txt"}) is False
    timeouts = [p for p in emitted if p.get("type") == "permission_timeout"]
    assert len(timeouts) == 1
    requests = [p for p in emitted if p.get("type") == "permission_request"]
    assert timeouts[0]["id"] == requests[0]["id"]


# ── 4. cancel stops, distinct from deny ───────────────────────────────────

def test_cancel_stops():
    from novi.runtime.trace import ExecutionTrace

    executor = _make_executor(cfg={})
    trace = ExecutionTrace(user_input="test")
    host = _CancelHost()
    result = executor.execute("bash", {"command": "ls"},
                              permission_callback=host.callback, trace=trace)
    assert result.success is False
    assert "cancel" in result.output.lower() or "stopp" in result.output.lower()
    assert "not performed" in result.output.lower()
    assert "DENIED permission" not in result.output
    assert "timed out" not in result.output.lower()
    reasons = [e.data.get("reason") for e in trace.debug_events
               if e.category == "permission_denied" and e.data]
    assert "cancelled" in reasons


def test_session_stop_marks_cancelled_not_timeout():
    sess = _make_session()
    sess._emit = lambda p: None  # type: ignore
    # Start a permission wait in a thread, then stop it.
    outcome = {}

    def waiter():
        outcome["result"] = sess._ask_permission("bash", {"command": "ls"})

    t = threading.Thread(target=waiter)
    t.start()
    deadline = time.monotonic() + 5
    while sess._perm_request_id == "" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sess._perm_request_id != ""  # request registered, still waiting
    sess.stop()
    t.join(timeout=5)
    assert outcome.get("result") is False
    assert sess._perm_cancelled is True
    assert sess._perm_timed_out is False


def test_answer_permission_after_stop_stays_cancelled():
    sess = _make_session()
    sess._emit = lambda p: None  # type: ignore
    with patch.object(sess._perm_event, "wait", return_value=True):
        # Simulate stop() racing the wait: cancelled set, wait released.
        orig_wait = sess._perm_event.wait

        def racy_wait(timeout=None):
            sess.stop()
            return True

        with patch.object(sess._perm_event, "wait", side_effect=racy_wait):
            assert sess._ask_permission("read", {"path": "a.txt"}) is False
            assert sess._perm_cancelled is True


# ── 5. persist roundtrip ──────────────────────────────────────────────────

def test_persist_roundtrip(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HOMEDRIVE", str(tmp_path))
    monkeypatch.setenv("HOMEPATH", "")
    import novi.configuration.bootstrap as boot
    monkeypatch.setattr(boot, "CONFIG_PATH", tmp_path / ".novi" / "config.toml")
    monkeypatch.setattr(boot, "_configuration", None)
    import novi.webui_server as ws
    ws._shared_backend = None
    from novi.webui_server import create_app
    client = TestClient(create_app(cfg={}))

    resp = client.patch("/api/configuration", json={"permissions.write_file": "allow"})
    assert resp.json()[0]["ok"] is True
    snap = client.get("/api/configuration").json()
    assert snap["permissions"]["write_file"] == "allow"

    # Persisted value affects the next permission check: write_file allowed,
    # no interactive callback consulted.
    from novi.runtime.permissions import PermissionResolver
    resolver = PermissionResolver({"permissions": snap["permissions"]}, auto=False)
    assert resolver.resolve("write_file", {"path": "x"}) == "allow"

    def _boom(tool, args):
        raise AssertionError("callback must not run for allowed tool")

    executor = _make_executor(cfg={"permissions": snap["permissions"]})
    result = executor.execute("write_file", {"path": "x", "content": "y"},
                              permission_callback=_boom)
    assert result.success is True


# ── frontend honesty gates ────────────────────────────────────────────────

def test_frontend_cancel_and_expired_honesty():
    prompt = Path("novi/webui/src/components/common/PermissionPrompt.tsx")
    text = prompt.read_text(encoding="utf-8")
    # Cancel affordance distinct from deny
    assert "onCancel" in text
    assert "Cancel" in text
    # Timeout-expired message honest about non-performance
    assert "expired" in text.lower()
    assert "not performed" in text


def test_frontend_handles_permission_timeout_event():
    hook = Path("novi/webui/src/hooks/useNoviChat.ts")
    text = hook.read_text(encoding="utf-8")
    assert "permission_timeout" in text
    assert "expired" in text.lower()
    assert "not performed" in text


def test_permission_defs_have_no_dead_keys():
    import re
    from novi.tools import TOOL_REGISTRY
    from novi.runtime.mcp_permissions import classify_operation

    consts = Path("novi/webui/src/components/settings/constants.tsx")
    text = consts.read_text(encoding="utf-8")
    keys = set(re.findall(r"key:\s*'([^']+)'", text))
    reachable = {"read", "write", "delete", "execute"} | set(TOOL_REGISTRY.keys())
    # classify_operation only ever yields read/write/delete/execute; any other
    # key must name a real tool (exact-name match) or it is a dead control.
    dead = {k for k in keys if k not in reachable}
    assert not dead, f"PERMISSION_DEFS keys with no backing tool/classification: {dead}"
    # Spot-check the classifier covers what remains
    assert classify_operation("some_unknown_tool_xyz") == "execute"
