"""M2.4 — Automatic Resolution Layer tests.

Verifies deterministic role resolution, trusted>supported preference, never
selecting incompatible, experimental last-resort, missing-trusted fallback,
hardware-confidence behaviour, complete + non-empty role maps, separate
embeddings, and the config-integration path (llm.meta.source = automatic,
derived evidence NOT persisted).
"""

import pytest

from cozmo.configuration.catalog import ModelFact
from cozmo.configuration.discovery import ModelStatus
from cozmo.configuration.hardware import (
    DetectionConfidence,
    GpuConfidence,
    GpuInfo,
    HardwareProfile,
)
from cozmo.configuration.qualification import Qualification
from cozmo.configuration.resolver import (
    ALL_ROLES,
    resolve_automatic,
    apply_automatic,
)
from cozmo.configuration.bootstrap import build_registry
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
               ["chat", "vision", "tools"]),
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
    nomic=("nomic-embed-text", Qualification.SUPPORTED, ["embeddings"]),
)

ALL_INSTALLED = list(CATALOG.keys())


# ── Deterministic resolution with known hardware ──────────────────────────


def test_deterministic_full_role_map_with_known_hardware():
    r1 = resolve_automatic(HW_HIGH, ALL_INSTALLED, CATALOG)
    r2 = resolve_automatic(HW_HIGH, ALL_INSTALLED, CATALOG)
    assert r1.role_map == r2.role_map  # deterministic
    assert set(r1.role_map.keys()) == set(ALL_ROLES)


def test_complete_role_map_never_empty():
    r = resolve_automatic(HW_HIGH, ALL_INSTALLED, CATALOG)
    for role in ALL_ROLES:
        assert r.role_map[role], f"role {role} must never be empty"
    assert all(r.roles[role].model == r.role_map[role] for role in ALL_ROLES)


def test_internal_roles_never_empty_and_derived():
    r = resolve_automatic(HW_HIGH, ALL_INSTALLED, CATALOG)
    for role in ("classifier", "router", "orchestrator"):
        assert r.role_map[role]
        assert r.roles[role].source == "automatic"
        assert r.roles[role].capability == "reasoning/coding"


# ── Trusted preference / capability mapping ───────────────────────────────


def test_trusted_preferred_over_supported_for_role():
    # qwen3:8b (trusted) beats llama3.1:8b (supported) for chat even though
    # both provide chat; trusted is chosen.
    installed = ["qwen3:8b", "llama3.1:8b"]
    r = resolve_automatic(HW_HIGH, installed, CATALOG)
    assert r.role_map["chat"] == "qwen3:8b"
    assert r.role_map["planner"] == "qwen3:8b"  # reasoning, trusted


def test_vision_role_uses_vision_model():
    r = resolve_automatic(HW_HIGH, ALL_INSTALLED, CATALOG)
    assert r.role_map["vision"] == "qwen2.5vl:7b"


def test_incompatible_never_selected():
    installed = ["bad:model", "qwen3:8b"]
    r = resolve_automatic(HW_HIGH, installed, CATALOG)
    for role, model in r.role_map.items():
        assert model != "bad:model", f"incompatible selected for {role}"


def test_incompatible_only_installed_leaves_roles_empty():
    r = resolve_automatic(HW_HIGH, ["bad:model"], CATALOG)
    # incompatible is never selectable; roles end up empty (never fabricated).
    assert all(r.role_map[role] == "" for role in ALL_ROLES)


# ── Missing-trusted fallback ──────────────────────────────────────────────


def test_missing_trusted_falls_back_to_supported():
    # qwen3:8b not installed; only supported models present.
    installed = ["llama3.1:8b", "qwen2.5-coder:1.5b"]
    r = resolve_automatic(HW_HIGH, installed, CATALOG)
    assert r.roles["chat"].qualification == Qualification.SUPPORTED
    assert r.role_map["coder"] == "qwen2.5-coder:1.5b"
    assert any("trusted" not in reason for reason in r.roles["chat"].reasons)


