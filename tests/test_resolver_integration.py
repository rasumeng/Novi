"""Model selection integration tests (Phase 1).

Covers the boundary between pure advisory recommendations and persistent,
authoritative selection:

* Selection is user intent persisted verbatim to ``llm.workloads.*`` and is
  never rewritten by installs, removals, or hardware refreshes.
* Recommendation is advisory: it reflects the current installed set, but
  computing it never touches configuration.
* No silent substitution: an uninstalled model stays selected and is reported
  ``not-installed``.
* Empty-evidence (cold start) never fabricates or wipes selection.
* Runtime application: selection writes reach the runtime apply-hook path.
* Nothing here ever installs or downloads models.

Hermetic: in-memory / tmp_path config; no network, no Ollama, no services.
"""

from cozmo.configuration.bootstrap import build_registry, DEFAULT_CONFIG
from cozmo.configuration.hardware import (
    DetectionConfidence,
    GpuConfidence,
    GpuInfo,
    HardwareProfile,
)
from cozmo.configuration.manager import Configuration
from cozmo.configuration.resolver import (
    WORKLOADS,
    recommend,
    apply_selection,
)


def hw(gpu="", vram=None, gpu_conf=GpuConfidence.UNKNOWN, ram=None,
       conf=DetectionConfidence.UNKNOWN) -> HardwareProfile:
    return HardwareProfile(
        gpu=GpuInfo(vendor="nvidia" if gpu else "", name=gpu,
                    vram_total_gb=vram, confidence=gpu_conf),
        ram_gb=ram, confidence=conf,
    )


HW_HIGH = hw("RTX 4060", 8.0, GpuConfidence.KNOWN_VRAM, 32.0, DetectionConfidence.HIGH)
HW_BIG = hw("RTX 4090", 24.0, GpuConfidence.KNOWN_VRAM, 64.0, DetectionConfidence.HIGH)


def _make_cfg(tmp_path, bus=None, reg=None):
    reg = reg or build_registry()
    cfg = Configuration(reg, tmp_path / "cozmo.toml", defaults=DEFAULT_CONFIG,
                        bus=bus)
    cfg.initialize()
    return cfg


def _selection(cfg):
    return {w: cfg.get(f"llm.workloads.{w}.model", "") for w in WORKLOADS}


# ── Selection is persistent and authoritative ─────────────────────────────


def test_selection_persists_across_reload(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, {"general": "llama3", "research": "gemma2",
                          "code": "qwen2.5-coder:7b"})
    cfg2 = _make_cfg(tmp_path)
    assert _selection(cfg2) == {"general": "llama3", "research": "gemma2",
                                "code": "qwen2.5-coder:7b"}


def test_install_never_rewrites_selection(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, {"general": "llama3", "research": "gemma2",
                          "code": ""})
    before = _selection(cfg)

    # a trusted model appears in the installed set -> advisory recommendations
    # change, but the persisted selection is untouched.
    recs = recommend(HW_HIGH, ["llama3", "gemma2", "qwen3:8b"])
    assert recs.workloads["general"].model == "qwen3:8b"
    assert _selection(cfg) == before
    assert cfg.get("llm.workloads.general.model") == "llama3"


def test_removal_never_rewrites_selection(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, {"general": "gone:model", "research": "",
                          "code": "llama3"})
    before = _selection(cfg)

    # model removal changes recommendations (advisory), selection stays.
    recs = recommend(HW_HIGH, [])
    assert recs.workloads["general"].model == ""
    assert _selection(cfg) == before
    assert cfg.get("llm.workloads.general.model") == "gone:model"


def test_hardware_change_never_rewrites_selection(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, {"general": "gemma4", "research": "gemma4",
                          "code": ""})
    before = _selection(cfg)
    installed = ["gemma4", "llama3.1:8b"]
    recs_small = recommend(HW_HIGH, installed)      # gemma4 demoted
    recs_big = recommend(HW_BIG, installed)         # not demoted
    assert recs_small.workloads["research"].model == "llama3.1:8b"
    assert recs_big.workloads["research"].model == "gemma4"
    assert _selection(cfg) == before


# ── No silent substitution ────────────────────────────────────────────────


def test_not_installed_selection_is_kept_and_reported(tmp_path):
    cfg = _make_cfg(tmp_path)
    out = apply_selection(cfg, {"general": "missing:model", "research": "",
                                "code": ""}, installed=["llama3"])
    assert cfg.get("llm.workloads.general.model") == "missing:model"
    assert out["workloads"]["general"]["status"] == "not-installed"
    # no automatic fallback substituted into config
    assert cfg.get("llm.workloads.general.model") != "llama3"


def test_empty_discovery_never_fabricates_selection(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, {"general": "", "research": "", "code": ""})
    recs = recommend(HW_HIGH, [])
    assert all(recs.workloads[w].model == "" for w in WORKLOADS)
    assert all(cfg.get(f"llm.workloads.{w}.model") == "" for w in WORKLOADS)


# ── Advisory recommendations track the installed set ──────────────────────


def test_recommendations_reflect_installed_set_change(tmp_path):
    cfg = _make_cfg(tmp_path)
    r1 = recommend(HW_HIGH, ["llama3.1:8b"])
    r2 = recommend(HW_HIGH, ["llama3.1:8b", "qwen3:8b"])
    assert r1.workloads["general"].model == "llama3.1:8b"
    assert r2.workloads["general"].model == "qwen3:8b"
    # config unchanged by either computation
    assert _selection(cfg) == {"general": "", "research": "", "code": ""}


def test_recommendation_contains_derived_evidence_only():
    recs = recommend(HW_HIGH, ["qwen2.5vl:7b"])
    g = recs.workloads["general"]
    assert g.model == "qwen2.5vl:7b"
    assert g.vision_capable is True
    assert g.capabilities  # derived, advisory-only
    assert not hasattr(g, "persisted")


# ── Writes go through the framework ───────────────────────────────────────


def test_apply_selection_writes_only_workloads(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, {"general": "llama3", "research": "gemma2",
                          "code": "qwen2.5-coder:7b"}, by="user")
    # no mode/meta/provenance keys written
    assert cfg.get("models.mode", "absent") == "absent"
    assert cfg.get("llm.meta.source", "absent") == "absent"
    raw = cfg.state.as_dict()
    assert "experience" not in raw
    assert "lightweight_mode" not in raw.get("runtime", {})


def test_apply_selection_reaches_runtime_apply_path(tmp_path):
    applied = []
    reg = build_registry()
    reg.require_owner("runtime", lambda p, v, prev: applied.append((p, v)))
    cfg = _make_cfg(tmp_path, reg=reg)
    apply_selection(cfg, {"general": "llama3", "research": "",
                          "code": ""}, by="user")
    assert ("llm.workloads.general.model", "llama3") in applied


# ── No automatic installation ─────────────────────────────────────────────


def test_selection_paths_never_install():
    import inspect
    from cozmo.configuration import resolver
    text = inspect.getsource(resolver)
    assert "pull" not in text
    assert "ModelInstaller" not in text
    assert "install_model" not in text
    assert "/api/pull" not in text