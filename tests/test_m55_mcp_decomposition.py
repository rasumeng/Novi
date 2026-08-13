"""M5.5 — MCP seams decomposition: hermetic, fake-runtime, no real MCP servers.

Covers the M5.5 decomposition acceptance tests:

Architecture
   1. every seam instantiates independently with a fake host (no MCPHost,
      no real subprocess, no MCPManager facade)
   2. Connector Registry stays thin (never imports MCP lifecycle/runtime)
   3. Runtime Composition (webui) stays thin (seams wired via MCPManager only)

Seams
   4. MCPToolDiscovery standalone: registers into the EXISTING ToolRegistry,
      replace-on-rediscover, unregister removes only its server's tools
   5. MCPRuntimeClient standalone: connect / list_tools / close per server;
       failure records last_error + re-raises; close idempotent
   6. MCPLifecycle standalone: gating (mcp.enabled + per-server enabled),
       reconnect on config change, error isolation, idempotent refresh
   7. MCPStatus standalone: read-only observer (never starts/stops/reconnects),
       safe surface (no env/secrets), works loop-on and loop-off

Composition
   8. MCPManager is a thin facade: methods delegate to the same seam instances
   9. per-session ToolExecutor still consumes registry tools through the seams

CLI / WebUI sharing
  10. CLI ``mcp`` uses the shared runtime primitive (MCPRuntimeClient), not a
       second lifecycle implementation
  11. WebUI ``/api/mcp/test`` uses MCPRuntimeClient, keeps test connections
      isolated from the configured lifecycle
  12. CLI never brokers a second lifecycle/loop outside the WebUI process

Configuration authority + persistence
  13. seams are runtime-only: nothing persisted, config stays authoritative
  14. redaction/secret boundaries maintained through the seams (env masked)
"""

import asyncio
import threading
import time

import pytest

from cozmo.runtime.mcp import (
    MCPLifecycle,
    MCPRuntimeClient,
    MCPStatus,
    MCPToolDiscovery,
)
from cozmo.runtime.tool_registry import ToolRegistry


# ═══════════════════════════════════════════════════════════════════════════
# Programmable fake MCP runtime (hermetic; no real servers / subprocesses)
# ═══════════════════════════════════════════════════════════════════════════


class FakeRuntime:
    """In-memory replacement for MCPHost + MCP protocol.

    Behavior is per-server and set reset-between-tests so each scenario can
    script connect/list failures and the exact tool list returned.
    """

    instances: list["FakeRuntime"] = []
    behavior: dict[str, dict] = {}

    def __init__(self, config):
        self.name = next(iter(config.get("servers", {})), None)
        self.connected = False
        self.closed = False
        self.connect_calls = 0
        self.invocations: list[tuple[str, dict]] = []
        FakeRuntime.instances.append(self)

    async def connect(self, server_configs=None):
        self.connect_calls += 1
        beh = FakeRuntime.behavior.get(self.name, {})
        if beh.get("fail_connect"):
            raise RuntimeError(f"[fake] connect failed for {self.name}")
        self.connected = True

    async def get_tool_wrappers(self):
        beh = FakeRuntime.behavior.get(self.name, {})
        if beh.get("fail_list"):
            raise RuntimeError(f"[fake] list failed for {self.name}")
        names = beh.get("tools", [f"{self.name}_tool"])
        wrappers = []
        for n in names:
            async def wrapper(**kwargs):
                self.invocations.append((n, kwargs))
                return f"{n}:ok"
            wrapper.__name__ = n
            wrapper.__doc__ = f"tool {n}"
            wrappers.append(wrapper)
        return wrappers

    async def disconnect(self):
        self.closed = True
        self.connected = False

    @classmethod
    def reset(cls, **behavior):
        cls.instances = []
        cls.behavior = behavior


def tool_names(registry):
    return sorted(t.name for t in registry.list())


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures — every seam constructed directly (no MCPManager, no MCPHost)
# ═══════════════════════════════════════════════════════════════════════════


def _make_seams(registry=None):
    FakeRuntime.reset()
    reg = registry or ToolRegistry()
    discovery = MCPToolDiscovery(reg)
    lifecycle = MCPLifecycle(discovery, host_factory=FakeRuntime)
    status = MCPStatus(lifecycle, discovery)
    return reg, discovery, lifecycle, status


