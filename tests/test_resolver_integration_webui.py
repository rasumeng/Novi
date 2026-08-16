"""WebUI selection/recommendation wiring (integration).

Proves the Phase 1 selection <-> recommendation split is wired to real
lifecycle points:

* startup: discovery payload exposes workloads + advisory recommendations;
  selection is NOT auto-populated (user intent is authoritative)
* ``POST /api/configuration/models/selection`` persists workloads verbatim
* ``POST /api/configuration/models/recommend`` is advisory unless ``apply``
* model install completion refreshes recommendations, never selection
* discovery payload carries workloads + recommended + visionCapable, and no
  longer exposes roles / presets / activeExperience

Hermetic: process config is pointed at tmp, model discovery and the Ollama
installer are stubbed — no network, no real user config, no Ollama.
"""

import time

import pytest

from cozmo.configuration.discovery import DiscoveredModel


@pytest.fixture(autouse=True)
def _isolated_home_and_config(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HOMEDRIVE", str(tmp_path))
    monkeypatch.setenv("HOMEPATH", "")
    import cozmo.configuration.bootstrap as boot
    monkeypatch.setattr(boot, "CONFIG_PATH", tmp_path / ".cozmo" / "config.toml")
    monkeypatch.setattr(boot, "_configuration", None)


def _make_app(monkeypatch, installed_names):
    from fastapi.testclient import TestClient

    holder = {"names": list(installed_names)}
    from cozmo.configuration.discovery import ModelDiscovery
    monkeypatch.setattr(
        ModelDiscovery, "installed",
        lambda self: [DiscoveredModel(name=n) for n in holder["names"]],
    )
    from cozmo.webui_server import create_app
    return TestClient(create_app(cfg={})), holder


def _config():
    from cozmo.configuration.bootstrap import get_configuration
    return get_configuration()


def _selection():
    return _config().get("llm.workloads", {}) or {}


def _workload_models():
    return {w: (spec or {}).get("model", "") if isinstance(spec, dict) else (spec or "")
            for w, spec in _selection().items()}


def test_startup_exposes_workloads_but_never_autofills_selection(monkeypatch):
    client, holder = _make_app(
        monkeypatch, ["llama3.1:8b", "qwen2.5-coder:7b", "nomic-embed-text"])

    snap = client.get("/api/configuration").json()
    # selection surface is present but empty — user intent, never auto-resolved
    assert snap["llm"]["workloads"]["general"]["model"] == ""
    assert snap["llm"]["workloads"]["research"]["model"] == ""
    assert snap["llm"]["workloads"]["code"]["model"] == ""

    # legacy surfaces are gone
    assert "mode" not in snap.get("models", {})
    assert "roles" not in snap["llm"]
    assert "meta" not in snap["llm"]


def test_startup_discovery_payload_has_workloads_and_recommended(monkeypatch):
    client, holder = _make_app(
        monkeypatch, ["llama3.1:8b", "qwen2.5-coder:7b", "nomic-embed-text"])
    payload = client.get("/api/models/discovery").json()
    assert set(payload["workloads"]) == {"general", "research", "code"}
    assert payload["workloads"]["general"] == ""
    assert "workloads" in payload["recommended"]
    rec = payload["recommended"]["workloads"]["general"]
    assert rec["model"] == "llama3.1:8b"
    assert "visionCapable" in rec
    # retired fields are gone
    assert "presets" not in payload
    assert "activeExperience" not in payload
    assert "roles" not in payload
    assert payload["vision_capable"] is False


def test_vision_capable_flag_derived_in_discovery(monkeypatch):
    client, holder = _make_app(monkeypatch, ["qwen2.5vl:7b"])
    payload = client.get("/api/models/discovery").json()
    assert payload["recommended"]["workloads"]["general"]["model"] == "qwen2.5vl:7b"
    assert payload["recommended"]["workloads"]["general"]["visionCapable"] is True
    # no selection yet -> vision_capable must be False (never implied)
    assert payload["vision_capable"] is False

    # select a vision-capable general model -> flag derived from selection
    client.post("/api/configuration/models/selection", json={
        "workloads": {"general": "qwen2.5vl:7b", "research": "", "code": ""}})
    payload = client.get("/api/models/discovery").json()
    assert payload["vision_capable"] is True


def test_selection_get_and_post(monkeypatch):
    client, holder = _make_app(monkeypatch, ["llama3.1:8b", "qwen2.5-coder:7b"])

    # empty by default
    got = client.get("/api/configuration/models/selection").json()
    assert got == {"ok": True, "workloads": {
        "general": "", "research": "", "code": ""}}

    # persist verbatim
    resp = client.post("/api/configuration/models/selection", json={
        "workloads": {"general": "llama3.1:8b", "research": "not-installed:model",
                      "code": "qwen2.5-coder:7b"},
    }).json()
    assert resp["ok"] is True
    assert resp["workloads"]["general"]["status"] == "installed"
    assert resp["workloads"]["research"]["status"] == "not-installed"

    assert _workload_models()["general"] == "llama3.1:8b"
    assert _workload_models()["research"] == "not-installed:model"
    assert _workload_models()["code"] == "qwen2.5-coder:7b"

    # survives reload
    import cozmo.configuration.bootstrap as boot
    monkeypatch.setattr(boot, "_configuration", None)
    got2 = client.get("/api/configuration/models/selection").json()
    assert got2["workloads"]["general"] == "llama3.1:8b"


def test_selection_never_auto_resolved_at_startup(monkeypatch):
    # Even with a full trusted set installed, selection stays empty until the
    # user explicitly selects — recommendations are advisory.
    client, holder = _make_app(
        monkeypatch, ["qwen3:8b", "qwen2.5vl:7b", "nomic-embed-text"])
    payload = client.get("/api/models/discovery").json()
    assert payload["recommended"]["workloads"]["general"]["model"] == "qwen3:8b"
    assert payload["workloads"]["general"] == ""
    assert _config().get("llm.workloads.general.model") == ""


def test_recommend_is_advisory_unless_applied(monkeypatch):
    client, holder = _make_app(monkeypatch, ["llama3.1:8b", "qwen2.5-coder:7b"])

    # advisory: recommendations returned, selection untouched
    resp = client.post("/api/configuration/models/recommend", json={}).json()
    assert resp["ok"] is True
    assert resp["workloads"]["general"]["model"] == "llama3.1:8b"
    assert _config().get("llm.workloads.general.model") == ""

    # apply=true: recommendations written via the verbatim selection path
    resp2 = client.post("/api/configuration/models/recommend",
                        json={"apply": True}).json()
    assert resp2["ok"] is True
    assert "selection" in resp2
    assert _config().get("llm.workloads.general.model") == "llama3.1:8b"
    assert _config().get("llm.workloads.code.model") == "qwen2.5-coder:7b"


def test_install_completion_refreshes_recommendations_not_selection(monkeypatch):
    from cozmo.configuration.install import ModelInstaller
    monkeypatch.setattr(
        ModelInstaller, "pull",
        lambda self, name, on_progress=None: (
            (on_progress or self.on_progress)(
                {"name": name, "status": "done"}))
    )
    client, holder = _make_app(monkeypatch, ["llama3.1:8b", "nomic-embed-text"])
    client.post("/api/configuration/models/selection", json={
        "workloads": {"general": "llama3.1:8b", "research": "",
                      "code": ""}}).json()
    assert _config().get("llm.workloads.general.model") == "llama3.1:8b"

    # install qwen3:8b -> advisory recommendations change...
    holder["names"] += ["qwen3:8b"]
    client.post("/api/models/install", json={"name": "qwen3:8b"})

    deadline = time.time() + 5
    while time.time() < deadline:
        payload = client.get("/api/models/discovery").json()
        if payload["recommended"]["workloads"]["general"]["model"] == "qwen3:8b":
            break
        time.sleep(0.05)
    assert payload["recommended"]["workloads"]["general"]["model"] == "qwen3:8b"
    # ...but selection is never rewritten
    assert _config().get("llm.workloads.general.model") == "llama3.1:8b"


def test_recommend_endpoint_never_installs(monkeypatch):
    from fastapi.testclient import TestClient
    from cozmo.configuration.install import ModelInstaller
    calls = []
    monkeypatch.setattr(ModelInstaller, "pull",
                        lambda self, name, on_progress=None: calls.append(name))
    client, holder = _make_app(monkeypatch, ["qwen3:8b"])

    client.post("/api/configuration/models/recommend", json={"apply": True})
    assert calls == []  # recommendation must never pull/download models