def test_experimental_last_resort_when_no_trusted_supported():
    installed = ["exp:model"]
    r = resolve_automatic(HW_HIGH, installed, CATALOG)
    assert r.roles["chat"].qualification == Qualification.EXPERIMENTAL
    assert r.role_map["chat"] == "exp:model"
    assert any("experimental" in c.lower() for c in r.roles["chat"].caveats)


# ── Hardware confidence behaviour ─────────────────────────────────────────


def test_gpu_known_vram_known_respects_gemma_caveat():
    # On an 8 GB system, gemma4 (min_vram_gb=12) must not win reasoning over
    # qwen3:8b (trusted, no VRAM mismatch) merely because it is trusted.
    r = resolve_automatic(HW_HIGH, ALL_INSTALLED, CATALOG)
    assert r.roles["planner"].model == "qwen3:8b"
    assert r.roles["planner"].qualification == Qualification.TRUSTED


def test_higher_vram_lifts_gemma_vram_demotion():
    # On 24 GB, gemma4's min_vram_gb hint is met -> not demoted -> its trust
    # grade lets it win reasoning over a merely-supported alternative.
    hw_24 = hw("RTX 4090", 24.0, GpuConfidence.KNOWN_VRAM, 64.0,
               DetectionConfidence.HIGH)
    installed = ["gemma4", "llama3.1:8b"]  # trusted vs supported
    r = resolve_automatic(hw_24, installed, CATALOG)
    assert r.roles["planner"].model == "gemma4"

    # On 8 GB the same pair: gemma4 is demoted, so the supported model is used.
    r8 = resolve_automatic(HW_HIGH, installed, CATALOG)
    assert r8.roles["planner"].model == "llama3.1:8b"


def test_gpu_unknown_vram_unknown_is_conservative():
    # Medium software confidence; no VRAM used for filtering -> trusted chosen,
    # never a fabricated VRAM claim.
    r = resolve_automatic(HW_MED, ALL_INSTALLED, CATALOG)
    assert r.roles["chat"].qualification == Qualification.TRUSTED
    assert r.hardware_confidence == DetectionConfidence.MEDIUM


def test_unknown_hardware_is_provisional_and_conservative():
    r = resolve_automatic(HW_UNKNOWN, ALL_INSTALLED, CATALOG)
    assert r.provisional is True
    assert r.roles["chat"].qualification == Qualification.TRUSTED
    assert r.hardware_confidence == DetectionConfidence.UNKNOWN


def test_low_hardware_is_provisional():
    assert resolve_automatic(HW_LOW, ALL_INSTALLED, CATALOG).provisional is True
    assert resolve_automatic(HW_HIGH, ALL_INSTALLED, CATALOG).provisional is False


# ── Embeddings separate ───────────────────────────────────────────────────


def test_embeddings_resolve_separately():
    r = resolve_automatic(HW_HIGH, ALL_INSTALLED, CATALOG)
    assert r.embedding_model == "nomic-embed-text"
    assert r.roles["chat"].model != r.embedding_model


# ── Registered settings: models.mode + llm.meta.source ────────────────────

REAL_INSTALLED = [
    "qwen3:8b", "qwen2.5vl:7b", "gemma4", "llama3.1:8b",
    "qwen2.5-coder:7b", "nomic-embed-text",
]


def _make_cfg(tmp_path, bus=None):
    reg = build_registry()
    cfg = Configuration(reg, tmp_path / "cozmo.toml", bus=bus)
    cfg.initialize()
    return cfg


def test_mode_and_source_settings_registered(tmp_path):
    cfg = _make_cfg(tmp_path)
    assert cfg.has_config("models.mode")
    assert cfg.has_config("llm.meta.source")
    setting = cfg.registry.get("models.mode")
    assert setting.type.value == "enum"
    assert setting.visibility.value == "hidden"
    meta = cfg.registry.get("llm.meta.source")
    assert meta.type.value == "enum"
    assert meta.visibility.value == "hidden"


def test_mode_and_source_validate_allowed_enum_values(tmp_path):
    cfg = _make_cfg(tmp_path)
    for setting_id in ("models.mode", "llm.meta.source"):
        cfg.set(setting_id, "automatic")
        cfg.set(setting_id, "custom")
        with pytest.raises(Exception):
            cfg.set(setting_id, "bogus")
        with pytest.raises(Exception):
            cfg.set(setting_id, 123)