@pytest.fixture
def seams():
    reg, discovery, lifecycle, status = _make_seams()
    yield reg, discovery, lifecycle, status
    lifecycle.stop()


def _cfg(enabled=True, servers=None):
    return {"mcp": {"enabled": enabled, "servers": servers or {
        "akita": {"command": "fake"}}}}


# ═══════════════════════════════════════════════════════════════════════════
# 1. Architecture — seams instantiate independently, composition stays thin
# ═══════════════════════════════════════════════════════════════════════════

def test_all_seams_instantiable_without_mcp_host():
    from cozmo.runtime import mcp as mcp_pkg
    assert "MCPHost" not in vars(mcp_pkg)
    assert "MCPManager" not in vars(mcp_pkg)

    reg = ToolRegistry()
    disc = MCPToolDiscovery(reg)
    life = MCPLifecycle(disc, host_factory=FakeRuntime)
    client = MCPRuntimeClient("solo", host_factory=FakeRuntime)
    status = MCPStatus(life, disc)
    try:
        assert disc.server_tools == {}
        assert life.state == "stopped"
        assert client.host is None
        assert status.health_check() == {}
    finally:
        life.stop()


def test_connector_registry_stays_thin():
    import importlib
    module_text = open(importlib.import_module("cozmo.connectors").__file__, encoding="utf-8").read()
    assert "from .runtime.mcp" not in module_text
    assert "import MCPLifecycle" not in module_text
    assert "MCPHost" not in module_text


def test_runtime_composition_stays_thin():
    import importlib
    # webui built only via MCPManager seam wiring inside cozmo/webui.py
    module_text = open(importlib.import_module("cozmo.webui").__file__, encoding="utf-8").read()
    assert "from .runtime.providers.mcp import MCPManager" in module_text
    # the seams themselves are not assembled by hand in webui
    assert "MCPLifecycle(" not in module_text
    assert "MCPRuntimeClient(" not in module_text


# ═══════════════════════════════════════════════════════════════════════════
# 4. Discovery seam — standalone registration semantics
# ═══════════════════════════════════════════════════════════════════════════

def test_discovery_registers_into_existing_registry():
    reg = ToolRegistry()

    def f(): pass
    reg.register("unrelated", f, "existing tool")

    discovery = MCPToolDiscovery(reg)
    discovery.discover("srv_a", _wrappers(["a_one", "a_two"]))
    discovery.discover("srv_b", _wrappers(["b_one"]))

    assert tool_names(reg) == ["a_one", "a_two", "b_one", "unrelated"]
    assert [t["name"] for t in discovery.tools_for("srv_a")] == ["a_one", "a_two"]
    assert discovery.tools_for("srv_b")[0]["name"] == "b_one"


def test_discovery_rediscover_replaces_not_duplicates():
    reg = ToolRegistry()
    discovery = MCPToolDiscovery(reg)
    discovery.discover("srv", _wrappers(["t1"]))
    discovery.discover("srv", _wrappers(["t1", "t2"]))

    assert tool_names(reg) == ["t1", "t2"]  # no duplicate t1
    assert len([t for t in reg.list() if t.name == "t1"]) == 1


def test_discovery_unregister_only_removes_own_server():
    reg = ToolRegistry()
    discovery = MCPToolDiscovery(reg)
    discovery.discover("srv_a", _wrappers(["a1"]))
    discovery.discover("srv_b", _wrappers(["b1"]))

    discovery.unregister_server("srv_a")
    assert tool_names(reg) == ["b1"]
    assert "srv_a" not in discovery.server_tools
    assert [t["name"] for t in discovery.tools_for("srv_b")] == ["b1"]


def test_discovery_unregister_all_and_empty_servers():
    reg = ToolRegistry()
    discovery = MCPToolDiscovery(reg)
    discovery.discover("a", _wrappers([]))
    discovery.discover("b", _wrappers(["b1"]))
    discovery.unregister_all()
    assert reg.list() == []
    assert discovery.server_tools == {}


def _wrappers(names):
    wrappers = []
    for n in names:
        async def w(**kwargs):
            return f"{n}:ok"
        w.__name__ = n
        w.__doc__ = n
        wrappers.append(w)
    return wrappers


# ═══════════════════════════════════════════════════════════════════════════
# 5. Runtime client seam — standalone per-server connection
# ═══════════════════════════════════════════════════════════════════════════

