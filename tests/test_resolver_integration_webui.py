"""WebUI selection/recommendation wiring (integration).

Proves the single-primary-model selection <-> recommendation split is wired
to real lifecycle points:

* startup: discovery payload exposes primary + advisory recommendation;
  selection is NOT auto-populated (user intent is authoritative)
* ``POST /api/configuration/models/selection`` persists primary verbatim
* ``POST /api/configuration/models/recommend`` is advisory unless ``apply``
* model install completion refreshes recommendations, never selection
* discovery payload carries primary + recommended + visionCapable, and no
  longer exposes roles / presets / activeExperience / workloads.*

Hermetic: process config is pointed at tmp, model discovery and the Ollama
installer are stubbed — no network, no real user config, no Ollama.
"""

import time

import pytest

from novi.configuration.discovery import DiscoveredModel


@pytest.fixture(autouse=True)
def _isolated_home_and_config(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HOMEDRIVE", str(tmp_path))
    monkeypatch.setenv("HOMEPATH", "")
    import novi.configuration.bootstrap as boot
    monkeypatch.setattr(boot, "CONFIG_PATH", tmp_path / ".novi" / "config.toml")
    monkeypatch.setattr(boot, "_configuration", None)


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


def _primary():
    return _config().get("llm.primary_model", "") or ""


def test_startup_exposes_primary_but_never_autofills_selection(monkeypatch):
    client, holder = _make_app(
        monkeypatch, ["llama3.1:8b", "qwen2.5-coder:7b", "nomic-embed-text"])

    snap = client.get("/api/configuration").json()
    # selection surface is present but empty — user intent, never auto-resolved
    assert snap["llm"]["primary_model"] == ""
    assert "workloads" not in snap["llm"]

    # legacy surfaces are gone
    assert "mode" not in snap.get("models", {})
    assert "roles" not in snap["llm"]
    assert "meta" not in snap["llm"]


def test_startup_discovery_payload_has_primary_and_recommended(monkeypatch):
    client, holder = _make_app(
        monkeypatch, ["llama3.1:8b", "qwen2.5-coder:7b", "nomic-embed-text"])
    payload = client.get("/api/models/discovery").json()
    assert payload["primary"] == ""
    assert payload["model"] == ""
    assert "primary" in payload["recommended"]
    rec = payload["recommended"]["primary"]
    assert rec["model"] == "llama3.1:8b"
    assert "visionCapable" in rec
    # structured explanation is additive and exposed to the UI
    exp = rec["explanation"]
    assert set(exp.keys()) == {"provenance", "hardwareFit", "alternatives", "provisional"}
    assert exp["provenance"]["source"]
    assert exp["hardwareFit"]["fit"] in ("fits", "does_not_fit", "unknown")
    assert exp["provisional"] == payload["recommended"]["provisional"]
    # retired fields are gone
    assert "presets" not in payload
    assert "activeExperience" not in payload
    assert "roles" not in payload
    assert payload["vision_capable"] is False


def test_discovery_hardware_payload_surfaces_gpu_and_confidence(monkeypatch):
    client, holder = _make_app(monkeypatch, ["llama3.1:8b"])
    payload = client.get("/api/models/discovery").json()
    hw = payload["hardware"]
    # ramGb preserved for backward compatibility
    assert "ramGb" in hw
    # structured GPU facts + overall detection confidence
    assert set(hw["gpu"].keys()) == {"name", "vramTotalGb", "vendor"}
    assert isinstance(hw["gpu"]["name"], str)
    assert isinstance(hw["gpu"]["vendor"], str)
    # VRAM is known-or-unknown, never a fabricated number
    assert hw["gpu"]["vramTotalGb"] is None or isinstance(hw["gpu"]["vramTotalGb"], (int, float))
    assert hw["confidence"] in ("high", "medium", "low", "unknown")


def test_discovery_hardware_unknown_vram_stays_unknown(monkeypatch):
    import novi.configuration.catalog as catalog
    from novi.configuration.catalog import ModelRecommendationEngine
    from novi.configuration.hardware import (
        DetectionConfidence,
        GpuConfidence,
        GpuInfo,
        HardwareProfile,
    )
    unknown_vram_hw = HardwareProfile(
        gpu=GpuInfo(
            vendor="nvidia",
            name="NVIDIA GeForce RTX 4060",
            vram_total_gb=None,          # detection could not determine VRAM
            confidence=GpuConfidence.KNOWN_NO_VRAM,
        ),
        ram_gb=16.0,
        confidence=DetectionConfidence.MEDIUM,
    )
    monkeypatch.setattr(
        catalog, "ModelRecommendationEngine",
        lambda: ModelRecommendationEngine(hardware=unknown_vram_hw),
    )
    payload = catalog.build_catalog_payload([])
    hw = payload["hardware"]
    assert hw["ramGb"] == 16.0
    assert hw["gpu"]["name"] == "NVIDIA GeForce RTX 4060"
    assert hw["gpu"]["vendor"] == "nvidia"
    assert hw["gpu"]["vramTotalGb"] is None  # unknown, never invented
    assert hw["confidence"] == "medium"


def test_vision_capable_flag_derived_in_discovery(monkeypatch):
    client, holder = _make_app(monkeypatch, ["qwen2.5vl:7b"])
    payload = client.get("/api/models/discovery").json()
    assert payload["recommended"]["primary"]["model"] == "qwen2.5vl:7b"
    assert payload["recommended"]["primary"]["visionCapable"] is True
    # no selection yet -> vision_capable must be False (never implied)
    assert payload["vision_capable"] is False

    # select a vision-capable primary model -> flag derived from selection
    client.post("/api/configuration/models/selection", json={
        "model": "qwen2.5vl:7b"})
    payload = client.get("/api/models/discovery").json()
    assert payload["vision_capable"] is True


def test_selection_get_and_post(monkeypatch):
    client, holder = _make_app(monkeypatch, ["llama3.1:8b", "qwen2.5-coder:7b"])

    # empty by default
    got = client.get("/api/configuration/models/selection").json()
    assert got["ok"] is True
    assert got["model"] == ""
    assert got["primary"] == ""

    # persist verbatim. The save path is deliberately I/O-free: it never does a
    # blocking installed-model lookup, so the entry reports "configured"
    # (persisted). Availability is surfaced by the separate discovery endpoint.
    resp = client.post("/api/configuration/models/selection", json={
        "model": "llama3.1:8b",
    }).json()
    assert resp["ok"] is True
    assert resp["model"] == "llama3.1:8b"
    assert resp["status"] == "configured"

    assert _primary() == "llama3.1:8b"

    # survives reload
    import novi.configuration.bootstrap as boot
    monkeypatch.setattr(boot, "_configuration", None)
    got2 = client.get("/api/configuration/models/selection").json()
    assert got2["model"] == "llama3.1:8b"


def test_selection_never_auto_resolved_at_startup(monkeypatch):
    # Even with a full trusted set installed, selection stays empty until the
    # user explicitly selects — recommendations are advisory.
    client, holder = _make_app(
        monkeypatch, ["qwen3:8b", "qwen2.5vl:7b", "nomic-embed-text"])
    payload = client.get("/api/models/discovery").json()
    assert payload["recommended"]["primary"]["model"] == "qwen3:8b"
    assert payload["primary"] == ""
    assert _config().get("llm.primary_model") == ""


def test_recommend_is_advisory_unless_applied(monkeypatch):
    client, holder = _make_app(monkeypatch, ["llama3.1:8b", "qwen2.5-coder:7b"])

    # advisory: recommendations returned, selection untouched
    resp = client.post("/api/configuration/models/recommend", json={}).json()
    assert resp["ok"] is True
    assert resp["primary"]["model"] == "llama3.1:8b"
    assert _config().get("llm.primary_model") == ""

    # apply=true: recommendation written via the verbatim selection path
    resp2 = client.post("/api/configuration/models/recommend",
                        json={"apply": True}).json()
    assert resp2["ok"] is True
    assert "selection" in resp2
    assert _config().get("llm.primary_model") == "llama3.1:8b"


def test_install_completion_refreshes_recommendations_not_selection(monkeypatch):
    from novi.configuration.install import ModelInstaller
    monkeypatch.setattr(
        ModelInstaller, "pull",
        lambda self, name, on_progress=None: (
            (on_progress or self.on_progress)(
                {"name": name, "status": "done"}))
    )
    client, holder = _make_app(monkeypatch, ["llama3.1:8b", "nomic-embed-text"])
    client.post("/api/configuration/models/selection", json={
        "model": "llama3.1:8b"}).json()
    assert _config().get("llm.primary_model") == "llama3.1:8b"

    # install qwen3:8b -> advisory recommendations change...
    holder["names"] += ["qwen3:8b"]
    client.post("/api/models/install", json={"name": "qwen3:8b"})

    deadline = time.time() + 5
    while time.time() < deadline:
        payload = client.get("/api/models/discovery").json()
        if payload["recommended"]["primary"]["model"] == "qwen3:8b":
            break
        time.sleep(0.05)
    assert payload["recommended"]["primary"]["model"] == "qwen3:8b"
    # ...but selection is never rewritten
    assert _config().get("llm.primary_model") == "llama3.1:8b"


def test_recommend_endpoint_never_installs(monkeypatch):
    from novi.configuration.install import ModelInstaller
    calls = []
    monkeypatch.setattr(ModelInstaller, "pull",
                        lambda self, name, on_progress=None: calls.append(name))
    client, holder = _make_app(monkeypatch, ["qwen3:8b"])

    client.post("/api/configuration/models/recommend", json={"apply": True})
    assert calls == []  # recommendation must never pull/download models


def _primary_snapshot(client):
    snap = client.get("/api/configuration").json()
    return snap["llm"]["primary_model"]


def _walk_keys(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{prefix}{k}"
            yield from _walk_keys(v, f"{prefix}{k}.")


def _has_retired_segment(key):
    forbidden = {"mode", "auto", "roles", "presets", "activeExperience", "workloads"}
    return any(seg.lower() in {f.lower() for f in forbidden} for seg in key.split("."))


def test_selection_never_introduces_retired_keys(monkeypatch):
    client, holder = _make_app(
        monkeypatch, ["llama3.1:8b", "qwen2.5-coder:7b", "nomic-embed-text"])
    client.post("/api/configuration/models/selection", json={
        "model": "llama3.1:8b"})
    client.post("/api/configuration/models/recommend", json={"apply": True})
    snap = client.get("/api/configuration").json()
    for key in _walk_keys(snap):
        assert not _has_retired_segment(key), key


# ── Model deletion ──────────────────────────────────────────────────────────


def _patch_delete(monkeypatch, ok: bool):
    import novi.configuration.install as install
    monkeypatch.setattr(install, "delete_model", lambda name, url: ok)


def test_delete_calls_ollama_delete_and_reports_success(monkeypatch):
    import novi.configuration.install as install
    calls = []
    monkeypatch.setattr(install, "delete_model",
                        lambda name, url: calls.append((name, url)) or True)
    client, _ = _make_app(monkeypatch, ["qwen3:8b"])
    resp = client.post("/api/models/delete", json={"name": "qwen3:8b"}).json()
    assert resp == {"ok": True, "name": "qwen3:8b"}
    assert len(calls) == 1
    assert calls[0][0] == "qwen3:8b"


def test_delete_triggers_discovery_refresh(monkeypatch):
    import novi.configuration.discovery as discovery
    import novi.configuration.install as install
    import novi.configuration.resolver as resolver
    monkeypatch.setattr(install, "delete_model", lambda name, url: True)
    invalidated = []
    monkeypatch.setattr(discovery, "invalidate_cache",
                        lambda *a, **k: invalidated.append(1))
    recomputed = []
    _orig_recommend = resolver.recommend
    monkeypatch.setattr(resolver, "recommend",
                        lambda installed=None, **kw: recomputed.append(1) or _orig_recommend(installed=installed, **kw))
    client, _ = _make_app(monkeypatch, ["qwen3:8b"])
    invalidated.clear()
    recomputed.clear()
    client.post("/api/models/delete", json={"name": "qwen3:8b"})
    assert invalidated, "delete must invalidate the metadata cache"
    assert recomputed, "delete must refresh advisory recommendations"


def test_delete_selected_model_keeps_selection_intact(monkeypatch):
    _patch_delete(monkeypatch, True)
    client, _ = _make_app(monkeypatch, ["qwen3:8b", "llama3.1:8b"])
    client.post("/api/configuration/models/selection", json={"model": "qwen3:8b"})
    resp = client.post("/api/models/delete", json={"name": "qwen3:8b"}).json()
    assert resp["ok"] is True
    assert _primary_snapshot(client) == "qwen3:8b"


def test_deleted_selected_model_is_reported_missing(monkeypatch):
    _patch_delete(monkeypatch, True)
    client, holder = _make_app(monkeypatch, ["qwen3:8b", "llama3.1:8b"])
    client.post("/api/configuration/models/selection", json={"model": "qwen3:8b"})
    # the daemon no longer has qwen3:8b
    holder["names"] = ["llama3.1:8b"]
    client.post("/api/models/delete", json={"name": "qwen3:8b"})
    payload = client.get("/api/models/discovery").json()
    assert "qwen3:8b" in payload["missingModels"]
    assert "qwen3:8b" not in payload["installedNames"]
    assert payload["primary"] == "qwen3:8b"


def test_delete_unselected_model_leaves_selection_unchanged(monkeypatch):
    _patch_delete(monkeypatch, True)
    client, _ = _make_app(monkeypatch, ["qwen3:8b", "llama3.1:8b"])
    client.post("/api/configuration/models/selection", json={"model": "llama3.1:8b"})
    before = _primary_snapshot(client)
    client.post("/api/models/delete", json={"name": "qwen3:8b"})
    assert _primary_snapshot(client) == before


def test_delete_failure_leaves_configuration_untouched(monkeypatch):
    _patch_delete(monkeypatch, False)
    client, _ = _make_app(monkeypatch, ["qwen3:8b"])
    client.post("/api/configuration/models/selection", json={"model": "qwen3:8b"})
    resp = client.post("/api/models/delete", json={"name": "qwen3:8b"}).json()
    assert resp["ok"] is False
    assert resp["name"] == "qwen3:8b"
    assert _config().get("llm.primary_model") == "qwen3:8b"
    # discovery still lists the model as installed (nothing changed)
    payload = client.get("/api/models/discovery").json()
    assert "qwen3:8b" in payload["installedNames"]


def test_delete_rejects_empty_or_missing_name(monkeypatch):
    import novi.configuration.install as install
    calls = []
    monkeypatch.setattr(install, "delete_model",
                        lambda name, url: calls.append(name) or True)
    client, _ = _make_app(monkeypatch, ["qwen3:8b"])
    assert client.post("/api/models/delete", json={"name": ""}).json()["ok"] is False
    assert client.post("/api/models/delete", json={"name": "   "}).json()["ok"] is False
    assert client.post("/api/models/delete", json={}).json()["ok"] is False
    assert calls == []


def test_recommendations_may_change_after_delete_but_never_autoapplied(monkeypatch):
    _patch_delete(monkeypatch, True)
    client, holder = _make_app(monkeypatch, ["qwen3:8b"])
    client.post("/api/configuration/models/selection", json={"model": "qwen3:8b"})
    before = client.get("/api/models/discovery").json()
    assert before["recommended"]["primary"]["model"] == "qwen3:8b"
    # after deletion the available set changed: recommendation can move on…
    holder["names"] = ["llama3.1:8b"]
    client.post("/api/models/delete", json={"name": "qwen3:8b"})
    after = client.get("/api/models/discovery").json()
    assert after["recommended"]["primary"]["model"] == "llama3.1:8b"
    # …but the persisted selection is untouched and the model is reported missing
    assert after["primary"] == "qwen3:8b"
    assert after["model"] == "qwen3:8b"
    assert "qwen3:8b" in after["missingModels"]
