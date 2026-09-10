"""Model selection integration tests (single primary model).

Covers the boundary between pure advisory recommendation and persistent,
authoritative selection:

* Selection is user intent persisted verbatim to ``llm.primary_model`` and is
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

from novi.configuration.bootstrap import build_registry, DEFAULT_CONFIG
from novi.configuration.hardware import (
    DetectionConfidence,
    GpuConfidence,
    GpuInfo,
    HardwareProfile,
)
from novi.configuration.manager import Configuration
from novi.configuration.resolver import (
    PRIMARY_MODEL_KEY,
    get_primary_model,
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
    cfg = Configuration(reg, tmp_path / "novi.toml", defaults=DEFAULT_CONFIG,
                        bus=bus)
    cfg.initialize()
    return cfg


def _selection(cfg):
    return cfg.get(PRIMARY_MODEL_KEY, "")


# ── Selection is persistent and authoritative ─────────────────────────────


def test_selection_persists_across_reload(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, model="llama3")
    cfg2 = _make_cfg(tmp_path)
    # fresh instance over same file path? _make_cfg uses tmp_path/novi.toml
    # so reload from same store path:
    cfg2 = Configuration(build_registry(), cfg.store.path, defaults=DEFAULT_CONFIG)
    cfg2.initialize()
    assert cfg2.get(PRIMARY_MODEL_KEY) == "llama3"


def test_install_never_rewrites_selection(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, model="llama3")
    before = _selection(cfg)

    # a trusted model appears in the installed set -> advisory recommendation
    # changes, but the persisted selection is untouched.
    recs = recommend(HW_HIGH, ["llama3", "gemma2", "qwen3:8b"])
    assert recs.primary.model == "qwen3:8b"
    assert _selection(cfg) == before
    assert cfg.get("llm.primary_model") == "llama3"


def test_removal_never_rewrites_selection(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, model="gone:model")
    before = _selection(cfg)

    # model removal changes recommendations (advisory), selection stays.
    recs = recommend(HW_HIGH, [])
    assert recs.primary.model == ""
    assert _selection(cfg) == before
    assert cfg.get("llm.primary_model") == "gone:model"


def test_hardware_change_never_rewrites_selection(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, model="gemma4")
    before = _selection(cfg)
    installed = ["gemma4", "llama3.1:8b"]
    recs_small = recommend(HW_HIGH, installed)      # gemma4 demoted
    recs_big = recommend(HW_BIG, installed)         # not demoted
    assert recs_small.primary.model == "llama3.1:8b"
    assert recs_big.primary.model == "gemma4"
    assert _selection(cfg) == before


# ── No silent substitution ────────────────────────────────────────────────


def test_not_installed_selection_is_kept_and_reported(tmp_path):
    cfg = _make_cfg(tmp_path)
    out = apply_selection(cfg, model="missing:model", installed=["llama3"])
    assert cfg.get("llm.primary_model") == "missing:model"
    assert out["status"] == "not-installed"
    assert out["model"] == "missing:model"
    # no automatic fallback substituted into config
    assert cfg.get("llm.primary_model") != "llama3"


def test_empty_discovery_never_fabricates_selection(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, model="")
    recs = recommend(HW_HIGH, [])
    assert recs.primary.model == ""
    assert cfg.get("llm.primary_model") == ""


# ── Advisory recommendations track the installed set ──────────────────────


def test_recommendations_reflect_installed_set_change(tmp_path):
    cfg = _make_cfg(tmp_path)
    r1 = recommend(HW_HIGH, ["llama3.1:8b"])
    r2 = recommend(HW_HIGH, ["llama3.1:8b", "qwen3:8b"])
    assert r1.primary.model == "llama3.1:8b"
    assert r2.primary.model == "qwen3:8b"
    # config unchanged by either computation
    assert _selection(cfg) == ""


def test_recommendation_contains_derived_evidence_only():
    recs = recommend(HW_HIGH, ["qwen2.5vl:7b"])
    g = recs.primary
    assert g.model == "qwen2.5vl:7b"
    assert g.vision_capable is True
    assert g.capabilities  # derived, advisory-only
    assert not hasattr(g, "persisted")


# ── Writes go through the framework ───────────────────────────────────────


def test_apply_selection_writes_only_primary(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, model="llama3", by="user")
    # no mode/meta/provenance keys written
    assert cfg.get("models.mode", "absent") == "absent"
    assert cfg.get("llm.meta.source", "absent") == "absent"
    assert cfg.get("llm.workloads", "absent") == "absent"
    raw = cfg.state.as_dict()
    assert "experience" not in raw
    assert "lightweight_mode" not in raw.get("runtime", {})


def test_apply_selection_reaches_runtime_apply_path(tmp_path):
    applied = []
    reg = build_registry()
    reg.require_owner("runtime", lambda p, v, prev: applied.append((p, v)))
    cfg = _make_cfg(tmp_path, reg=reg)
    apply_selection(cfg, model="llama3", by="user")
    assert ("llm.primary_model", "llama3") in applied


def test_get_primary_model_reads_selection(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, model="qwen3:8b")
    assert get_primary_model(configuration=cfg) == "qwen3:8b"


# ── No automatic installation ─────────────────────────────────────────────


def test_selection_paths_never_install():
    import inspect
    from novi.configuration import resolver
    text = inspect.getsource(resolver)
    assert "pull" not in text
    assert "ModelInstaller" not in text
    assert "install_model" not in text
    assert "/api/pull" not in text
