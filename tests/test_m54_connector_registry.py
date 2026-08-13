"""M5.4 — Connector Registry + MCP server permission consumption.

Covers the M5.4 acceptance tests:

Connector Registry
  1. register / lookup / enumerate connectors
  2. unregister
  3. duplicate registration raises (same id), replace=True re-registers
  4. unknown connector lookup (get -> None, require -> raise)
  5. MCP + Telegram registration
  6. status retrieval through the registry (safe, relayed from connector)
  7. no credential leakage through status / describe surfaces

Configuration authority
  8. registry-derived state never creates a second persistence authority
  9. runtime state is never persisted; configuration stays authoritative

MCP
  10. MCP represented by the registry; existing lifecycle still works
  11. enabled/disabled behavior (M5.3) remains intact behind the registry
  12. MCP runtime/session state is not persisted through the registry

Telegram
  13. Telegram represented by the registry; existing TelegramLifecycle still works
  14. enabled/disabled behavior (M5.3) remains intact

Permissions (M5.4 gap: mcp.servers.<name>.permissions is now consumed)
  15. allow behavior works (explicit allowed permission does not deny)
  16. deny behavior works (explicitly forbidden operation is denied)
  17. ask behavior still reaches the existing permission callback path
  18. MCP permissions do not bypass ToolExecutor (deny surfaces via ToolResult,
      the underlying fn is never invoked)
  19. existing global/tool-risk permissions remain intact
"""

import threading

import pytest

from cozmo.configuration.manager import Configuration
from cozmo.configuration.bootstrap import build_registry as _build_registry
from cozmo.connectors import (
    ConnectorAlreadyRegisteredError,
    ConnectorDefinition,
    ConnectorRegistry,
    UnknownConnectorError,
)
from cozmo.runtime.mcp_permissions import MCPPermissionGate, classify_operation
from cozmo.runtime.tool_registry import ToolRegistry


# ── helper: real PermissionResolver-backed executor ─────────────────────────


def _make_executor(registry, cfg, *, gate=None, lesson_dir=None, perm_mode="manual"):
    from cozmo.runtime.permissions import PermissionResolver
    from cozmo.runtime.lessons import LessonStore
    from cozmo.runtime.tool_executor import ToolExecutor

    perms = PermissionResolver(cfg, auto=False)
    lesson_store = LessonStore(persist_dir=str(lesson_dir)) if lesson_dir else object()
    if lesson_dir is None:
        class _NoopStore:
            def record(self, *a, **k):
                pass
        lesson_store = _NoopStore()
    executor = ToolExecutor(
        registry=registry,
        perms=perms,
        lesson_store=lesson_store,
        lc_tools=registry.as_lc_tools(),
        tool_fallbacks={},
        max_tool_output=8000,
        perm_mode=perm_mode,
        mcp_permissions=gate,
    )
    return executor


# ═══════════════════════════════════════════════════════════════════════════
# Connector Registry — core operations
# ═══════════════════════════════════════════════════════════════════════════

def _simple_status():
    return {"enabled": True, "state": "running"}


def test_register_lookup_enumerate():
    reg = ConnectorRegistry()
    reg.register(ConnectorDefinition(
        connector_id="mcp", connector_type="mcp", label="MCP",
        enabled=True, status_fn=_simple_status))

    assert reg.has("mcp") is True
    conn = reg.get("mcp")
    assert conn.connector_id == "mcp"
    assert conn.connector_type == "mcp"
    assert conn.enabled is True
    assert len(reg.list()) == 1
    assert reg.types() == ["mcp"]


def test_enumerate_insertion_order():
    reg = ConnectorRegistry()
    reg.register(ConnectorDefinition("mcp", "mcp", label="MCP"))
    reg.register(ConnectorDefinition("telegram", "telegram", label="Telegram"))
    assert [c.connector_id for c in reg.list()] == ["mcp", "telegram"]
    assert reg.types() == ["mcp", "telegram"]


def test_unregister():
    reg = ConnectorRegistry()
    reg.register(ConnectorDefinition("mcp", "mcp"))
    reg.unregister("mcp")
    assert reg.has("mcp") is False
    assert reg.list() == []
    reg.unregister("never_registered")  # no-op


