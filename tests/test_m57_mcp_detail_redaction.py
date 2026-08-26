"""M5.7 — MCP server detail must never expose configured env values.

Closes the secret-redaction boundary on ``GET /api/mcp/servers/{name}`` and
``MCPStatus.get_server_detail``: env VALUES are masked with the existing M5.2
placeholder (``{"configured": bool, "masked": true}``) while env keys, command,
args, name, and lifecycle stay visible. Storage is never touched and the
stored/configured env is never mutated.

Hermetic: fake MCP host, no real server, no subprocess, no network.
"""

import pytest
from fastapi.testclient import TestClient

import novi.webui_server as ws


class FakeHost:
    """In-memory MCP host replacement; never spawns a subprocess/network."""

    instances = []

    def __init__(self, config):
        self.name = next(iter(config.get("servers", {})), None)
        self.cfg = (config.get("servers") or {}).get(self.name, {}) if self.name else {}
        self.connected = True

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


@pytest.fixture
def seams(monkeypatch):
    """MCPStatus over a fake host, wired to a secret-bearing server config."""
    from novi.runtime.mcp import MCPLifecycle, MCPStatus, MCPToolDiscovery
    from novi.runtime.tool_registry import ToolRegistry

    FakeHost.instances = []
    reg = ToolRegistry()
    disc = MCPToolDiscovery(reg)
    life = MCPLifecycle(disc, host_factory=FakeHost)
    env = {"GITHUB_TOKEN": "ghp_M57_SECRET", "ORG": "acme"}
    cfg = {"mcp": {"enabled": True, "servers": {"github": {
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": env}}}}
    life.start(cfg)
    status = MCPStatus(life, disc)
    yield cfg, env, life, status
    life.stop()


# ── 1 + 2: values masked, keys structurally intact ────────────────────────


def test_detail_redacts_env_values_keeps_keys(seams):
    _, _, _, status = seams
    detail = status.get_server_detail("github")
    env = detail["config"]["env"]
    assert env == {
        "GITHUB_TOKEN": {"configured": True, "masked": True},
        "ORG": {"configured": True, "masked": True},
    }


def test_detail_never_returns_raw_env_value(seams):
    _, _, _, status = seams
    text = repr(status.get_server_detail("github"))
    assert "ghp_M57_SECRET" not in text


# ── 3: non-secret fields keep flowing ─────────────────────────────────────


def test_detail_keeps_command_args_name(seams):
    _, _, _, status = seams
    detail = status.get_server_detail("github")
    assert detail is not None
    assert detail["name"] == "github"
    assert detail["config"]["command"] == "npx"
    assert detail["config"]["args"] == ["-y", "@modelcontextprotocol/server-github"]


def test_detail_status_and_tools_survive(seams):
    _, _, _, status = seams
    detail = status.get_server_detail("github")
    assert detail["status"] in ("ok", "disconnected")
    assert [t["name"] for t in detail["tools"]] == ["github_tool"]


# ── 4: storage/config never mutated ───────────────────────────────────────


def test_redaction_does_not_mutate_stored_or_configured_env(seams):
    cfg, env, life, status = seams
    status.get_server_detail("github")
    assert life.server_configs["github"]["env"]["GITHUB_TOKEN"] == "ghp_M57_SECRET"
    assert env == {"GITHUB_TOKEN": "ghp_M57_SECRET", "ORG": "acme"}
    assert cfg["mcp"]["servers"]["github"]["env"]["GITHUB_TOKEN"] == "ghp_M57_SECRET"


# ── 5: safe status/lifecycle surfaces unchanged ───────────────────────────


def test_safe_status_and_lifecycle_unchanged(seams):
    _, _, _, status = seams
    status_text = repr(status.get_status())
    lifecycle_text = repr(status.get_lifecycle())
    assert "ghp_M57_SECRET" not in status_text
    assert "ghp_M57_SECRET" not in lifecycle_text
    assert "env" not in status_text
    assert "env" not in lifecycle_text


# ── 6: API boundary never leaks credentials ───────────────────────────────


def test_api_server_detail_never_leaks_credentials(monkeypatch):
    from novi.runtime.providers import mcp as mcp_mod

    FakeHost.instances = []
    monkeypatch.setattr(mcp_mod, "MCPHost", FakeHost)

    from novi.runtime.tool_registry import ToolRegistry

    registry = ToolRegistry()
    manager = mcp_mod.MCPManager(registry)
    servers = {"github": {"command": "npx", "env": {"GITHUB_TOKEN": "ghp_API_SECRET",
                                                    "ORG": "acme"}}}
    manager.start({"mcp": {"enabled": True, "servers": servers}})

    monkeypatch.setattr(ws, "_shared_backend", {"mcp": manager})
    try:
        client = TestClient(ws.create_app(cfg={"mcp": {"servers": servers}}))
        resp = client.get("/api/mcp/servers/github")
        assert resp.status_code == 200
        body = resp.json()
        text = resp.text
        assert "ghp_API_SECRET" not in text
        assert body["config"]["env"]["GITHUB_TOKEN"] == {
            "configured": True, "masked": True}
        assert body["config"]["env"]["ORG"] == {"configured": True, "masked": True}
        assert body["config"]["command"] == "npx"
    finally:
        manager.stop()
        monkeypatch.setattr(ws, "_shared_backend", None)