def test_mode_and_source_persist_through_framework(tmp_path):
    cfg = _make_cfg(tmp_path)
    cfg.set("models.mode", "custom")
    cfg.set("llm.meta.source", "custom")
    assert cfg.get("models.mode") == "custom"
    assert cfg.get("llm.meta.source") == "custom"


def test_mode_and_source_default_automatic(tmp_path):
    cfg = _make_cfg(tmp_path)
    assert cfg.get("models.mode", default="automatic") == "automatic"
    assert cfg.get("llm.meta.source", default="automatic") == "automatic"


# ── Config integration ────────────────────────────────────────────────────


def test_apply_automatic_persists_roles_and_source(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_automatic(cfg, installed=REAL_INSTALLED, hardware=HW_HIGH)
    for role in ALL_ROLES:
        assert cfg.get(f"llm.roles.{role}.model"), f"{role} must be persisted"
    assert cfg.get("llm.meta.source") == "automatic"
    assert cfg.get("models.mode") == "automatic"
    assert cfg.get("embedding.model") == "nomic-embed-text"


def test_apply_emits_config_event(tmp_path):
    from cozmo.configuration.events import ConfigBus
    bus = ConfigBus()
    paths = []
    bus.on_any(lambda ev: paths.append(ev.path))
    cfg = _make_cfg(tmp_path, bus=bus)
    apply_automatic(cfg, installed=REAL_INSTALLED, hardware=HW_HIGH)
    assert "llm.roles.chat.model" in paths
    assert "llm.meta.source" in paths
    assert "models.mode" in paths


def test_apply_survives_config_reload(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_automatic(cfg, installed=REAL_INSTALLED, hardware=HW_HIGH)
    path = cfg.store.path
    cfg2 = Configuration(build_registry(), path)
    cfg2.initialize()
    for role in ALL_ROLES:
        assert cfg2.get(f"llm.roles.{role}.model")
    assert cfg2.get("llm.meta.source") == "automatic"
    assert cfg2.get("models.mode") == "automatic"


def test_apply_does_not_persist_derived_eligibility(tmp_path):
    # role map + mode + provenance persist, but derived evidence NEVER leaks.
    cfg = _make_cfg(tmp_path)
    apply_automatic(cfg, installed=REAL_INSTALLED, hardware=HW_HIGH)
    raw = cfg.state.as_dict()
    joined = {k.lower() for k in raw}
    assert not any("eligib" in k for k in joined)
    assert not any("hardwarefit" in k for k in joined)
    assert not any("caveat" in k for k in joined)
    assert not any("qualif" in k for k in joined)

# -- M3.2 � Custom capability assignment state machine ---------------------


def _set_custom_assign(cfg, **kw):
    for cap, model in kw.items():
        cfg.set(f"models.custom.assign.{cap}", model or "")


def test_custom_maps_capabilities_to_roles(tmp_path):
    from cozmo.configuration.resolver import resolve_custom
    cfg = _make_cfg(tmp_path)
    installed = REAL_INSTALLED + ["qwen2.5-coder:1.5b"]
    _set_custom_assign(cfg, chat="qwen3:8b", reasoning="llama3.1:8b",
                       coding="qwen2.5-coder:1.5b", vision="qwen2.5vl:7b")
    r = resolve_custom(cfg, hardware=HW_HIGH, installed=installed, catalog=CATALOG)
    assert r.role_map["chat"] == "qwen3:8b"
    assert r.role_map["planner"] == "llama3.1:8b"
    assert r.role_map["coder"] == "qwen2.5-coder:1.5b"
    assert r.role_map["vision"] == "qwen2.5vl:7b"
    assert r.mode == "custom"
    for role in ALL_ROLES:
        assert r.role_map[role], f"{role} must never be empty"


def test_custom_internal_roles_derive_and_stay_sourced_custom(tmp_path):
    from cozmo.configuration.resolver import resolve_custom
    cfg = _make_cfg(tmp_path)
    _set_custom_assign(cfg, coding="qwen2.5-coder:1.5b", chat="qwen3:8b")
    r = resolve_custom(cfg, hardware=HW_HIGH, installed=REAL_INSTALLED, catalog=CATALOG)
    # internal roles derive from reasoning -> coding -> chat, never empty
    for role in ("classifier", "router", "orchestrator"):
        assert r.role_map[role]
        assert r.roles[role].capability == "reasoning/coding"


def test_custom_unset_capability_falls_back_and_is_not_written(tmp_path):
    from cozmo.configuration.resolver import resolve_custom
    cfg = _make_cfg(tmp_path)
    _set_custom_assign(cfg, chat="qwen3:8b", coding="qwen2.5-coder:1.5b")
    r = resolve_custom(cfg, hardware=HW_HIGH, installed=REAL_INSTALLED, catalog=CATALOG)
    # reasoning (planner) unset -> baseline automatic, not written back as intent
    assert r.roles["planner"].source == "automatic"
    assert cfg.get("models.custom.assign.reasoning", "") in ("", None)
    assert r.role_map["planner"], "planner never empty"


def test_apply_custom_persists_roles_mode_and_source(tmp_path):
    from cozmo.configuration.resolver import apply_custom
    cfg = _make_cfg(tmp_path)
    _set_custom_assign(cfg, chat="qwen3:8b", reasoning="llama3.1:8b",
                       coding="qwen2.5-coder:1.5b", vision="qwen2.5vl:7b")
    apply_custom(cfg, installed=REAL_INSTALLED, hardware=HW_HIGH)
    for role in ALL_ROLES:
        assert cfg.get(f"llm.roles.{role}.model"), f"{role} persisted"
    assert cfg.get("models.mode") == "custom"
    assert cfg.get("llm.meta.source") == "custom"
    assert cfg.get("llm.roles.chat.model") == "qwen3:8b"
    assert cfg.get("llm.roles.planner.model") == "llama3.1:8b"


def test_custom_change_updates_only_that_role(tmp_path):
    from cozmo.configuration.resolver import apply_custom
    cfg = _make_cfg(tmp_path)
    installed = REAL_INSTALLED + ["qwen2.5-coder:1.5b"]
    _set_custom_assign(cfg, chat="qwen3:8b", coding="qwen2.5-coder:7b")
    apply_custom(cfg, installed=installed, hardware=HW_HIGH)
    # change only coding
    _set_custom_assign(cfg, coding="qwen2.5-coder:1.5b")
    apply_custom(cfg, installed=installed, hardware=HW_HIGH)
    assert cfg.get("llm.roles.coder.model") == "qwen2.5-coder:1.5b"
    assert cfg.get("llm.roles.chat.model") == "qwen3:8b"  # untouched


def test_custom_assign_survives_reload(tmp_path):
    from cozmo.configuration.resolver import apply_custom
    cfg = _make_cfg(tmp_path)
    _set_custom_assign(cfg, chat="qwen3:8b", coding="qwen2.5-coder:1.5b")
    apply_custom(cfg, installed=REAL_INSTALLED, hardware=HW_HIGH)
    path = cfg.store.path
    cfg2 = Configuration(build_registry(), path)
    cfg2.initialize()
    assert cfg2.get("models.mode") == "custom"
    assert cfg2.get("llm.meta.source") == "custom"
    assert cfg2.get("models.custom.assign.chat") == "qwen3:8b"
    assert cfg2.get("models.custom.assign.coding") == "qwen2.5-coder:1.5b"
    assert cfg2.get("llm.roles.chat.model") == "qwen3:8b"


def test_return_to_automatic_preserves_custom_intent(tmp_path):
    from cozmo.configuration.resolver import apply_custom, apply_automatic
    cfg = _make_cfg(tmp_path)
    _set_custom_assign(cfg, chat="qwen3:8b", coding="qwen2.5-coder:1.5b")
    apply_custom(cfg, installed=REAL_INSTALLED, hardware=HW_HIGH)
    apply_automatic(cfg, installed=REAL_INSTALLED, hardware=HW_HIGH)
    assert cfg.get("models.mode") == "automatic"
    assert cfg.get("llm.meta.source") == "automatic"
    # user's custom intent is preserved, not deleted
    assert cfg.get("models.custom.assign.chat") == "qwen3:8b"
    assert cfg.get("models.custom.assign.coding") == "qwen2.5-coder:1.5b"


def test_missing_custom_model_preserves_intent_and_falls_back(tmp_path):
    from cozmo.configuration.resolver import apply_custom
    cfg = _make_cfg(tmp_path)
    _set_custom_assign(cfg, chat="gone:model", coding="qwen3:8b")
    apply_custom(cfg, installed=REAL_INSTALLED, hardware=HW_HIGH)
    # explicit intent survived
    assert cfg.get("models.custom.assign.chat") == "gone:model"
    assert cfg.get("models.mode") == "custom"
    # runtime role temporarily fell back to a safe installed model (baseline)
    assert cfg.get("llm.roles.chat.model"), "chat must not be empty"
    # a valid custom model is still respected verbatim
    assert cfg.get("llm.roles.coder.model") == "qwen3:8b"


def test_custom_does_not_write_embeddings(tmp_path):
    from cozmo.configuration.resolver import apply_custom
    cfg = _make_cfg(tmp_path)
    _set_custom_assign(cfg, chat="qwen3:8b")
    apply_custom(cfg, installed=REAL_INSTALLED, hardware=HW_HIGH)
    assign = cfg.get("models.custom.assign", {}) or {}
    assert "embedding" not in assign
    assert "embeddings" not in assign
    assert set(assign.keys()) <= {"chat", "reasoning", "coding", "vision"}


def test_custom_does_not_persist_derived_evidence(tmp_path):
    from cozmo.configuration.resolver import apply_custom
    cfg = _make_cfg(tmp_path)
    _set_custom_assign(cfg, chat="qwen3:8b")
    apply_custom(cfg, installed=REAL_INSTALLED, hardware=HW_HIGH)
    raw = cfg.state.as_dict()
    joined = {k.lower() for k in raw}
    assert not any("caveat" in k for k in joined)
    assert not any("hardwarefit" in k for k in joined)

def test_full_custom_flow_reload_survives(tmp_path):
    """End-to-end M3.2 flow on the real framework: enter Custom (seeded from
    Automatic), change one capability, leave another unset, return to Automatic,
    then re-enter Custom without losing earlier explicit choices."""
    from cozmo.configuration.resolver import apply_automatic, apply_custom
    installed = REAL_INSTALLED + ["qwen2.5-coder:1.5b"]
    hardware = HW_HIGH

    cfg = _make_cfg(tmp_path)
    # automatic baseline -> effective roles
    apply_automatic(cfg, installed=installed, hardware=hardware)
    auto_chat = cfg.get("llm.roles.chat.model")

    # enter Custom seeded from the effective assignments
    _set_custom_assign(cfg, chat=auto_chat, coding=cfg.get("llm.roles.coder.model"))
    apply_custom(cfg, installed=installed, hardware=hardware)
    assert cfg.get("models.mode") == "custom"
    assert cfg.get("llm.meta.source") == "custom"

    # change only coding; reasoning stays unset (inherits Auto fallback)
    _set_custom_assign(cfg, coding="qwen2.5-coder:1.5b")
    apply_custom(cfg, installed=installed, hardware=hardware)
    assert cfg.get("llm.roles.coder.model") == "qwen2.5-coder:1.5b"
    assert cfg.get("llm.roles.chat.model") == auto_chat
    assert cfg.get("models.custom.assign.reasoning", "") in ("", None)

    # return to Automatic: resolver reruns, custom intent preserved
    apply_automatic(cfg, installed=installed, hardware=hardware)
    assert cfg.get("models.mode") == "automatic"
    assert cfg.get("llm.meta.source") == "automatic"
    assert cfg.get("models.custom.assign.coding") == "qwen2.5-coder:1.5b"

    # re-enter Custom: prior explicit choice still present, survives full reload
    cfg2 = Configuration(build_registry(), cfg.store.path)
    cfg2.initialize()
    assert cfg2.get("models.custom.assign.coding") == "qwen2.5-coder:1.5b"
    assert cfg2.get("models.custom.assign.chat") == auto_chat
