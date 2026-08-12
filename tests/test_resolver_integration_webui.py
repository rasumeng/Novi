"""M3.3 — WebUI trigger wiring (integration).

Proves the recomputation seam is connected to real lifecycle points:

* startup: ``create_app`` reconciles ``llm.roles.*`` while Automatic is active
* explicit discovery refresh: ``POST /api/configuration/models/recompute``
  re-resolves on model removal / hardware refresh
* model install completion: the background install thread runs the seam
* Custom mode: all recomputation is a strict NOOP
* idempotency: a second no-op recompute changes nothing

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


def test_startup_reconciles_automatic_roles(monkeypatch):
    client, holder = _make_app(
        monkeypatch, ["llama3.1:8b", "qwen2.5-coder:7b", "nomic-embed-text"])

    snap = client.get("/api/configuration").json()
    assert snap["models"]["mode"] == "automatic"
    assert snap["llm"]["meta"]["source"] == "automatic"
    assert snap["llm"]["roles"]["chat"]["model"] == "llama3.1:8b"
    assert snap["llm"]["roles"]["coder"]["model"] == "qwen2.5-coder:7b"


def test_recompute_endpoint_reresolves_on_model_removal_or_refresh(monkeypatch):
    client, holder = _make_app(
        monkeypatch, ["qwen3:8b", "qwen2.5vl:7b", "nomic-embed-text"])
    snap = client.get("/api/configuration").json()
    assert snap["llm"]["roles"]["chat"]["model"] == "qwen3:8b"

    # qwen3:8b disappears (e.g. removed out-of-band) -> explicit refresh.
    holder["names"] = ["llama3.1:8b", "qwen2.5-coder:7b", "nomic-embed-text"]
    resp = client.post("/api/configuration/models/recompute", json={}).json()
    assert resp["ok"] is True
    assert resp["mode"] == "automatic"

    snap2 = client.get("/api/configuration").json()
    assert snap2["llm"]["roles"]["chat"]["model"] == "llama3.1:8b"
    assert snap2["llm"]["roles"]["coder"]["model"] == "qwen2.5-coder:7b"


def test_install_completion_triggers_recompute(monkeypatch):
    from cozmo.configuration.install import ModelInstaller
    monkeypatch.setattr(
        ModelInstaller, "pull",
        lambda self, name, on_progress=None: (
            (on_progress or self.on_progress)(
                {"name": name, "status": "done"}))
    )
    client, holder = _make_app(
        monkeypatch, ["llama3.1:8b", "qwen2.5-coder:7b", "nomic-embed-text"])
    assert _config().get("llm.roles.chat.model") == "llama3.1:8b"

    # install begins; once the (stubbed) pull completes Ollama reports it.
    holder["names"] += ["qwen3:8b"]
    client.post("/api/models/install", json={"name": "qwen3:8b"})

    deadline = time.time() + 5
    while time.time() < deadline:
        if _config().get("llm.roles.chat.model") == "qwen3:8b":
            break
        time.sleep(0.05)
    assert _config().get("llm.roles.chat.model") == "qwen3:8b"
    assert _config().get("models.mode") == "automatic"


def test_custom_mode_recompute_is_noop(monkeypatch):
    from cozmo.configuration import resolver
    client, holder = _make_app(
        monkeypatch, ["qwen3:8b", "qwen2.5vl:7b", "nomic-embed-text"])

    enter = client.post("/api/configuration/models/state", json={
        "mode": "custom",
        "assign": {"chat": "qwen3:8b", "coding": "qwen2.5vl:7b"},
    }).json()
    assert enter["ok"] is True
    assert _config().get("models.mode") == "custom"
    assert _config().get("llm.roles.chat.model") == "qwen3:8b"

    # A new/removal/hardware refresh while custom -> nothing changes.
    holder["names"] = ["llama3.1:8b", "nomic-embed-text"]
    before_chat = _config().get("llm.roles.chat.model")
    resp = client.post("/api/configuration/models/recompute", json={}).json()
    assert resp["ok"] is True
    assert resp["mode"] == "custom"
    assert _config().get("models.mode") == "custom"
    assert _config().get("llm.meta.source") == "custom"
    assert _config().get("models.custom.assign.chat") == "qwen3:8b"
    # intent is preserved even though the model vanished from discovery
    assert _config().get("llm.roles.chat.model") == before_chat

    # resolver itself confirms Custom recompute NEVER runs apply_automatic
    res, changed = resolver.recompute_automatic_if_active(
        _config(), installed=["qwen3:8b"], hardware=None)
    assert res is None and changed is False


def test_noop_recompute_is_idempotent(monkeypatch):
    client, holder = _make_app(
        monkeypatch, ["qwen3:8b", "qwen2.5vl:7b", "nomic-embed-text"])
    snap1 = client.get("/api/configuration").json()

    # Twice-unchanged recompute -> identical state, no churn, endpoint healthy.
    client.post("/api/configuration/models/recompute", json={})
    client.post("/api/configuration/models/recompute", json={})
    snap2 = client.get("/api/configuration").json()
    assert snap2 == snap1


def test_recompute_endpoint_never_installs(monkeypatch):
    from fastapi.testclient import TestClient
    from cozmo.configuration.install import ModelInstaller
    calls = []
    monkeypatch.setattr(ModelInstaller, "pull",
                        lambda self, name, on_progress=None: calls.append(name))
    client, holder = _make_app(monkeypatch, ["qwen3:8b"])

    client.post("/api/configuration/models/recompute", json={})
    assert calls == []  # recomputation must never pull/download models