def test_duplicate_registration_raises():
    reg = ConnectorRegistry()
    reg.register(ConnectorDefinition("mcp", "mcp"))
    with pytest.raises(ConnectorAlreadyRegisteredError):
        reg.register(ConnectorDefinition("mcp", "mcp"))
    # replace=True is the explicit idempotent override
    reg.register(ConnectorDefinition("mcp", "mcp", label="MCP v2"), replace=True)
    assert reg.get("mcp").label == "MCP v2"


def test_unknown_lookup():
    reg = ConnectorRegistry()
    assert reg.get("ghost") is None
    with pytest.raises(UnknownConnectorError):
        reg.require("ghost")


def test_status_relayed_from_connector():
    reg = ConnectorRegistry()
    reg.register(ConnectorDefinition("mcp", "mcp", status_fn=_simple_status))
    assert reg.statuses()["mcp"] == {"enabled": True, "state": "running"}
    assert reg.get("mcp").status()["state"] == "running"


def test_status_without_fn_and_broken_fn_fallback():
    reg = ConnectorRegistry()
    reg.register(ConnectorDefinition("telegram", "telegram", enabled=False))
    assert reg.get("telegram").status() == {"enabled": False}

    def broken():
        raise RuntimeError("boom")

    reg.register(ConnectorDefinition("mcp", "mcp", enabled=True, status_fn=broken),
                 replace=True)
    assert reg.get("mcp").status() == {"enabled": True, "state": "error"}


def test_status_never_leaks_credentials():
    reg = ConnectorRegistry()
    mcp_def = ConnectorDefinition(
        "mcp", "mcp", enabled=True,
        status_fn=lambda: {"enabled": True, "state": "running", "servers": {}},
        identity={"servers": ["github"]},
    )
    reg.register(mcp_def)
    # A malicious/buggy status fn must not smuggle raw config through... but the
    # registry contract is that identity/status are SECRET-FREE. Assert the
    # relayed payloads we seed carry no secret and describe() only exposes the
    # registered (safe) fields.
    text = repr(reg.statuses()) + repr(reg.get("mcp").describe())
    assert "ghp_" not in text
    assert "bot_token" not in text
    assert "TOKEN" not in text
    assert "env" not in text


def test_identity_update_is_runtime_only():
    reg = ConnectorRegistry()
    reg.register(ConnectorDefinition("mcp", "mcp", enabled=False,
                                  identity={"servers": []}))
    reg.get("mcp").update(enabled=True, identity={"servers": ["a", "b"]})
    assert reg.get("mcp").enabled is True
    assert reg.get("mcp").identity == {"servers": ["a", "b"]}


# ═══════════════════════════════════════════════════════════════════════════
# Configuration authority — no second persistence authority
# ═══════════════════════════════════════════════════════════════════════════

def test_registry_never_writes_config(tmp_path):
    reg = _build_registry()
    cfg = Configuration(reg, tmp_path / "cfg.toml")
    cfg.initialize()
    cfg.set("mcp.enabled", True, by="test")
    cfg.store.write(cfg.state.as_dict())
    before = (tmp_path / "cfg.toml").read_bytes()

    connectors = ConnectorRegistry()
    connectors.register(ConnectorDefinition(
        "mcp", "mcp", enabled=True,
        identity={"servers": list((cfg.get("mcp", {}).get("servers", {}) or {}).keys())},
    ))
    connectors.get("mcp").update(enabled=False, identity={"servers": ["x"]})
    _ = connectors.statuses()
    connectors.unregister("mcp")

    # Registry operations are derived reads only — the file is untouched.
    assert (tmp_path / "cfg.toml").read_bytes() == before


