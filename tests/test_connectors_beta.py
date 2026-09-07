"""Task 8 (beta cleanup) — connector flow verification, no redesign.

Covers the connect -> status -> tool-exec -> error-surfaced path:

* ``GET /api/connectors/status`` shape (keys + per-connector fields)
* MCP lifecycle flow (start -> running -> disable -> stopped -> re-enable)
* Secret redaction across lifecycle / status / detail / connectors payloads
* Tool-exec error surface (``POST /api/mcp/test`` + ``MCPRuntimeClient``)

Hermetic: fake MCP host / fake runtime client, no subprocess, no network.
"""

import pytest
from fastapi.testclient import TestClient

import novi.webui_server as ws
from novi.connectors import ConnectorDefinition, ConnectorRegistry

SECRET = "ghp_TASK8_SECRET"
TOKEN = "TASK8_BOT_TOKEN"


# ── fakes ────────────────────────────────────────────────────────────────


class FakeHost:
    """In-memory MCP host replacement; never spawns a subprocess/network."""

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


class FakeConfig:
    """Minimal stand-in for the Configuration framework (mcp reads only)."""

    def __init__(self, servers):
        from novi.configuration.bootstrap import build_registry
        self._servers = servers
        self.registry = build_registry()

    def get(self, key, default=None):
        if key == "mcp":
            return {"servers": self._servers}
        return default


def _manager(monkeypatch):
    from novi.runtime.providers import mcp as mcp_mod
    from novi.runtime.tool_registry import ToolRegistry

    monkeypatch.setattr(mcp_mod, "MCPHost", FakeHost)
    return mcp_mod.MCPManager(ToolRegistry())


def _cfg(enabled=True):
    return {"mcp": {"enabled": enabled, "servers": {
        "github": {"command": "npx", "env": {"GITHUB_TOKEN": SECRET}}}}}


@pytest.fixture
def app_with_fake_config(monkeypatch):
    """create_app wired to a FakeConfig; caller sets servers per test."""
    import novi.configuration.bootstrap as boot

    servers = {}

    def _get():
        return FakeConfig(servers)

    monkeypatch.setattr(boot, "get_configuration", _get)
    client = TestClient(ws.create_app(cfg={}))
    return client, servers


# ═══════════════════════════════════════════════════════════════════════════
# 1. Connectors status endpoint shape
# ═══════════════════════════════════════════════════════════════════════════

def test_connectors_status_shape(monkeypatch):
    registry = ConnectorRegistry()
    registry.register(ConnectorDefinition(
        "mcp", "mcp", label="MCP", enabled=True,
        status_fn=lambda: {"enabled": True, "state": "running", "servers": {}},
        identity={"servers": ["github"]},
    ))
    registry.register(ConnectorDefinition(
        "telegram", "telegram", label="Telegram", enabled=False,
        status_fn=lambda: {"enabled": False, "state": "stopped"},
    ))
    monkeypatch.setattr(ws, "_shared_backend", {"connectors": registry})
    try:
        client = TestClient(ws.create_app(cfg={}))
        resp = client.get("/api/connectors/status")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"mcp", "telegram"}
        for connector_id, payload in body.items():
            assert "enabled" in payload, connector_id
            assert "state" in payload, connector_id
        assert body["mcp"]["state"] == "running"
        assert body["mcp"]["enabled"] is True
        assert body["telegram"]["state"] == "stopped"
    finally:
        monkeypatch.setattr(ws, "_shared_backend", None)


def test_connectors_status_empty_without_backend(monkeypatch):
    monkeypatch.setattr(ws, "_shared_backend", None)
    client = TestClient(ws.create_app(cfg={}))
    assert client.get("/api/connectors/status").json() == {}


def test_mcp_lifecycle_default_without_backend(monkeypatch):
    monkeypatch.setattr(ws, "_shared_backend", None)
    client = TestClient(ws.create_app(cfg={}))
    body = client.get("/api/mcp/lifecycle").json()
    assert body == {"enabled": False, "state": "stopped",
                    "running": False, "servers": {}}


