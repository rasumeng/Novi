"""Model recommendation + selection tests (Phase 1).

Verifies deterministic workload recommendations, trusted>supported preference,
never selecting incompatible, experimental last-resort, missing-trusted
fallback, hardware-confidence behaviour, derived vision capability, pure
advisory ``recommend()`` (never writes), and the verbatim persistent
``apply_selection()`` path (installed / not-installed / unset, no silent
substitution, no derived evidence leaked into config).
"""

import pytest

from cozmo.configuration.catalog import ModelFact
from cozmo.configuration.hardware import (
    DetectionConfidence,
    GpuConfidence,
    GpuInfo,
    HardwareProfile,
)
from cozmo.configuration.qualification import Qualification
from cozmo.configuration.resolver import (
    WORKLOADS,
    WORKLOAD_CAPABILITY,
    recommend,
    apply_selection,
)
from cozmo.configuration.bootstrap import build_registry, DEFAULT_CONFIG
from cozmo.configuration.manager import Configuration


def hw(gpu="", vram=None, gpu_conf=GpuConfidence.UNKNOWN, ram=None,
       conf=DetectionConfidence.UNKNOWN) -> HardwareProfile:
    return HardwareProfile(
        gpu=GpuInfo(vendor="nvidia" if gpu else "", name=gpu,
                    vram_total_gb=vram, confidence=gpu_conf),
        ram_gb=ram, confidence=conf,
    )


HW_HIGH = hw("RTX 4060", 8.0, GpuConfidence.KNOWN_VRAM, 32.0, DetectionConfidence.HIGH)
HW_MED = hw("GTX 1080", None, GpuConfidence.KNOWN_NO_VRAM, 16.0, DetectionConfidence.MEDIUM)
HW_LOW = hw("", None, GpuConfidence.UNKNOWN, 16.0, DetectionConfidence.LOW)
HW_UNKNOWN = hw("", None, GpuConfidence.UNKNOWN, None, DetectionConfidence.UNKNOWN)


def _facts(**kw):
    out = {}
    for _alias, spec in kw.items():
        name, qual, caps, *rest = spec
        extra = rest[0] if rest else {}
        out[name] = ModelFact(name=name, qualification=qual,
                              capabilities=caps, **extra)
    return out


CATALOG = _facts(
    qwen3_8b=("qwen3:8b", Qualification.TRUSTED,
              ["chat", "reasoning", "coding", "tools"]),
    qwen2_5vl=("qwen2.5vl:7b", Qualification.TRUSTED,
               ["chat", "vision", "tools"], {"supports_vision": True}),
    gemma4=("gemma4", Qualification.TRUSTED,
            ["chat", "reasoning", "tools"], {"min_vram_gb": 12.0,
             "caveats": ["sluggish on 8 GB VRAM"]}),
    llama31=("llama3.1:8b", Qualification.SUPPORTED,
             ["chat", "reasoning", "tools"]),
    qwen_coder_15=("qwen2.5-coder:1.5b", Qualification.SUPPORTED,
                   ["chat", "coding", "tools"]),
    experimental=("exp:model", Qualification.EXPERIMENTAL,
                  ["chat", "reasoning"]),
    incompatible=("bad:model", Qualification.INCOMPATIBLE, ["chat"]),
)

ALL_INSTALLED = list(CATALOG.keys())


# ── Deterministic recommendation with known hardware ──────────────────────


def test_deterministic_workload_recommendations():
    r1 = recommend(HW_HIGH, ALL_INSTALLED, CATALOG)
    r2 = recommend(HW_HIGH, ALL_INSTALLED, CATALOG)
    assert r1.to_dict() == r2.to_dict()  # deterministic
    assert set(r1.workloads.keys()) == set(WORKLOADS)


def test_each_workload_maps_to_its_capability():
    assert WORKLOAD_CAPABILITY == {
        "general": "chat", "research": "reasoning", "code": "coding",
    }