def test_configuration_remains_authoritative(tmp_path):
    reg = _build_registry()
    cfg = Configuration(reg, tmp_path / "cfg.toml")
    cfg.initialize()

    connectors = ConnectorRegistry()
    connectors.register(ConnectorDefinition(
        "mcp", "mcp", enabled=False,
        identity={"servers": []},
    ))
    # Simulate the composition-root refresh-from-config flow.
    cfg.set("mcp.enabled", False, by="test")
    cfg.set("telegram.enabled", True, by="test")
    snap = cfg.snapshot()
    connectors.get("mcp").update(
        enabled=bool(snap["mcp"].get("enabled", True)),
        identity={"servers": sorted(snap["mcp"].get("servers", {}).keys())},
    )
    assert connectors.get("mcp").enabled is False
    # intent endures through the framework, not the registry


def test_runtime_state_not_persisted():
    # A registry starts empty; registration is in-memory only. Recreating the
    # registry (fresh process) has zero connectors.
    reg = ConnectorRegistry()
    reg.register(ConnectorDefinition("mcp", "mcp"))
    assert "mcp" in [c.connector_id for c in reg.list()]
    fresh = ConnectorRegistry()
    assert fresh.list() == []


# ═══════════════════════════════════════════════════════════════════════════
# MCP registration — wraps existing lifecycle, keeps M5.3 behavior
# ═══════════════════════════════════════════════════════════════════════════

class FakeHost:
    instances: list["FakeHost"] = []

    def __init__(self, config):
        self.name = next(iter(config.get("servers", {})), None)
        self.connected = False

    async def connect(self, server_configs=None):
        self.connected = True

    async def get_tool_wrappers(self):
        async def wrapper(**kwargs):
            return "ok"
        wrapper.__name__ = f"{self.name}_tool"
        wrapper.__doc__ = f"MCP tool from {self.name}"
        return [wrapper]

    async def disconnect(self):
        self.connected = False

    @classmethod
    def reset(cls):
        cls.instances = []


@pytest.fixture
def mcp_manager(monkeypatch):
    FakeHost.reset()
    from cozmo.runtime.providers import mcp as mcp_mod

    monkeypatch.setattr(mcp_mod, "MCPHost", FakeHost)
    registry = ToolRegistry()
    manager = mcp_mod.MCPManager(registry)
    yield manager, registry
    manager.stop()


def _mcp_cfg(enabled=True):
    return {"mcp": {"enabled": enabled, "servers": {
        "github": {"command": "npx", "env": {"GITHUB_TOKEN": "ghp_TRACE_ONLY"}}}}}


def test_mcp_represented_by_registry_and_lifecycle_works(mcp_manager):
    manager, tool_registry = mcp_manager
    connectors = ConnectorRegistry()
    connectors.register(ConnectorDefinition(
        "mcp", "mcp", label="Model Context Protocol",
        enabled=True, status_fn=manager.get_lifecycle,
        identity={"servers": ["github"]},
    ))

    manager.start(_mcp_cfg(enabled=True))
    assert "github" in manager._hosts
    assert [t.name for t in tool_registry.list()] == ["github_tool"]
    status = connectors.get("mcp").status()
    assert status["running"] is True
    assert status["enabled"] is True


def test_mcp_enable_disable_intact_behind_registry(mcp_manager):
    manager, tool_registry = mcp_manager
    connectors = ConnectorRegistry()
    connectors.register(ConnectorDefinition(
        "mcp", "mcp", status_fn=manager.get_lifecycle, identity={"servers": []}))

    manager.start(_mcp_cfg(enabled=True))
    manager.refresh_from_config(_mcp_cfg(enabled=False))
    connectors.get("mcp").update(enabled=False)
    assert connectors.get("mcp").status()["state"] == "stopped"
    assert manager._hosts == {}
    assert tool_registry.list() == []

    manager.refresh_from_config(_mcp_cfg(enabled=True))
    connectors.get("mcp").update(enabled=True)
    assert connectors.get("mcp").status()["running"] is True
    assert [t.name for t in tool_registry.list()] == ["github_tool"]