# ═══════════════════════════════════════════════════════════════════════════
# 2. MCP lifecycle flow: connect -> status -> disable -> re-enable
# ═══════════════════════════════════════════════════════════════════════════

def test_mcp_lifecycle_flow(monkeypatch):
    manager = _manager(monkeypatch)
    connectors = ConnectorRegistry()
    connectors.register(ConnectorDefinition(
        "mcp", "mcp", label="MCP", enabled=True,
        status_fn=manager.get_lifecycle, identity={"servers": []},
    ))
    try:
        # connect
        manager.start(_cfg(enabled=True))
        assert "github" in manager._hosts
        # status
        lifecycle = connectors.get("mcp").status()
        assert lifecycle["enabled"] is True
        assert lifecycle["state"] == "running"
        assert lifecycle["running"] is True
        assert lifecycle["servers"]["github"]["connected"] is True
        status = manager.get_status()
        assert status["github"]["status"] in ("ok", "connected", "running", "disconnected")
        assert [t["name"] for t in status["github"]["tools"]] == ["github_tool"]
        # disable -> stopped
        manager.refresh_from_config(_cfg(enabled=False))
        connectors.get("mcp").update(enabled=False)
        stopped = connectors.get("mcp").status()
        assert stopped["state"] == "stopped"
        assert stopped["running"] is False
        assert manager._hosts == {}
        # re-enable -> running again
        manager.refresh_from_config(_cfg(enabled=True))
        connectors.get("mcp").update(enabled=True)
        assert connectors.get("mcp").status()["running"] is True
        assert "github" in manager._hosts
    finally:
        manager.stop()


def test_mcp_lifecycle_endpoints_reflect_flow(monkeypatch):
    manager = _manager(monkeypatch)
    monkeypatch.setattr(ws, "_shared_backend", {"mcp": manager})
    try:
        client = TestClient(ws.create_app(cfg={}))
        manager.start(_cfg(enabled=True))
        lifecycle = client.get("/api/mcp/lifecycle").json()
        assert lifecycle["running"] is True
        assert lifecycle["servers"]["github"]["connected"] is True
        status = client.get("/api/mcp/status").json()
        assert "github" in status
    finally:
        manager.stop()
        monkeypatch.setattr(ws, "_shared_backend", None)


# ═══════════════════════════════════════════════════════════════════════════
# 3. Secret redaction across every status payload
# ═══════════════════════════════════════════════════════════════════════════

def test_no_status_payload_carries_raw_config(monkeypatch):
    manager = _manager(monkeypatch)
    connectors = ConnectorRegistry()
    connectors.register(ConnectorDefinition(
        "mcp", "mcp", enabled=True, status_fn=manager.get_lifecycle,
        identity={"servers": ["github"]},
    ))
    try:
        manager.start(_cfg(enabled=True))
        text = (
            repr(manager.get_status())
            + repr(manager.get_lifecycle())
            + repr(manager.get_server_detail("github"))
            + repr(connectors.statuses())
            + repr(connectors.get("mcp").describe())
        )
        assert SECRET not in text
        assert "env" not in repr(manager.get_status())
        assert "env" not in repr(manager.get_lifecycle())
    finally:
        manager.stop()


def test_server_detail_env_masked_via_api(monkeypatch):
    manager = _manager(monkeypatch)
    manager.start(_cfg(enabled=True))
    monkeypatch.setattr(ws, "_shared_backend", {"mcp": manager})
    try:
        client = TestClient(ws.create_app(cfg={}))
        resp = client.get("/api/mcp/servers/github")
        assert resp.status_code == 200
        body = resp.json()
        assert SECRET not in resp.text
        assert body["config"]["env"]["GITHUB_TOKEN"] == {
            "configured": True, "masked": True}
        assert body["config"]["command"] == "npx"
        assert body["name"] == "github"
    finally:
        manager.stop()
        monkeypatch.setattr(ws, "_shared_backend", None)