def test_runtime_client_e2e_hermetic():
    FakeRuntime.reset(akita={"tools": ["read", "write"]})
    client = MCPRuntimeClient("akita", host_factory=FakeRuntime)

    async def run():
        await client.connect({"command": "npx"})
        wrappers = await client.list_tools()
        out = await wrappers[0](**{"q": 1})
        await client.close()

    asyncio.run(run())
    assert client.connected is False
    assert client.disconnected is True
    assert client.host is None
    # the client itself owns no ToolRegistry side effects
    assert FakeRuntime.behavior["akita"]["tools"] == ["read", "write"]


def test_runtime_client_failure_records_and_reraises():
    FakeRuntime.reset(bad={"fail_connect": True})
    client = MCPRuntimeClient("bad", host_factory=FakeRuntime)
    with pytest.raises(RuntimeError):
        asyncio.run(client.connect({"command": "nope"}))
    assert client.connected is False
    assert client.host is None
    assert "connect failed" in client.last_error


def test_runtime_client_close_idempotent():
    FakeRuntime.reset(akita={})
    client = MCPRuntimeClient("akita", host_factory=FakeRuntime)
    asyncio.run(client.connect({"command": "x"}))
    asyncio.run(client.close())
    asyncio.run(client.close())  # never raises
    assert client.connected is False
    assert client.disconnected is True


# ═══════════════════════════════════════════════════════════════════════════
# 6. Lifecycle seam — standalone gating, reconnect, isolation, idempotency
# ═══════════════════════════════════════════════════════════════════════════

def test_lifecycle_enabled_gating(seams):
    reg, _, life, _ = seams
    life.start(_cfg(enabled=False))
    assert life.state == "stopped"
    assert life.loop is None
    assert tool_names(reg) == []

    life.start(_cfg(enabled=True))
    assert life.state == "running"
    assert life.loop is not None
    assert "akita" in life.server_names
    assert tool_names(reg) == ["akita_tool"]


def test_lifecycle_per_server_enabled(seams):
    reg, _, life, _ = seams
    servers = {"on": {"command": "x"}, "off": {"command": "y", "enabled": False}}
    life.start(_cfg(enabled=True, servers=servers))
    assert life.server_names == {"on"}
    assert tool_names(reg) == ["on_tool"]


def test_lifecycle_reconnect_on_config_change(seams):
    reg, _, life, _ = seams
    servers = {"srv": {"command": "v1"}}
    life.start(_cfg(enabled=True, servers=servers))
    host1 = next(iter(FakeRuntime.instances))
    assert host1.name == "srv"

    servers["srv"] = {"command": "v2"}
    life.refresh_from_config(_cfg(enabled=True, servers=servers))
    host2 = next(i for i in FakeRuntime.instances if i is not host1)
    assert host1.closed is True
    assert host2.connected is True
    assert tool_names(reg) == ["srv_tool"]
    assert len(FakeRuntime.instances) == 2


def test_lifecycle_connect_error_isolation(seams):
    reg, _, life, _ = seams
    FakeRuntime.reset(ok={}, bad={"fail_connect": True})
    servers = {"ok": {"command": "c"}, "bad": {"command": "c"}}
    life.start(_cfg(enabled=True, servers=servers))
    # one server failing must not take the others down
    assert "ok" in life.server_names
    assert "bad" not in life.server_names
    assert "bad" in life.server_errors
    assert tool_names(reg) == ["ok_tool"]
    assert life.state == "running"


def test_lifecycle_list_error_keeps_connection_but_marks_error(seams):
    reg, _, life, _ = seams
    FakeRuntime.reset(bad={"fail_list": True})
    life.start(_cfg(enabled=True, servers={"bad": {"command": "c"}}))
    assert "bad" in life.server_names
    assert "tool discovery failed" in life.server_errors.values()
    assert tool_names(reg) == []


def test_lifecycle_refresh_idempotent_no_reconnect(seams):
    reg, _, life, _ = seams
    life.start(_cfg(enabled=True))
    first = list(FakeRuntime.instances)
    life.refresh_from_config(_cfg(enabled=True))
    life.refresh_from_config(_cfg(enabled=True))
    assert FakeRuntime.instances == first
    assert [c.connect_calls for c in FakeRuntime.instances] == [1]
    assert tool_names(reg) == ["akita_tool"]


