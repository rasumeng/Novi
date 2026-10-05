"""M3.4 — explicit-consent first-setup / missing-recommended-model flow.

Covers the consent layer around missing recommended models for Automatic mode:

* missing-model detection (catalog models Novi recommends but not installed)
* no install without explicit consent
* explicit consent starts installation
* successful installation refreshes advisory recommendations; user selection
  is never rewritten
* failed installation preserves configuration
* cancelled ("not now") installation preserves configuration
* user selection stays completely untouched by installs
* no duplicate installation requests
* embeddings never appear as a recommendation / setup item / install target

Hermetic: process config is pointed at tmp, discovery and the Ollama installer
are stubbed, and hardware is a fixed profile — no network, no Ollama.
"""

import threading
import time

import pytest

from novi.configuration.discovery import DiscoveredModel
from novi.configuration.hardware import (
    DetectionConfidence,
    GpuConfidence,
    GpuInfo,
    HardwareProfile,
)

BIG_HARDWARE = HardwareProfile(
    gpu=GpuInfo(vendor="nvidia", name="Test GPU", vram_total_gb=24.0,
                confidence=GpuConfidence.KNOWN_VRAM),
    ram_gb=64.0,
    confidence=DetectionConfidence.HIGH,
)


@pytest.fixture(autouse=True)
def _isolated_home_and_config(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HOMEDRIVE", str(tmp_path))
    monkeypatch.setenv("HOMEPATH", "")
    import novi.configuration.bootstrap as boot
    monkeypatch.setattr(boot, "CONFIG_PATH", tmp_path / ".novi" / "config.toml")
    monkeypatch.setattr(boot, "_configuration", None)
    # Deterministic hardware so recommended-but-missing is predictable.
    import novi.configuration.catalog as catalog_mod
    monkeypatch.setattr(catalog_mod, "detect_hardware", lambda: BIG_HARDWARE)


def _make_app(monkeypatch, installed_names):
    from fastapi.testclient import TestClient

    holder = {"names": list(installed_names)}
    from novi.configuration.discovery import ModelDiscovery
    monkeypatch.setattr(
        ModelDiscovery, "installed",
        lambda self: [DiscoveredModel(name=n) for n in holder["names"]],
    )
    from novi.webui_server import create_app
    return TestClient(create_app(cfg={})), holder


def _config():
    from novi.configuration.bootstrap import get_configuration
    return get_configuration()


def _available_names(payload) -> set[str]:
    return {m["name"] for m in payload["models"] if m["status"] == "available"}


# ── missing-model detection ───────────────────────────────────────────────


def test_discovery_reports_missing_recommended_models(monkeypatch):
    client, holder = _make_app(monkeypatch, ["llama3.1:8b", "nomic-embed-text"])
    payload = client.get("/api/models/discovery").json()

    available = {m["name"]: m for m in payload["models"]
                 if m["status"] == "available"}
    # The single beta tier for this hardware shows as available.
    assert set(available) == {"gemma4:e4b"}
    entry = available["gemma4:e4b"]
    assert entry["recommended"] is True
    assert entry["reasons"]
    assert entry["approxRamGb"] == 4.0
    assert entry["displayName"] == "Gemma 4 E4B"
    # Installed models are NOT reported as available.
    assert "llama3.1:8b" not in available


def test_seam_available_recommendations_is_pure_catalog_evidence(monkeypatch):
    from novi.configuration.catalog import build_available_recommendations
    recs = build_available_recommendations(
        installed_names={"llama3.1:8b"}, hardware=BIG_HARDWARE)
    names = {r["name"] for r in recs}
    assert "llama3.1:8b" not in names
    assert names == {"gemma4:e4b"}
    for r in recs:
        assert r["status"] == "available"
        assert r["recommended"] is True
        assert r["reasons"]
        assert r["approxRamGb"] is not None


def test_embeddings_never_surface_as_install_target(monkeypatch):
    from novi.configuration.catalog import build_available_recommendations
    recs = build_available_recommendations(installed_names=set(),
                                           hardware=BIG_HARDWARE)
    caps = {c for r in recs for c in r["capabilities"]}
    assert "embeddings" not in caps
    names = {r["name"] for r in recs}
    assert {"nomic-embed-text", "mxbai-embed-large"} & names == set()

    client, holder = _make_app(monkeypatch, [])
    payload = client.get("/api/models/discovery").json()
    available = _available_names(payload)
    assert "nomic-embed-text" not in available
    assert "mxbai-embed-large" not in available
    assert all("embeddings" not in m["capabilities"]
               for m in payload["models"] if m["status"] == "available")


def test_hardware_mismatch_models_are_not_recommended(monkeypatch):
    from novi.configuration.catalog import build_available_recommendations
    tiny = HardwareProfile(ram_gb=2.0, confidence=DetectionConfidence.LOW)
    recs = build_available_recommendations(installed_names=set(),
                                           hardware=tiny)
    assert [r["name"] for r in recs] == ["qwen3.5:0.8b"]


# ── consent / no-silent-install ───────────────────────────────────────────


def test_no_install_without_explicit_consent(monkeypatch):
    from novi.configuration.install import ModelInstaller
    calls = []
    monkeypatch.setattr(ModelInstaller, "pull",
                        lambda self, name, on_progress=None: calls.append(name))
    client, holder = _make_app(monkeypatch, ["llama3.1:8b"])

    # Startup + discovery + explicit recommendation refresh all mention the
    # missing model…
    payload = client.get("/api/models/discovery").json()
    assert "gemma4:e4b" in _available_names(payload)
    client.post("/api/configuration/models/recommend", json={})
    # …but none of them install anything.
    assert calls == []


def test_explicit_consent_starts_installation(monkeypatch):
    from novi.configuration.install import ModelInstaller
    calls = []
    monkeypatch.setattr(ModelInstaller, "pull",
                        lambda self, name, on_progress=None: calls.append(name))
    client, holder = _make_app(monkeypatch, ["llama3.1:8b"])

    resp = client.post("/api/models/install", json={"name": "gemma4:e4b"}).json()
    assert resp["ok"] is True
    assert calls == ["gemma4:e4b"]


def test_no_duplicate_install_requests(monkeypatch):
    from novi.configuration.install import ModelInstaller
    gate = threading.Event()
    calls = []

    def slow_pull(self, name, on_progress=None):
        calls.append(name)
        gate.wait(timeout=5)

    monkeypatch.setattr(ModelInstaller, "pull", slow_pull)
    client, holder = _make_app(monkeypatch, ["llama3.1:8b"])

    first = client.post("/api/models/install", json={"name": "gemma4:e4b"}).json()
    assert first["ok"] is True
    # Second consent while the first pull is in flight is coalesced.
    second = client.post("/api/models/install", json={"name": "gemma4:e4b"}).json()
    assert second["ok"] is True
    assert second["already_installing"] is True
    assert calls == ["gemma4:e4b"]

    gate.set()  # release the in-flight pull


# ── successful install → M3.3 convergence ─────────────────────────────────


def test_successful_install_refreshes_recommendations(monkeypatch):
    from novi.configuration.install import ModelInstaller

    def pulling(self, name, on_progress=None):
        holder["names"].append(name)
        (on_progress or self.on_progress)({"name": name, "status": "done"})

    monkeypatch.setattr(ModelInstaller, "pull", pulling)
    client, holder = _make_app(
        monkeypatch, ["qwen3:8b", "llama3.1:8b", "nomic-embed-text"])
    # selection is authoritative: set the primary model, confirm it's untouched.
    client.post("/api/configuration/models/selection", json={
        "model": "llama3.1:8b"}).json()
    assert _config().get("llm.primary_model") == "llama3.1:8b"

    # Explicit consent installs the recommended beta-tier model.
    client.post("/api/models/install", json={"name": "gemma4:e4b"})

    deadline = time.time() + 5
    while time.time() < deadline:
        payload = client.get("/api/models/discovery").json()
        if "gemma4:e4b" in payload["installedNames"]:
            break
        time.sleep(0.05)
    # The model-set lifecycle refresh ran and the new model is installed.
    assert "gemma4:e4b" in payload["installedNames"]
    rec = payload["recommended"]["primary"]
    assert "visionCapable" in rec
    # …but the user's selection was never rewritten.
    assert _config().get("llm.primary_model") == "llama3.1:8b"
    assert _config().get("models.mode", "absent") == "absent"


def test_failed_install_preserves_configuration(monkeypatch):
    from novi.configuration.install import ModelInstaller

    def failing(self, name, on_progress=None):
        (on_progress or self.on_progress)({"name": name, "status": "error",
                                           "error": "boom"})
        raise RuntimeError("pull failed")

    monkeypatch.setattr(ModelInstaller, "pull", failing)
    client, holder = _make_app(
        monkeypatch, ["qwen3:8b", "llama3.1:8b", "nomic-embed-text"])

    before = _config().snapshot()
    client.post("/api/models/install", json={"name": "gemma4:e4b"})
    time.sleep(0.5)

    after = _config().snapshot()
    assert after == before
    assert _config().get("llm.primary_model") == ""
    # The failed model never became a recommendation source.
    payload = client.get("/api/models/discovery").json()
    assert payload["recommended"]["primary"]["model"] != "gemma4:e4b"


def test_cancelled_install_preserves_configuration(monkeypatch):
    from novi.configuration.install import ModelInstaller
    calls = []
    monkeypatch.setattr(ModelInstaller, "pull",
                        lambda self, name, on_progress=None: calls.append(name))
    client, holder = _make_app(
        monkeypatch, ["qwen3:8b", "llama3.1:8b", "nomic-embed-text"])

    before = {
        "primary": _config().get("llm.primary_model"),
        "assign": _config().get("models.custom.assign", {}),
    }
    # User declines the recommended install ("not now").
    resp = client.post("/api/configuration/models/setup/dismiss",
                       json={"name": "gemma4:e4b"}).json()
    assert resp["ok"] is True

    assert calls == []  # cancelling never installs
    # The only persisted change is the dismissal itself; model configuration
    # (primary / custom assignments) is untouched.
    assert _config().get("llm.primary_model") == before["primary"]
    assert _config().get("models.custom.assign", {}) == before["assign"]
    assert _config().get("llm.primary_model") != "gemma4:e4b"

    # The choice is persisted so the setup card stops asking.
    payload = client.get("/api/models/discovery").json()
    assert "gemma4:e4b" in payload["dismissedRecommended"]
    # The model stays in the library as "available" for a later explicit install.
    assert "gemma4:e4b" in _available_names(payload)


def test_dismiss_requires_a_model_name(monkeypatch):
    client, holder = _make_app(monkeypatch, ["llama3.1:8b"])
    resp = client.post("/api/configuration/models/setup/dismiss", json={}).json()
    assert resp["ok"] is False


# ── Selection isolation ──────────────────────────────────────────────────


def test_install_never_touches_user_selection(monkeypatch):
    from novi.configuration.install import ModelInstaller

    def pulling(self, name, on_progress=None):
        holder["names"].append(name)
        (on_progress or self.on_progress)({"name": name, "status": "done"})

    monkeypatch.setattr(ModelInstaller, "pull", pulling)
    client, holder = _make_app(
        monkeypatch, ["qwen3:8b", "llama3.1:8b", "nomic-embed-text"])

    # User explicitly selected the primary model.
    client.post("/api/configuration/models/selection", json={
        "model": "llama3.1:8b"}).json()

    # An install of a recommended model completes while the selection is set.
    client.post("/api/models/install", json={"name": "gemma4:e4b"})
    time.sleep(0.5)

    assert _config().get("llm.primary_model") == "llama3.1:8b"
    # No "recommended setup" persistence wrote into the selection.
    assert _config().get("models.recommendations.dismissed", []) == []