def test_mcp_runtime_state_not_persisted_through_registry(mcp_manager, tmp_path):
    manager, _ = mcp_manager
    connectors = ConnectorRegistry()
    connectors.register(ConnectorDefinition(
        "mcp", "mcp", status_fn=manager.get_lifecycle, identity={"servers": []}))

    manager.start(_mcp_cfg(enabled=True))
    before = sorted(p.name for p in tmp_path.rglob("*")) if tmp_path.exists() else []
    connectors.get("mcp").update(enabled=True, identity={"servers": ["github"]})
    _ = connectors.statuses()
    manager.stop()
    connectors.unregister("mcp")
    after = sorted(p.name for p in tmp_path.rglob("*")) if tmp_path.exists() else []
    assert after == before


# ═══════════════════════════════════════════════════════════════════════════
# Telegram registration — wraps existing TelegramLifecycle, keeps M5.3 behavior
# ═══════════════════════════════════════════════════════════════════════════

class FakeBot:
    def __init__(self, token="", allowed=None):
        self.token = token
        self.start_calls = 0
        self.stop_calls = 0

    def start(self, **kwargs):
        self.start_calls += 1

    def stop(self):
        self.stop_calls += 1


class FakeBotFactory:
    def __init__(self):
        self.built = []

    def __call__(self, ctx, token, *, allowed_chat_ids=()):
        bot = FakeBot(token=token)
        self.built.append(bot)
        return bot


def test_telegram_represented_by_registry_and_lifecycle_works():
    from cozmo.services.telegram import TelegramLifecycle

    factory = FakeBotFactory()
    life = TelegramLifecycle(object(), bot_factory=factory)
    connectors = ConnectorRegistry()
    connectors.register(ConnectorDefinition(
        "telegram", "telegram", label="Telegram",
        enabled=False, status_fn=life.get_status))

    cfg_on = {"telegram": {"enabled": True, "bot_token": "TOK_ONLY_IN_CONFIG"}}
    life.apply(cfg_on)
    connectors.get("telegram").update(enabled=True)
    status = connectors.get("telegram").status()
    assert status["running"] is True
    assert status["state"] == "running"
    assert len(factory.built) == 1


def test_telegram_enable_disable_intact_behind_registry():
    from cozmo.services.telegram import TelegramLifecycle

    factory = FakeBotFactory()
    life = TelegramLifecycle(object(), bot_factory=factory)
    connectors = ConnectorRegistry()
    connectors.register(ConnectorDefinition(
        "telegram", "telegram", status_fn=life.get_status))

    life.apply({"telegram": {"enabled": True, "bot_token": "TOK"}})
    connectors.get("telegram").update(enabled=True)
    assert connectors.get("telegram").status()["running"] is True

    life.apply({"telegram": {"enabled": False}})
    connectors.get("telegram").update(enabled=False)
    assert connectors.get("telegram").status()["running"] is False
    assert connectors.get("telegram").status()["state"] == "stopped"


def test_telegram_status_never_exposes_token():
    from cozmo.services.telegram import TelegramLifecycle

    factory = FakeBotFactory()
    life = TelegramLifecycle(object(), bot_factory=factory)
    connectors = ConnectorRegistry()
    connectors.register(ConnectorDefinition(
        "telegram", "telegram", status_fn=life.get_status))

    life.apply({"telegram": {"enabled": True, "bot_token": "SUPER_SECRET_123"}})
    text = repr(connectors.statuses()) + repr(connectors.get("telegram").describe())
    assert "SUPER_SECRET_123" not in text
    assert "bot_token" not in text


# ═══════════════════════════════════════════════════════════════════════════
# MCP server permissions — gate semantics
# ═══════════════════════════════════════════════════════════════════════════

def test_classify_operation():
    assert classify_operation("github_read_repo") == "read"
    assert classify_operation("github_create_issue") == "write"
    assert classify_operation("filesystem_delete_file") == "delete"
    assert classify_operation("sqlite_query") == "read"
    assert classify_operation("list_directory") == "read"


def test_gate_allow_behavior():
    gate = MCPPermissionGate({"servers": {
        "github": {"permissions": {"read": True}},
    }})
    # explicitly allowed operation: not denied -> defer to existing engine
    assert gate.decision("github_list_issues") is None
    # an unlisted operation on the same server: not denied either
    assert gate.decision("github_weird_thing") is None
    assert gate.server_for("github_list_issues") == "github"