def test_code_workload_recommends_coding_model():
    r = recommend(HW_HIGH, ALL_INSTALLED, CATALOG)
    # code -> qwen3:8b: trusted, and it provides the coding capability.
    assert r.workloads["code"].model == "qwen3:8b"
    assert r.workloads["code"].capability == "coding"
    # only the supported coder available -> it wins for code
    r2 = recommend(HW_HIGH, ["qwen2.5-coder:1.5b"], CATALOG)
    assert r2.workloads["code"].model == "qwen2.5-coder:1.5b"


# ── Trusted preference / capability mapping ───────────────────────────────


def test_trusted_preferred_over_supported_for_general():
    installed = ["qwen3:8b", "llama3.1:8b"]
    r = recommend(HW_HIGH, installed, CATALOG)
    assert r.workloads["general"].model == "qwen3:8b"
    assert r.workloads["research"].model == "qwen3:8b"  # reasoning, trusted


def test_research_workload_prefers_reasoning_capability():
    r = recommend(HW_HIGH, ALL_INSTALLED, CATALOG)
    assert r.workloads["research"].model == "qwen3:8b"
    assert r.workloads["research"].capability == "reasoning"


def test_vision_is_derived_capability_flag_not_a_workload():
    r = recommend(HW_HIGH, ["qwen2.5vl:7b", "llama3.1:8b"], CATALOG)
    # general picks the broadest trusted chat model; vision rides along.
    assert set(r.workloads.keys()) == {"general", "research", "code"}
    assert r.workloads["general"].model == "qwen2.5vl:7b"
    assert r.workloads["general"].vision_capable is True
    # non-vision model reports the flag as False, never fabricates it
    r2 = recommend(HW_HIGH, ["llama3.1:8b"], CATALOG)
    assert r2.workloads["general"].vision_capable is False


def test_incompatible_never_selected():
    installed = ["bad:model", "qwen3:8b"]
    r = recommend(HW_HIGH, installed, CATALOG)
    for w, rec in r.workloads.items():
        assert rec.model != "bad:model", f"incompatible selected for {w}"


def test_incompatible_only_installed_yields_empty_recommendations():
    r = recommend(HW_HIGH, ["bad:model"], CATALOG)
    assert all(r.workloads[w].model == "" for w in WORKLOADS)


# ── Missing-trusted fallback ──────────────────────────────────────────────


def test_missing_trusted_falls_back_to_supported():
    installed = ["llama3.1:8b", "qwen2.5-coder:1.5b"]
    r = recommend(HW_HIGH, installed, CATALOG)
    assert r.workloads["general"].qualification == Qualification.SUPPORTED
    assert r.workloads["code"].model == "qwen2.5-coder:1.5b"


def test_experimental_last_resort_when_no_trusted_supported():
    r = recommend(HW_HIGH, ["exp:model"], CATALOG)
    assert r.workloads["general"].qualification == Qualification.EXPERIMENTAL
    assert r.workloads["general"].model == "exp:model"
    assert any("experimental" in c.lower()
               for c in r.workloads["general"].caveats)


# ── Hardware confidence behaviour ─────────────────────────────────────────


def test_vram_caveat_demotes_trusted_on_low_vram():
    # On an 8 GB system, gemma4 (min_vram_gb=12) must not win reasoning over
    # qwen3:8b (trusted, no VRAM mismatch) merely because it is trusted.
    r = recommend(HW_HIGH, ALL_INSTALLED, CATALOG)
    assert r.workloads["research"].model == "qwen3:8b"
    assert r.workloads["research"].qualification == Qualification.TRUSTED


def test_higher_vram_lifts_gemma_vram_demotion():
    hw_24 = hw("RTX 4090", 24.0, GpuConfidence.KNOWN_VRAM, 64.0,
               DetectionConfidence.HIGH)
    installed = ["gemma4", "llama3.1:8b"]  # trusted vs supported
    r = recommend(hw_24, installed, CATALOG)
    assert r.workloads["research"].model == "gemma4"

    # On 8 GB the same pair: gemma4 is demoted, so the supported model is used.
    r8 = recommend(HW_HIGH, installed, CATALOG)
    assert r8.workloads["research"].model == "llama3.1:8b"


def test_unknown_hardware_is_provisional_and_conservative():
    r = recommend(HW_UNKNOWN, ALL_INSTALLED, CATALOG)
    assert r.provisional is True
    assert r.workloads["general"].qualification == Qualification.TRUSTED
    assert r.hardware_confidence == DetectionConfidence.UNKNOWN


