"""M5.3 — Honest MCP enabled flags + live connector lifecycle (Goal A/B/C).

Makes ``mcp.enabled`` and per-server ``mcp.servers.<name>.enabled`` actually
gate the runtime: connections, tool registration, loop lifecycle, and
shutdown. Uses a fake MCP host — no external MCP servers required.

Covers the M5.3 MCP acceptance tests:
  1. ``mcp.enabled=false`` prevents startup.
  2. ``mcp.enabled=true`` allows startup.
  3. Enabled → disabled stops active MCP connections.
  4. Disabled → enabled starts MCP.
  5. Repeated enable does not duplicate connections.
  6. Repeated disable is safe.
  7. MCP stop runs on the application shutdown path.
  8. Shutdown is idempotent.
  9. Failed MCP connection does not modify persisted configuration.
  10. Disabled MCP server does not register tools.
  11. Disabling a connected server removes its runtime tools.
  12. Existing MCP status remains functional.
  13. MCP status/lifecycle surfaces stay secret-free.
  14. MCP stays runtime-only / stateless.
"""

import asyncio

import pytest

from novi.configuration.bootstrap import build_registry as _build_registry
from novi.configuration.manager import Configuration
from novi.runtime.providers import mcp as mcp_mod
from novi.runtime.tool_registry import ToolRegistry


# ── fake MCP host ──────────────────────────────────────────────────────────


class FakeHost:
    instances: list["FakeHost"] = []

    def __init__(self, config):
        self.name = next(iter(config.get("servers", {})), None)
        self.cfg = (config.get("servers") or {}).get(self.name, {}) if self.name else {}
        self.connected = False
        self.disconnected = False
        self.fail_connect = False
        self.connect_calls = 0
        FakeHost.instances.append(self)

    async def connect(self, server_configs=None):
        self.connect_calls += 1
        if self.fail_connect:
            raise RuntimeError(f"[fake] connect failed for {self.name}")
        self.connected = True

    async def get_tool_wrappers(self):
        async def wrapper(**kwargs):
            return "ok"
        wrapper.__name__ = f"{self.name}_tool"
        wrapper.__doc__ = f"MCP tool from {self.name}"
        return [wrapper]

    async def disconnect(self):
        self.disconnected = True
        self.connected = False

    @classmethod
    def reset(cls):
        cls.instances = []


@pytest.fixture
def mcp(monkeypatch):
    """MCPManager wired to fake hosts + a fresh ToolRegistry."""
    FakeHost.reset()
    monkeypatch.setattr(mcp_mod, "MCPHost", FakeHost)
    registry = ToolRegistry()
    manager = mcp_mod.MCPManager(registry)
    yield manager, registry
    manager.stop()


def _cfg(enabled=True, servers=None, **override):
    out = {"mcp": {"enabled": enabled, "servers": servers or {
        "akita": {"command": "fake"}}}}
    out["mcp"].update(override)
    return out


def _tool_names(registry):
    return sorted(t.name for t in registry.list())


# ── enabled flag honesty ──────────────────────────────────────────────────


def test_disabled_prevents_startup(mcp):
    manager, registry = mcp
    manager.start(_cfg(enabled=False))
    assert manager._loop is None
    assert manager._hosts == {}
    assert _tool_names(registry) == []
    life = manager.get_lifecycle()
    assert life["enabled"] is False
    assert life["state"] == "stopped"


def test_enabled_allows_startup(mcp):
    manager, registry = mcp
    manager.start(_cfg(enabled=True))
    assert manager._loop is not None
    assert "akita" in manager._hosts
    assert manager._hosts["akita"].connected is True
    assert _tool_names(registry) == ["akita_tool"]
    assert manager.get_status()["akita"]["status"] == "ok"


