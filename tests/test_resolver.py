"""Model recommendation + selection tests (single primary model).

Verifies deterministic primary recommendation, trusted>supported preference,
never selecting incompatible, experimental last-resort, missing-trusted
fallback, hardware-confidence behaviour, derived vision capability, pure
advisory ``recommend()`` (never writes), and the verbatim persistent
``apply_selection()`` path (installed / not-installed / unset, no silent
substitution, no derived evidence leaked into config).
"""

import pytest

from novi.configuration.model_seeds import ModelFact
from novi.configuration.hardware import (
    DetectionConfidence,
    GpuConfidence,
    GpuInfo,
    HardwareProfile,
)
from novi.configuration.qualification import Qualification
from novi.configuration.resolver import (
    PRIMARY_MODEL_KEY,
    PrimaryRecommendation,
    Recommendations,
    get_primary_model,
    recommend,
    apply_selection,
)
from novi.configuration.bootstrap import build_registry, DEFAULT_CONFIG
from novi.configuration.manager import Configuration


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


def test_deterministic_primary_recommendation():
    r1 = recommend(HW_HIGH, ALL_INSTALLED, CATALOG)
    r2 = recommend(HW_HIGH, ALL_INSTALLED, CATALOG)
    assert r1.to_dict() == r2.to_dict()  # deterministic
    assert isinstance(r1, Recommendations)
    assert isinstance(r1.primary, PrimaryRecommendation)
    assert r1.primary.model


def test_primary_key_constant():
    assert PRIMARY_MODEL_KEY == "llm.primary_model"


def test_broadest_trusted_model_wins_primary():
    r = recommend(HW_HIGH, ALL_INSTALLED, CATALOG)
    # qwen3:8b covers chat+reasoning+coding+tools (broadest trusted).
    assert r.primary.model == "qwen3:8b"
    assert set(r.primary.capabilities) >= {"chat", "reasoning", "coding"}
    # only the supported coder available -> it wins for primary
    r2 = recommend(HW_HIGH, ["qwen2.5-coder:1.5b"], CATALOG)
    assert r2.primary.model == "qwen2.5-coder:1.5b"


# ── Trusted preference / capability mapping ───────────────────────────────


def test_trusted_preferred_over_supported_for_primary():
    installed = ["qwen3:8b", "llama3.1:8b"]
    r = recommend(HW_HIGH, installed, CATALOG)
    assert r.primary.model == "qwen3:8b"
    assert r.primary.qualification == Qualification.TRUSTED


def test_primary_covers_reasoning_capability():
    r = recommend(HW_HIGH, ALL_INSTALLED, CATALOG)
    assert r.primary.model == "qwen3:8b"
    assert "reasoning" in r.primary.capabilities


def test_vision_is_derived_capability_flag_not_a_slot():
    r = recommend(HW_HIGH, ["qwen2.5vl:7b"], CATALOG)
    assert r.primary.model == "qwen2.5vl:7b"
    assert r.primary.vision_capable is True
    # non-vision model reports the flag as False, never fabricates it
    r2 = recommend(HW_HIGH, ["llama3.1:8b"], CATALOG)
    assert r2.primary.vision_capable is False


def test_incompatible_never_selected():
    installed = ["bad:model", "qwen3:8b"]
    r = recommend(HW_HIGH, installed, CATALOG)
    assert r.primary.model != "bad:model"
    assert r.primary.model == "qwen3:8b"


def test_incompatible_only_installed_yields_empty_primary():
    r = recommend(HW_HIGH, ["bad:model"], CATALOG)
    assert r.primary.model == ""


# ── Missing-trusted fallback ──────────────────────────────────────────────


def test_missing_trusted_falls_back_to_supported():
    installed = ["llama3.1:8b", "qwen2.5-coder:1.5b"]
    r = recommend(HW_HIGH, installed, CATALOG)
    assert r.primary.qualification == Qualification.SUPPORTED
    assert r.primary.model in set(installed)


def test_experimental_last_resort_when_no_trusted_supported():
    r = recommend(HW_HIGH, ["exp:model"], CATALOG)
    assert r.primary.qualification == Qualification.EXPERIMENTAL
    assert r.primary.model == "exp:model"
    assert any("experimental" in c.lower() for c in r.primary.caveats)


# ── Hardware confidence behaviour ─────────────────────────────────────────


def test_vram_caveat_demotes_trusted_on_low_vram():
    # On an 8 GB system, gemma4 (min_vram_gb=12) must not win over
    # qwen3:8b (trusted, no VRAM mismatch).
    r = recommend(HW_HIGH, ALL_INSTALLED, CATALOG)
    assert r.primary.model == "qwen3:8b"
    assert r.primary.qualification == Qualification.TRUSTED