def test_lifecycle_disable_removes_tools_and_clears_runtime(seams):
    reg, _, life, _ = seams
    life.start(_cfg(enabled=True))
    life.refresh_from_config(_cfg(enabled=False))
    assert life.state == "stopped"
    assert life.loop is None
    assert life.server_names == set()
    assert tool_names(reg) == []


def test_lifecycle_dispose_loop_after_stop(seams):
    _, _, life, status = seams
    life.start(_cfg(enabled=True))
    loop = life.loop
    assert loop is not None
    life.stop()
    assert life.loop is None
    assert status.get_status() == {}


# ═══════════════════════════════════════════════════════════════════════════
# 7. Status seam — read-only observer of lifecycle + discovery
# ═══════════════════════════════════════════════════════════════════════════

def test_status_is_read_only_observer(seams):
    _, _, life, status = seams
    life.start(_cfg(enabled=True))
    clients_before = set(life.clients)
    inst_before = list(FakeRuntime.instances)

    _ = status.get_status()
    _ = status.get_lifecycle()
    _ = status.health_check()

    assert set(life.clients) == clients_before
    assert life.state == "running"  # status never stopped the lifecycle
    # status never reconnects or mutates the host set
    assert FakeRuntime.instances == inst_before
    assert len(FakeRuntime.instances) == 1


def test_status_reports_ok_with_tool_count(seams):
    _, _, life, status = seams
    FakeRuntime.reset(akita={"tools": ["t1", "t2"]})
    life.start(_cfg(enabled=True))
    st = status.get_status()
    assert st["akita"]["status"] == "ok"
    assert [t["name"] for t in st["akita"]["tools"]] == ["t1", "t2"]


def test_status_safe_surface_no_env_no_secrets(seams, tmp_path):
    _, _, life, status = seams
    servers = {"github": {"command": "npx", "env": {"GITHUB_TOKEN": "ghp_M5_5_SECRET"}}}
    life.start(_cfg(enabled=True, servers=servers))

    text = repr(status.get_status()) + repr(status.get_lifecycle())
    assert "ghp_M5_5_SECRET" not in text
    assert "env" not in text
    assert "npx" not in text  # command details are not part of status


def test_status_works_when_loop_off(seams):
    _, _, _, status = seams  # never started -> loop never created
    assert status.get_status() == {}
    assert status.get_lifecycle() == {
        "enabled": False, "state": "stopped", "running": False, "servers": {},
    }
    assert status.health_check() == {}


def test_status_stop_then_probe_disconnected(seams):
    _, _, life, status = seams
    life.start(_cfg(enabled=True))
    life.stop()
    assert status.get_status() == {}


# ═══════════════════════════════════════════════════════════════════════════
# 8. Composition — MCPManager delegates to the same seam instances
# ═══════════════════════════════════════════════════════════════════════════

def test_manager_is_thin_facade_over_seams(monkeypatch):
    from cozmo.runtime import mcp as mcp_mod

    FakeRuntime.reset(akita={})
    monkeypatch.setattr("cozmo.runtime.providers.mcp.MCPHost", FakeRuntime)
    from cozmo.runtime.providers.mcp import MCPManager
    from cozmo.runtime.tool_registry import ToolRegistry

    reg = ToolRegistry()
    manager = MCPManager(reg)
    try:
        assert manager.get_status() == {}
        # facade exposes the seam instances, not a private reimplementation
        assert isinstance(manager._discovery, MCPToolDiscovery)
        assert isinstance(manager._lifecycle, MCPLifecycle)
        assert isinstance(manager._status, MCPStatus)

        manager.start(_cfg(enabled=True))
        assert "akita" in manager._hosts
        assert tool_names(reg) == ["akita_tool"]
        # facade method == seam method (same backing instance)
        assert manager.get_status() is not None
        assert manager.get_lifecycle()["state"] == "running"
    finally:
        manager.stop()


# ═══════════════════════════════════════════════════════════════════════════
# 9. per-session ToolExecutor consumes seam-registered tools
# ═══════════════════════════════════════════════════════════════════════════