def test_enable_disable_stops_connections(mcp):
    manager, registry = mcp
    manager.start(_cfg(enabled=True))
    host = manager._hosts["akita"]
    manager.refresh_from_config(_cfg(enabled=False))
    assert host.disconnected is True
    assert manager._loop is None
    assert manager._hosts == {}
    assert _tool_names(registry) == []
    assert manager.get_lifecycle()["state"] == "stopped"


def test_disable_enable_starts_mcp(mcp):
    manager, registry = mcp
    manager.start(_cfg(enabled=False))
    assert manager._loop is None
    manager.refresh_from_config(_cfg(enabled=True))
    assert manager._loop is not None
    assert "akita" in manager._hosts
    assert _tool_names(registry) == ["akita_tool"]


# ── idempotency / concurrency safety ──────────────────────────────────────


def test_repeated_enable_no_duplicate_connections(mcp):
    manager, registry = mcp
    manager.start(_cfg(enabled=True))
    for _ in range(3):
        manager.refresh_from_config(_cfg(enabled=True))
    assert len(manager._hosts) == 1
    assert [h.connect_calls for h in FakeHost.instances] == [1]
    assert _tool_names(registry) == ["akita_tool"]


def test_repeated_disable_safe(mcp):
    manager, _ = mcp
    manager.start(_cfg(enabled=True))
    for _ in range(3):
        manager.refresh_from_config(_cfg(enabled=False))
    manager.stop()
    manager.stop()
    assert manager._loop is None
    assert manager._hosts == {}


def test_shutdown_idempotent(mcp):
    manager, registry = mcp
    manager.start(_cfg(enabled=True))
    host = manager._hosts["akita"]
    manager.stop()
    manager.stop()
    assert host.disconnected is True
    assert manager._loop is None
    assert manager._hosts == {}
    assert _tool_names(registry) == []
    assert manager.get_lifecycle()["state"] == "stopped"


def test_shutdown_stop_wired_to_webui_shutdown(monkeypatch):
    """MCPManager.stop() runs on the app shutdown path (lifespan)."""
    import novi.webui_server as ws
    from fastapi.testclient import TestClient

    from novi.webui_server import create_app

    monkeypatch.setattr(
        "novi.configuration.discovery.query_ollama_tags",
        lambda url="", timeout=0.0: [],
    )
    app = create_app(cfg={"mcp": {"enabled": False, "servers": {}},
                          "telegram": {"enabled": False}})
    assert app.router.lifespan_context is not None

    class StopRecorder:
        def __init__(self):
            self.stops = 0

        def stop(self):
            self.stops += 1

    fake_mcp = StopRecorder()
    fake_tg = StopRecorder()
    ws._shared_backend = {"mcp": fake_mcp, "telegram": fake_tg}
    try:
        with TestClient(app):
            pass
    finally:
        ws._shared_backend = None
    assert fake_mcp.stops == 1
    assert fake_tg.stops == 1


def test_shutdown_safe_when_backend_never_built(monkeypatch):
    """Shutdown with no shared backend (or never-started subsystems) is a no-op."""
    import novi.webui_server as ws

    ws._shared_backend = None
    try:
        ws._shutdown_backend()  # must not raise
    finally:
        ws._shared_backend = None


# ── failure behavior + config authority ───────────────────────────────────


def test_failed_connection_does_not_modify_config(mcp, tmp_path):
    manager, _ = mcp
    reg = _build_registry()
    configuration = Configuration(reg, tmp_path / "cfg.toml")
    configuration.initialize()
    reg.require_owner(
        "mcp",
        lambda p, v, prev: manager.refresh_from_config(configuration.snapshot()),
    )

    cfg = _cfg(enabled=True, servers={"bad": {"command": "nope"}})
    for name, host in cfg["mcp"]["servers"].items():
        host.setdefault("enabled", True)
    configuration.set("mcp", {"servers": cfg["mcp"]["servers"]}, by="test")
    configuration.set("mcp.enabled", True, by="test")
    intent = {"servers": cfg["mcp"]["servers"], "enabled": True}

    FakeHost.instances[0].fail_connect = True
    manager.refresh_from_config(configuration.snapshot())

    # intent survives even though the server failed to connect
    assert configuration.get("mcp.enabled") is True
    assert configuration.get("mcp") == intent
    second = Configuration(reg, tmp_path / "cfg.toml")
    second.initialize()
    assert second.get("mcp.enabled") is True
    assert set(second.get("mcp").keys()) == {"servers", "enabled"}


