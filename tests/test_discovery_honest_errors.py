"""Task 2.1 — honest Ollama discovery failure."""

import pytest
from fastapi.testclient import TestClient

from novi.configuration.discovery import _CACHE, ModelDiscovery


@pytest.fixture(autouse=True)
def _clear_cache():
    _CACHE.clear()
    # reset global error
    try:
        import novi.configuration.runtime_inventory as ri

        ri._last_tags_error = None  # type: ignore[attr-defined]
    except Exception:
        pass
    yield
    _CACHE.clear()
    try:
        import novi.configuration.runtime_inventory as ri

        ri._last_tags_error = None  # type: ignore[attr-defined]
    except Exception:
        pass


def _app_client(monkeypatch=None):
    # Build TestClient with isolated config; discovery will use patched seam
    from novi.webui_server import create_app
    from novi.configuration.bootstrap import get_configuration

    # Ensure ollama.url is default for these tests
    cfg = get_configuration()
    try:
        cfg.set("ollama.url", "http://localhost:11434", by="test")
    except Exception:
        pass
    app = create_app()
    return TestClient(app)


def test_daemon_down_empty_returns_error_field(monkeypatch):
    monkeypatch.setattr("novi.configuration.discovery.query_ollama_tags", lambda url, timeout=5.0: [])
    # Ensure no stale cache
    _CACHE.clear()
    client = _app_client()
    r = client.get("/api/models/discovery")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "error"
    assert data["ollamaReachable"] is False
    assert data["ollamaUrl"] == "http://localhost:11434"
    assert "ollamaError" in data
    assert "not reachable" in data["ollamaError"].lower() or "ollama" in data["ollamaError"].lower()
    assert data["modelsStale"] is False
    # additive — original fields still present
    assert "models" in data
    assert "hardware" in data


def test_daemon_down_with_stale_cache_returns_degraded_and_stale_models(monkeypatch):
    monkeypatch.setattr("novi.configuration.discovery.query_ollama_tags", lambda url, timeout=5.0: [])
    # Prevent startup background thread from wiping cache
    monkeypatch.setattr("novi.configuration.discovery.invalidate_cache", lambda url=None, name=None: None)
    client = _app_client()
    # Seed cache AFTER app creation (background invalidate would have cleared)
    _CACHE.set("http://localhost:11434", "cached-model:7b", {
        "capabilities": ["vision"],
        "details": {"family": "qwen"},
        "model_info": {},
    })
    r = client.get("/api/models/discovery")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "degraded"
    assert data["ollamaReachable"] is False
    assert data["modelsStale"] is True
    assert "ollamaError" in data
    # stale models still returned (at least the cached one appears as installed or surfaced)
    # build_catalog_payload turns stale records into installed models
    installed_names = data.get("installedNames", [])
    # cached-model should be in installedNames or models list
    assert "cached-model:7b" in installed_names or any(m["name"] == "cached-model:7b" for m in data["models"])
    # stale flag on at least one model
    assert any(m.get("stale") for m in data["models"] if m["name"] == "cached-model:7b")


def test_reachable_case_status_ok(monkeypatch):
    def fake_tags(url, timeout=5.0):
        return [{"name": "live-model:7b", "details": {"family": "llama"}, "size": 1000}]

    def fake_show(url, name, timeout=5.0):
        return {"capabilities": ["tools"], "details": {"family": "llama"}, "model_info": {}}

    monkeypatch.setattr("novi.configuration.discovery.query_ollama_tags", fake_tags)
    monkeypatch.setattr("novi.configuration.discovery.query_ollama_show", fake_show)
    client = _app_client()
    r = client.get("/api/models/discovery")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["ollamaReachable"] is True
    assert data["ollamaUrl"] == "http://localhost:11434"
    assert data["modelsStale"] is False
    # error absent or empty when ok
    assert "ollamaError" not in data or not data["ollamaError"]


def test_error_shape_additive(monkeypatch):
    monkeypatch.setattr("novi.configuration.discovery.query_ollama_tags", lambda url, timeout=5.0: [])
    client = _app_client()
    data = client.get("/api/models/discovery").json()
    # All additive fields present with correct types
    assert data["status"] in ("ok", "degraded", "error")
    assert isinstance(data["ollamaReachable"], bool)
    assert isinstance(data["ollamaUrl"], str)
    assert isinstance(data["modelsStale"], bool)
    if "ollamaError" in data:
        assert isinstance(data["ollamaError"], str)
        assert len(data["ollamaError"]) > 0
    # Original contract still intact
    for key in ("hardware", "models", "missingModels", "installedNames", "workloads", "recommended"):
        assert key in data


def test_discovery_installed_exposes_reachable_and_error(monkeypatch):
    # Direct ModelDiscovery surface
    monkeypatch.setattr("novi.configuration.discovery.query_ollama_tags", lambda url, timeout=5.0: [])
    disc = ModelDiscovery("http://localhost:11434")
    result = disc.installed()
    assert result == []
    assert disc.last_reachable is False
    assert disc.last_error is not None
    assert "not reachable" in disc.last_error.lower()
    assert disc.last_models_stale is False

    # With stale cache, stale flagged
    _CACHE.set("http://localhost:11434", "stale-a:7b", {"capabilities": []})
    disc2 = ModelDiscovery("http://localhost:11434")
    result2 = disc2.installed()
    assert len(result2) == 1
    assert disc2.last_reachable is False
    assert disc2.last_models_stale is True
    assert result2[0].stale is True


def test_stale_not_authoritative_for_runtime_validation(monkeypatch):
    """Stale discovery does not change runtime capability validation (still uses cache/live)."""
    _CACHE.set("http://localhost:11434", "stale-vision:7b", {"capabilities": ["vision"]})
    monkeypatch.setattr("novi.configuration.discovery.query_ollama_tags", lambda url, timeout=5.0: [])
    disc = ModelDiscovery("http://localhost:11434")
    stale_models = disc.installed()
    assert stale_models[0].stale is True
    # Runtime capability check should still see vision via cached_runtime_capabilities (not via stale flag)
    from novi.runtime.model_selector import model_capability_state
    from novi.configuration.model_records import CapabilityState

    assert model_capability_state("stale-vision:7b", "vision") == CapabilityState.SUPPORTED