def test_connectors_status_never_exposes_telegram_token():
    from novi.services.telegram import TelegramLifecycle

    built = []

    def factory(ctx, token, *, allowed_chat_ids=()):
        class _Bot:
            def start(self, **kw):
                pass

            def stop(self):
                pass

        bot = _Bot()
        built.append(bot)
        return bot

    life = TelegramLifecycle(object(), bot_factory=factory)
    connectors = ConnectorRegistry()
    connectors.register(ConnectorDefinition(
        "telegram", "telegram", status_fn=life.get_status))
    life.apply({"telegram": {"enabled": True, "bot_token": TOKEN}})
    connectors.get("telegram").update(enabled=True)
    text = repr(connectors.statuses()) + repr(
        connectors.get("telegram").describe())
    assert TOKEN not in text
    assert "bot_token" not in text


# ═══════════════════════════════════════════════════════════════════════════
# 4. Tool-exec error surface
# ═══════════════════════════════════════════════════════════════════════════

def test_mcp_test_requires_name(app_with_fake_config):
    client, _ = app_with_fake_config
    resp = client.post("/api/mcp/test", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": False, "error": "name required"}


def test_mcp_test_unknown_server(app_with_fake_config):
    client, _ = app_with_fake_config
    body = client.post("/api/mcp/test", json={"name": "ghost"}).json()
    assert body["ok"] is False
    assert "not found in config" in body["error"]
    assert "ghost" in body["error"]


def test_mcp_test_connect_failure_surfaced(app_with_fake_config, monkeypatch):
    import novi.runtime.mcp.runtime_client as rc_mod

    client, servers = app_with_fake_config
    servers["github"] = {"command": "npx", "env": {"GITHUB_TOKEN": SECRET}}

    class FailingClient:
        def __init__(self, name):
            self.name = name

        async def connect(self, cfg):
            raise RuntimeError("connection refused")

        async def list_tools(self):
            raise AssertionError("unreachable")

        async def close(self):
            pass

    monkeypatch.setattr(rc_mod, "MCPRuntimeClient", FailingClient)
    body = client.post("/api/mcp/test", json={"name": "github"}).json()
    assert body["ok"] is False
    assert body["error"].startswith("RuntimeError:")
    assert "connection refused" in body["error"]
    assert SECRET not in repr(body)


def test_mcp_test_success_reports_tool_count(app_with_fake_config, monkeypatch):
    import novi.runtime.mcp.runtime_client as rc_mod

    client, servers = app_with_fake_config
    servers["github"] = {"command": "npx"}

    async def _wrapper(**kwargs):
        return "ok"

    class OkClient:
        def __init__(self, name):
            pass

        async def connect(self, cfg):
            pass

        async def list_tools(self):
            return [_wrapper, _wrapper, _wrapper]

        async def close(self):
            pass

    monkeypatch.setattr(rc_mod, "MCPRuntimeClient", OkClient)
    body = client.post("/api/mcp/test", json={"name": "github"}).json()
    assert body == {"ok": True, "tools": 3}


def test_runtime_client_failure_records_and_reraises():
    import asyncio

    from novi.runtime.mcp.runtime_client import MCPRuntimeClient

    class BoomHost:
        def __init__(self, config):
            pass

        async def connect(self, cfgs):
            raise ConnectionError("refused")

        async def disconnect(self):
            pass

    async def _run():
        client = MCPRuntimeClient("github", host_factory=BoomHost)
        with pytest.raises(ConnectionError):
            await client.connect({"command": "npx"})
        assert client.connected is False
        assert "ConnectionError" in (client.last_error or "")
        assert "refused" in (client.last_error or "")
        await client.close()  # idempotent, never raises
        await client.close()
        assert client.connected is False

    asyncio.run(_run())