# ── per-server enabled ────────────────────────────────────────────────────


def test_disabled_server_does_not_register_tools(mcp):
    manager, registry = mcp
    servers = {
        "on": {"command": "x"},
        "off": {"command": "y", "enabled": False},
    }
    manager.start(_cfg(enabled=True, servers=servers))
    assert "on" in manager._hosts
    assert "off" not in manager._hosts
    assert _tool_names(registry) == ["on_tool"]


def test_disabling_connected_server_removes_tools(mcp):
    manager, registry = mcp
    servers = {"srv": {"command": "x", "env": {}}}
    manager.start(_cfg(enabled=True, servers=servers))
    assert _tool_names(registry) == ["srv_tool"]

    servers["srv"]["enabled"] = False
    manager.refresh_from_config(_cfg(enabled=True, servers=servers))
    assert "srv" not in manager._hosts
    assert _tool_names(registry) == []

    servers["srv"]["enabled"] = True
    manager.refresh_from_config(_cfg(enabled=True, servers=servers))
    assert "srv" in manager._hosts
    assert _tool_names(registry) == ["srv_tool"]


def test_config_change_reconnects_server(mcp):
    manager, registry = mcp
    servers = {"srv": {"command": "v1"}}
    manager.start(_cfg(enabled=True, servers=servers))
    host1 = manager._hosts["srv"]

    servers["srv"] = {"command": "v2"}
    manager.refresh_from_config(_cfg(enabled=True, servers=servers))
    host2 = manager._hosts["srv"]
    assert host1 is not host2
    assert host1.disconnected is True
    assert _tool_names(registry) == ["srv_tool"]


# ── status + secret safety + statelessness ────────────────────────────────


def test_mcp_status_remains_functional(mcp):
    manager, _ = mcp
    manager.start(_cfg(enabled=True))
    status = manager.get_status()
    assert set(status) == {"akita"}
    assert status["akita"]["status"] == "ok"
    assert [t["name"] for t in status["akita"]["tools"]] == ["akita_tool"]
    assert manager.health_check() == {"akita": "ok"}
    assert manager.get_server_detail("akita")["name"] == "akita"


def test_mcp_status_does_not_leak_secrets(mcp):
    manager, _ = mcp
    servers = {"github": {"command": "npx", "env": {"GITHUB_TOKEN": "ghp_TOP_SECRET"}}}
    manager.start(_cfg(enabled=True, servers=servers))

    status = manager.get_status()
    for server_status in status.values():
        text = repr(server_status)
        assert "ghp_TOP_SECRET" not in text
        assert "env" not in text

    life = manager.get_lifecycle()
    assert "ghp_TOP_SECRET" not in repr(life)
    assert "env" not in repr(life)


def test_mcp_remains_stateless(mcp, tmp_path):
    """MCP owns no persistent state: nothing written to the filesystem."""
    manager, _ = mcp
    manager.start(_cfg(enabled=True, servers={"srv": {"command": "x"}}))
    before = sorted(p.name for p in tmp_path.rglob("*")) if tmp_path.exists() else []
    manager.refresh_from_config(_cfg(enabled=True, servers={"srv": {"command": "x"}}))
    manager.stop()
    after = sorted(p.name for p in tmp_path.rglob("*")) if tmp_path.exists() else []
    assert after == before
    # runtime state is in-memory only and cleared on stop
    assert manager._hosts == {}
    assert manager._server_tools == {}
    assert manager._configured == set()