def test_low_hardware_is_provisional():
    assert recommend(HW_LOW, ALL_INSTALLED, CATALOG).provisional is True
    assert recommend(HW_HIGH, ALL_INSTALLED, CATALOG).provisional is False


# ── recommend() is pure advisory — never writes ───────────────────────────


def test_recommend_never_writes_config(tmp_path):
    reg = build_registry()
    cfg = Configuration(reg, tmp_path / "cozmo.toml", defaults=DEFAULT_CONFIG)
    cfg.initialize()
    before = cfg.snapshot()
    recommend(HW_HIGH, ALL_INSTALLED, CATALOG)
    after = cfg.snapshot()
    assert before == after
    assert cfg.get("llm.workloads.general.model") == ""


def test_recommend_never_installs_or_downloads():
    # recommend() has no side-channel to provider/model install.
    import inspect
    sig = inspect.signature(recommend)
    params = {name for name, p in sig.parameters.items()}
    assert params == {"hardware", "installed", "catalog"}


# ── apply_selection: verbatim persistent selection ────────────────────────


def _make_cfg(tmp_path, bus=None):
    reg = build_registry()
    cfg = Configuration(reg, tmp_path / "cozmo.toml", defaults=DEFAULT_CONFIG,
                        bus=bus)
    cfg.initialize()
    return cfg


def test_apply_selection_writes_workloads_verbatim(tmp_path):
    cfg = _make_cfg(tmp_path)
    out = apply_selection(cfg, {"general": "llama3", "research": "gemma2",
                                "code": "qwen2.5-coder:7b"},
                          installed=["llama3", "qwen2.5-coder:7b"])
    assert cfg.get("llm.workloads.general.model") == "llama3"
    assert cfg.get("llm.workloads.research.model") == "gemma2"
    assert cfg.get("llm.workloads.code.model") == "qwen2.5-coder:7b"
    assert out["workloads"]["general"]["status"] == "installed"
    assert out["workloads"]["research"]["status"] == "not-installed"
    assert out["workloads"]["code"]["status"] == "installed"


def test_apply_selection_never_substitutes_missing_model(tmp_path):
    # A model that is not installed is kept and reported, never replaced.
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, {"general": "gone:model"}, installed=["llama3"])
    assert cfg.get("llm.workloads.general.model") == "gone:model"


def test_apply_selection_empty_is_unset(tmp_path):
    cfg = _make_cfg(tmp_path)
    out = apply_selection(cfg, {"general": "", "research": "",
                                "code": "  "}, installed=["llama3"])
    assert cfg.get("llm.workloads.general.model") == ""
    assert cfg.get("llm.workloads.code.model") == ""
    assert out["workloads"]["general"]["status"] == "unset"
    assert out["workloads"]["code"]["status"] == "unset"


def test_apply_selection_emits_config_event(tmp_path):
    from cozmo.configuration.events import ConfigBus
    bus = ConfigBus()
    paths = []
    bus.on_any(lambda ev: paths.append(ev.path))
    cfg = _make_cfg(tmp_path, bus=bus)
    apply_selection(cfg, {"general": "llama3"}, by="user")
    assert "llm.workloads.general.model" in paths


def test_apply_selection_survives_reload(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, {"general": "llama3", "research": "gemma2",
                          "code": ""})
    cfg2 = Configuration(build_registry(), cfg.store.path,
                         defaults=DEFAULT_CONFIG)
    cfg2.initialize()
    assert cfg2.get("llm.workloads.general.model") == "llama3"
    assert cfg2.get("llm.workloads.research.model") == "gemma2"
    assert cfg2.get("llm.workloads.code.model") == ""


def test_apply_selection_does_not_persist_derived_evidence(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, {"general": "llama3", "research": "gemma2"})
    raw = cfg.state.as_dict()
    joined = {k.lower() for k in raw}
    assert not any("eligib" in k for k in joined)
    assert not any("hardwarefit" in k for k in joined)
    assert not any("caveat" in k for k in joined)
    assert not any("qualif" in k for k in joined)
    assert not any("visioncapable" in k for k in joined)