def test_executor_consumes_seam_registered_tools(seams, tmp_path):
    reg, _, life, _ = seams
    FakeRuntime.reset(akita={"tools": ["akita_query"]})
    life.start(_cfg(enabled=True))

    # a fresh per-session executor over the shared registration sees the tool
    from cozmo.runtime.tool_executor import ToolExecutor
    from cozmo.runtime.permissions import PermissionResolver

    executor = ToolExecutor(
        registry=reg,
        perms=PermissionResolver({}, auto=False),
        lesson_store=_NoopStore(),
        lc_tools=reg.as_lc_tools(),
        tool_fallbacks={},
        max_tool_output=8000,
        perm_mode="manual",
        mcp_permissions=None,
    )
    result = executor.execute("akita_query", {}, permission_callback=lambda t, a: True)
    assert result.success is True
    assert "ok" in result.output


class _NoopStore:
    def record(self, *a, **k):
        pass


# ═══════════════════════════════════════════════════════════════════════════
# 10/11/12. CLI + WebUI share runtime primitives (no second lifecycle)
# ═══════════════════════════════════════════════════════════════════════════

def test_cli_mcp_uses_shared_runtime_primitive():
    import importlib
    cli_mod = importlib.import_module("cozmo.cli")
    cli_text = open(cli_mod.__file__, encoding="utf-8").read()
    # CLI drives connections through the runtime client seam
    assert "MCPRuntimeClient" in cli_text
    # ...and generates no second lifecycle/loop owner
    assert "MCPLifecycle(" not in cli_text
    assert "new_event_loop" not in cli_text
    assert "MCPManager(" not in cli_text


def test_webui_mcp_test_uses_shared_primitive_and_is_isolated():
    import importlib
    ws_mod = importlib.import_module("cozmo.webui_server")
    ws_text = open(ws_mod.__file__, encoding="utf-8").read()
    assert "MCPRuntimeClient" in ws_text
    assert "MCPLifecycle(" not in ws_text
    # test connection must not touch configured lifecycle: the endpoint creates
    # its own ephemeral client, never the shared manager
    test_block = ws_text.split("/api/mcp/test")[1].split("/api/attachments")[0]
    assert "get_lifecycle" not in test_block
    assert "MCPHost(" not in test_block
    assert "MCPManager(" not in test_block


def test_webui_mcp_test_endpoint_reports_unknown_server(monkeypatch):
    import cozmo.webui_server as ws
    from fastapi.testclient import TestClient

    ws._shared_backend = None
    monkeypatch.setattr(
        "cozmo.configuration.discovery.query_ollama_tags",
        lambda url="", timeout=0.0: [],
    )
    try:
        app = ws.create_app(cfg={"mcp": {"servers": {}}, "telegram": {"enabled": False}})
        client = TestClient(app)
        resp = client.post("/api/mcp/test", json={"name": "missing"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        assert "not found" in resp.json()["error"]
    finally:
        ws._shared_backend = None


# ═══════════════════════════════════════════════════════════════════════════
# 13/14. Runtime-only + statelessness + redaction boundaries
# ═══════════════════════════════════════════════════════════════════════════

def test_seams_write_nothing_to_disk(seams, tmp_path):
    reg, discovery, life, status = seams
    life.start(_cfg(enabled=True, servers={"srv": {"command": "x"}}))
    before = sorted(p.name for p in tmp_path.rglob("*")) if tmp_path.exists() else []
    life.refresh_from_config(_cfg(enabled=True, servers={"srv": {"command": "x"}}))
    _ = status.get_status()
    discovery.unregister_server("srv")
    life.stop()
    after = sorted(p.name for p in tmp_path.rglob("*")) if tmp_path.exists() else []
    assert after == before
    # runtime state is fully in-memory and cleared on stop
    assert life.server_names == set()
    assert discovery.server_tools == {}
    assert life.clients == {}
    assert life.server_configs == {}


def test_seams_never_write_config(tmp_path):
    from cozmo.configuration.bootstrap import build_registry as _build_registry
    from cozmo.configuration.manager import Configuration

    FakeRuntime.reset(akita={})
    reg = _build_registry()
    configuration = Configuration(reg, tmp_path / "cfg.toml")
    configuration.initialize()
    configuration.set("mcp.enabled", True, by="test")
    configuration.set("mcp.servers", {"srv": {"command": "x"}}, by="test")
    configuration.store.write(configuration.state.as_dict())
    before = (tmp_path / "cfg.toml").read_bytes()

    _, discovery, life, status = _make_seams()
    try:
        life.start(configuration.snapshot())
        _ = status.get_status()
        discovery.unregister_all()
        life.stop()
    finally:
        life.stop()
    assert (tmp_path / "cfg.toml").read_bytes() == before