def test_higher_vram_lifts_gemma_vram_demotion():
    hw_24 = hw("RTX 4090", 24.0, GpuConfidence.KNOWN_VRAM, 64.0,
               DetectionConfidence.HIGH)
    installed = ["gemma4", "llama3.1:8b"]  # trusted vs supported
    r = recommend(hw_24, installed, CATALOG)
    assert r.primary.model == "gemma4"

    # On 8 GB the same pair: gemma4 is demoted, so the supported model is used.
    r8 = recommend(HW_HIGH, installed, CATALOG)
    assert r8.primary.model == "llama3.1:8b"


def test_unknown_hardware_is_provisional_and_conservative():
    r = recommend(HW_UNKNOWN, ALL_INSTALLED, CATALOG)
    assert r.provisional is True
    assert r.primary.qualification == Qualification.TRUSTED
    assert r.hardware_confidence == DetectionConfidence.UNKNOWN


def test_low_hardware_is_provisional():
    assert recommend(HW_LOW, ALL_INSTALLED, CATALOG).provisional is True
    assert recommend(HW_HIGH, ALL_INSTALLED, CATALOG).provisional is False


def test_get_primary_model_helpers():
    assert get_primary_model(config={"llm": {"primary_model": "  qwen3:8b "}}) == "qwen3:8b"
    assert get_primary_model(config={}) == ""


# ── recommend() is pure advisory — never writes ───────────────────────────


def test_recommend_never_writes_config(tmp_path):
    reg = build_registry()
    cfg = Configuration(reg, tmp_path / "novi.toml", defaults=DEFAULT_CONFIG)
    cfg.initialize()
    before = cfg.snapshot()
    recommend(HW_HIGH, ALL_INSTALLED, CATALOG)
    after = cfg.snapshot()
    assert before == after
    assert cfg.get("llm.primary_model") == ""


def test_recommend_never_installs_or_downloads():
    # recommend() has no side-channel to provider/model install.
    import inspect
    sig = inspect.signature(recommend)
    params = {name for name, p in sig.parameters.items()}
    assert params == {"hardware", "installed", "catalog"}


# ── apply_selection: verbatim persistent selection ────────────────────────


def _make_cfg(tmp_path, bus=None):
    reg = build_registry()
    cfg = Configuration(reg, tmp_path / "novi.toml", defaults=DEFAULT_CONFIG,
                        bus=bus)
    cfg.initialize()
    return cfg


def test_apply_selection_writes_primary_verbatim(tmp_path):
    cfg = _make_cfg(tmp_path)
    out = apply_selection(cfg, model="llama3", installed=["llama3", "qwen2.5-coder:7b"])
    assert cfg.get("llm.primary_model") == "llama3"
    assert get_primary_model(configuration=cfg) == "llama3"
    assert out["model"] == "llama3"
    assert out["status"] == "installed"
    assert out["primary"]["model"] == "llama3"


def test_apply_selection_reports_not_installed(tmp_path):
    cfg = _make_cfg(tmp_path)
    out = apply_selection(cfg, model="gemma2", installed=["llama3"])
    assert cfg.get("llm.primary_model") == "gemma2"
    assert out["status"] == "not-installed"


def test_apply_selection_never_substitutes_missing_model(tmp_path):
    # A model that is not installed is kept and reported, never replaced.
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, model="gone:model", installed=["llama3"])
    assert cfg.get("llm.primary_model") == "gone:model"


def test_apply_selection_empty_is_unset(tmp_path):
    cfg = _make_cfg(tmp_path)
    out = apply_selection(cfg, model="  ", installed=["llama3"])
    assert cfg.get("llm.primary_model") == ""
    assert out["status"] == "unset"


def test_apply_selection_model_kwarg(tmp_path):
    cfg = _make_cfg(tmp_path)
    out = apply_selection(cfg, model="llama3", installed=["llama3"])
    assert cfg.get("llm.primary_model") == "llama3"
    assert out["model"] == "llama3"


def test_apply_selection_emits_config_event(tmp_path):
    from novi.configuration.events import ConfigBus
    bus = ConfigBus()
    paths = []
    bus.on_any(lambda ev: paths.append(ev.path))
    cfg = _make_cfg(tmp_path, bus=bus)
    apply_selection(cfg, model="llama3")
    assert "llm.primary_model" in paths


def test_apply_selection_survives_reload(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, model="llama3")
    cfg2 = Configuration(build_registry(), cfg.store.path,
                         defaults=DEFAULT_CONFIG)
    cfg2.initialize()
    assert cfg2.get("llm.primary_model") == "llama3"


def test_apply_selection_does_not_persist_derived_evidence(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_selection(cfg, model="llama3")
    raw = cfg.state.as_dict()
    joined = {k.lower() for k in raw}
    assert not any("eligib" in k for k in joined)
    assert not any("hardwarefit" in k for k in joined)
    assert not any("caveat" in k for k in joined)
    assert not any("qualif" in k for k in joined)
    assert not any("visioncapable" in k for k in joined)