def test_gate_deny_behavior():
    gate = MCPPermissionGate({"servers": {
        "github": {"permissions": {"write": False, "read": False}},
    }})
    assert gate.decision("github_create_issue") == "deny"
    assert gate.decision("github_read_repo") == "deny"
    # delete/execute are listed as allowed by absence -> not denied
    assert gate.decision("github_purge_everything") is None
    assert gate.decision("github_run_thing") is None


def test_gate_exact_tool_name_key_wins():
    gate = MCPPermissionGate({"servers": {
        "github": {"permissions": {"github_secret_tool": False, "write": True}},
    }})
    assert gate.decision("github_secret_tool") == "deny"
    # write is allowed, so a plain write tool defers
    assert gate.decision("github_create_issue") is None


def test_gate_unknown_server_and_empty_config():
    gate = MCPPermissionGate({})
    assert gate.decision("github_create_issue") is None
    gate = MCPPermissionGate({"servers": {"other": {"permissions": {"write": False}}}})
    assert gate.decision("github_create_issue") is None
    assert gate.policies() == {"other": {"write": False}}


def test_gate_config_change_is_derived_stateless():
    gate = MCPPermissionGate({"servers": {"srv": {"permissions": {"write": False}}}})
    assert gate.decision("srv_create") == "deny"
    gate.refresh({"servers": {"srv": {"permissions": {"write": True}}}})
    assert gate.decision("srv_create") is None
    gate.refresh({})
    assert gate.policies() == {}


# ═══════════════════════════════════════════════════════════════════════════
# MCP server permissions — ToolExecutor consumption
# ═══════════════════════════════════════════════════════════════════════════

def _mcp_registry():
    calls = []

    def github_create_issue(title: str, body: str = ""):
        calls.append(("github_create_issue", title))
        return f"created {title}"

    def github_list_issues():
        calls.append(("github_list_issues",))
        return "issue list"

    def github_purge_everything():
        calls.append(("github_purge_everything",))
        return "purged"

    def write_file(path: str, content: str):
        calls.append(("write_file", path))
        return "wrote"

    reg = ToolRegistry()
    reg.register("github_create_issue", github_create_issue, "Create a GitHub issue")
    reg.register("github_list_issues", github_list_issues, "List GitHub issues")
    reg.register("github_purge_everything", github_purge_everything, "Purge GitHub")
    reg.register("write_file", write_file, "Write a file")
    return reg, calls


def test_permission_deny_blocks_mcp_tool_via_executor(tmp_path):
    # server denies write -> github_create_issue blocked through ToolExecutor
    gate = MCPPermissionGate({"servers": {"github": {"permissions": {"write": False}}}})
    reg, calls = _mcp_registry()
    executor = _make_executor(reg, {}, gate=gate, lesson_dir=tmp_path)

    result = executor.execute("github_create_issue", {"title": "x"})
    assert result.success is False
    assert "DENIED permission" in result.output
    assert calls == []  # the tool was NEVER invoked


def test_permission_allow_reaches_existing_path(tmp_path):
    # server allows everything -> the gate defers; with no config rules the tool
    # is MEDIUM risk -> manual mode -> callback asked (existing permission path)
    gate = MCPPermissionGate({"servers": {"github": {"permissions": {"execute": True}}}})
    reg, calls = _mcp_registry()
    executor = _make_executor(reg, {}, gate=gate, lesson_dir=tmp_path)

    asked = []

    def cb(tool, args):
        asked.append(tool)
        return True

    result = executor.execute("github_list_issues", {}, permission_callback=cb)
    assert result.success is True
    assert asked == ["github_list_issues"]
    assert calls == [("github_list_issues",)]


def test_permission_denied_by_user_still_fails_through_callback(tmp_path):
    # Whatever the server allows, the existing callback stays the authoritative
    # ask surface: user denies -> tool blocked.
    gate = MCPPermissionGate({})
    reg, calls = _mcp_registry()
    executor = _make_executor(reg, {}, gate=gate, lesson_dir=tmp_path)

    result = executor.execute("github_purge_everything", {},
                              permission_callback=lambda t, a: False)
    assert result.success is False
    assert "DENIED permission" in result.output
    assert calls == []


