"""Task 4.1 — Single config path (framework only).

Asserts:
* PUT /api/config removed (or delegates) — no bulk-write endpoint
* GET /api/config remains read-only alias (no write) and proxies snapshot
* Framework PATCH/POST per-setting is sufficient for every previous mutation
  (permissions, mcp.servers, agent, search.*, memory.*, runtime.*)
* No frontend source still references saveConfig/flushLegacy//api/config writes
* config_roots whole-root namespace removed (audit gate)
"""
import pathlib

import pytest
from fastapi.testclient import TestClient


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
WEBUI_SERVER = PROJECT_ROOT / "novi" / "webui_server.py"
API_TS = PROJECT_ROOT / "novi" / "webui" / "src" / "components" / "settings" / "api.ts"
SETTINGS_MODAL = PROJECT_ROOT / "novi" / "webui" / "src" / "components" / "settings" / "SettingsModal.tsx"
BUILTIN = PROJECT_ROOT / "novi" / "configuration" / "builtin.py"


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HOMEDRIVE", str(tmp_path))
    monkeypatch.setenv("HOMEPATH", "")
    import novi.configuration.bootstrap as boot
    monkeypatch.setattr(boot, "CONFIG_PATH", tmp_path / ".novi" / "config.toml")
    monkeypatch.setattr(boot, "_configuration", None)
    # ensure fresh backend lock (webui_server caches _shared_backend)
    import novi.webui_server as ws
    ws._shared_backend = None
    from novi.webui_server import create_app
    return TestClient(create_app(cfg={}))


def test_put_api_config_removed(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    resp = client.put("/api/config", json={"runtime": {"max_steps": 99}})
    # FastAPI returns 405 when route does not exist (or 404). It must not be 200.
    assert resp.status_code in (404, 405, 410), f"PUT /api/config should be gone, got {resp.status_code}"
    # Ensure source has no PUT route
    src = WEBUI_SERVER.read_text(encoding="utf-8")
    assert '@app.put("/api/config")' not in src
    assert "def put_config" not in src


def test_get_api_config_is_readonly_alias(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    # Set a known value via framework path
    client.patch("/api/configuration", json={"runtime.temperature": 0.7})
    # GET /api/config should return a dict (read-only alias) and reflect the set
    r_config = client.get("/api/configuration").json()
    r_legacy = client.get("/api/config").json()
    assert isinstance(r_legacy, dict)
    # alias should contain runtime.temperature (via snapshot sanitized+redacted)
    # redactor may mask secrets but runtime is passthrough
    assert r_legacy.get("runtime", {}).get("temperature") == 0.7 or r_legacy.get("runtime", {}).get("temperature") == r_config.get("runtime", {}).get("temperature")
    # It must not accept writes — PATCH to legacy shape not supported, only GET
    # Ensure GET does not mutate
    r2 = client.get("/api/config").json()
    assert r2 == r_legacy


def test_framework_patch_covers_legacy_mutations(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    # Permissions per-tool (previously via legacyPatch bulk permissions root)
    resp = client.patch("/api/configuration", json={"permissions.write_file": "allow"})
    assert resp.json()[0]["ok"] is True
    # mcp.servers whole collection (previously mcp bulk)
    resp = client.patch("/api/configuration", json={"mcp.servers": {"srv": {"command": "echo hi"}}})
    assert resp.json()[0]["ok"] is True
    # agent namespace (previously agent bulk)
    resp = client.patch("/api/configuration", json={"agent": {"system_prompt": "hello", "max_steps": 12}})
    assert resp.json()[0]["ok"] is True
    # search leaves
    for k, v in [("search.backend", "brave"), ("search.brave_api_key", "test-key"), ("search.url", "http://localhost:8080")]:
        resp = client.post(f"/api/configuration/{k}", json={"value": v})
        assert resp.json().get("ok") is True, f"set {k} failed: {resp.json()}"
    # memory leaves
    resp = client.post("/api/configuration/memory.max_turns_before_summary", json={"value": 7})
    assert resp.json().get("ok") is True
    # runtime leaf
    resp = client.post("/api/configuration/runtime.temperature", json={"value": 0.9})
    assert resp.json().get("ok") is True
    # Verify persistence via snapshot
    snap = client.get("/api/configuration").json()
    assert snap["permissions"]["write_file"] == "allow"
    assert snap["mcp"]["servers"]["srv"]["command"] == "echo hi"
    assert snap["agent"]["system_prompt"] == "hello"
    assert snap["search"]["backend"] == "brave"
    assert snap["memory"]["max_turns_before_summary"] == 7


def test_whole_root_bulk_now_unknown(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    # Whole-root bulk writes that were previously allowed via config_roots must now be unknown
    for root in ["mcp", "runtime", "memory", "llm", "models", "embedding", "personality"]:
        resp = client.patch("/api/configuration", json={root: {"foo": "bar"}})
        # Should be ok:false with unknown setting error, not silently persisted
        assert resp.json()[0]["ok"] is False
        assert "unknown" in resp.json()[0].get("error", "").lower() or "unknown" in str(resp.json()[0]).lower()


def test_no_legacy_frontend_consumers():
    # Gate 4: grep zero hits outside tests/docs before deleting route
    # Allowed hits: docs and tests are excluded; source must have zero
    for path, content in [
        (API_TS, API_TS.read_text(encoding="utf-8")),
        (SETTINGS_MODAL, SETTINGS_MODAL.read_text(encoding="utf-8")),
    ]:
        assert "saveConfig" not in content, f"{path} still references saveConfig"
        assert "flushLegacy" not in content, f"{path} still references flushLegacy"
        assert "collectLeafPaths" not in content
        assert "readLeaf" not in content
        assert "legacyPatch" not in content
        # /api/config write consumers: saveConfig already checked, but also direct fetch to /api/config for writes
        # GET alias is allowed in webui_server but not in api.ts fetchConfig (which used /api/config)
        # Note: "/api/configuration" contains "/api/config" substring, so check for exact legacy endpoint
        if path == API_TS:
            assert '"/api/config"' not in content and "'/api/config'" not in content, f"{path} still references legacy /api/config endpoint"
        if path == SETTINGS_MODAL:
            assert "updateToolPermission" not in content or "framework.set" in content, f"{path} still uses legacy updateToolPermission"
            # Ensure SettingsModal uses framework.set for permissions
            assert "framework.set" in content


def test_config_roots_removed():
    src = BUILTIN.read_text(encoding="utf-8")
    # The config_roots group should be gone
    assert 'key="config_roots"' not in src
    assert "Top-level configuration roots that the legacy web UI still writes" not in src