def test_mcp_permissions_do_not_bypass_tool_executor(tmp_path):
    # The denial is decided INSIDE ToolExecutor._check_permission and surfaces
    # as a normal ToolResult — there is no external gate that skips the
    # executor pipeline for MCP tools.
    gate = MCPPermissionGate({"servers": {"github": {"permissions": {"write": False}}}})
    reg, calls = _mcp_registry()
    executor = _make_executor(reg, {}, gate=gate, lesson_dir=tmp_path)
    result = executor.execute("github_create_issue", {"title": "no"})
    assert isinstance(result, object)
    assert result.success is False
    assert calls == []
    assert "DENIED permission" in result.output


def test_global_permissions_stay_intact_with_gate_present(tmp_path):
    # A non-MCP tool with a global deny still blocked even when the gate is
    # present (the gate only denies, never allows past existing rules).
    cfg = {"permissions": {"write_file": "deny"}}
    gate = MCPPermissionGate({"servers": {"github": {"permissions": {}}}})
    reg, calls = _mcp_registry()
    executor = _make_executor(reg, cfg, gate=gate, lesson_dir=tmp_path)
    result = executor.execute("write_file", {"path": "/x", "content": "y"})
    assert result.success is False
    assert "DENIED permission" in result.output
    assert calls == []


def test_gate_absent_keeps_existing_behavior(tmp_path):
    # No gate at all -> the pre-M5.4 path: explicit config deny wins.
    cfg = {"permissions": {"write_file": "deny"}}
    reg, calls = _mcp_registry()
    executor = _make_executor(reg, cfg, gate=None, lesson_dir=tmp_path)
    result = executor.execute("write_file", {"path": "/x", "content": "y"})
    assert result.success is False
    assert calls == []
    # an allowed low-risk tool still works
    result2 = executor.execute("github_list_issues", {},
                               permission_callback=lambda t, a: True)
    assert result2.success is True


def test_deny_modes_and_session_rules_unaffected(tmp_path):
    # bypass mode ignores the gate deny (existing mode semantics win) and the
    # executor/limit path is untouched.
    gate = MCPPermissionGate({"servers": {"github": {"permissions": {"write": False}}}})
    reg, calls = _mcp_registry()
    executor = _make_executor(reg, {}, gate=gate, lesson_dir=tmp_path, perm_mode="bypass")
    result = executor.execute("github_create_issue", {"title": "ok"})
    assert result.success is True
    assert calls == [("github_create_issue", "ok")]


# ═══════════════════════════════════════════════════════════════════════════
# WebUI — generic connector status endpoint
# ═══════════════════════════════════════════════════════════════════════════

def test_webui_connectors_status_endpoint(monkeypatch):
    """``GET /api/connectors/status`` relays the registry's secret-free statuses."""
    import cozmo.webui_server as ws
    from fastapi.testclient import TestClient

    registry = ConnectorRegistry()
    registry.register(ConnectorDefinition(
        "mcp", "mcp", enabled=True,
        status_fn=lambda: {"enabled": True, "state": "running", "servers": {}},
        identity={"servers": ["github"]},
    ))
    registry.register(ConnectorDefinition(
        "telegram", "telegram", enabled=False,
        status_fn=lambda: {"enabled": False, "state": "stopped"},
    ))
    ws._shared_backend = {"connectors": registry}
    try:
        client = TestClient(ws.create_app(cfg={}))
        resp = client.get("/api/connectors/status")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"mcp", "telegram"}
        assert body["mcp"]["state"] == "running"
        assert body["telegram"]["state"] == "stopped"
        assert "ghp_" not in repr(body)
        assert "env" not in repr(body)
        assert "bot_token" not in repr(body)
    finally:
        ws._shared_backend = None


def test_webui_connectors_status_empty_when_no_backend(monkeypatch):
    import cozmo.webui_server as ws
    from fastapi.testclient import TestClient

    ws._shared_backend = None
    try:
        client = TestClient(ws.create_app(cfg={}))
        assert client.get("/api/connectors/status").json() == {}
    finally:
        ws._shared_backend = None