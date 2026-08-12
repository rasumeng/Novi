"""M3.3 — Automatic Resolver Integration & Recompute Triggers.

Covers ``recompute_automatic_if_active`` — the seam that connects the M2.4
resolver to real model-set / hardware lifecycle events:

* Automatic mode is authoritative: external state changes re-resolve and
  ``llm.roles.*`` reflects the CURRENT resolution (never stale user intent).
* Custom mode is isolated: installs/removals/hardware refreshes are strict
  NOOPs — ``models.custom.assign.*``, ``models.mode``, ``llm.meta.source``
  and the resolved Custom roles are never rewritten.
* Idempotency: a recomputation that yields the same resolved state writes
  nothing and emits no configuration events (no loop).
* Empty-evidence guard: an inconclusive/empty discovery never wipes a working
  role map.
* Runtime application: changed roles reach the existing runtime apply-hook
  path; unchanged roles do not re-trigger it.
* Provenance: Automatic recomputation preserves
  ``models.mode = llm.meta.source = "automatic"``.
* No auto-installation ever happens here.

Hermetic: in-memory / tmp_path config; no network, no Ollama, no services.
"""

import pytest

from cozmo.configuration.bootstrap import build_registry
from cozmo.configuration.hardware import (
    DetectionConfidence,
    GpuConfidence,
    GpuInfo,
    HardwareProfile,
)
from cozmo.configuration.manager import Configuration
from cozmo.configuration.resolver import (
    ALL_ROLES,
    apply_automatic,
    apply_custom,
    recompute_automatic_if_active,
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


def _make_cfg(tmp_path, bus=None):
    reg = build_registry()
    cfg = Configuration(reg, tmp_path / "cozmo.toml", bus=bus)
    cfg.initialize()
    return cfg


def _roles(cfg):
    return {r: cfg.get(f"llm.roles.{r}.model", "") for r in ALL_ROLES}


def _set_custom_assign(cfg, **kw):
    for cap, model in kw.items():
        cfg.set(f"models.custom.assign.{cap}", model or "")


# ── Automatic + model installation ────────────────────────────────────────


def test_automatic_recompute_on_install_reresolves_roles(tmp_path):
    cfg = _make_cfg(tmp_path)
    installed = ["llama3.1:8b", "qwen2.5-coder:7b", "nomic-embed-text"]
    apply_automatic(cfg, installed=installed, hardware=HW_HIGH)
    # pre-install: only supported models; no trusted chat and no vision model.
    assert cfg.get("llm.roles.chat.model") == "llama3.1:8b"
    assert cfg.get("llm.roles.coder.model") == "qwen2.5-coder:7b"
    assert cfg.get("llm.roles.vision.model", "") == ""

    # trusted models become installed -> recompute adopts them everywhere.
    resolution, changed = recompute_automatic_if_active(
        cfg, installed=installed + ["qwen3:8b", "qwen2.5vl:7b"], hardware=HW_HIGH)
    assert changed is True
    assert resolution is not None
    assert cfg.get("llm.roles.chat.model") == "qwen3:8b"
    assert cfg.get("llm.roles.coder.model") == "qwen3:8b"
    assert cfg.get("llm.roles.planner.model") == "qwen3:8b"
    assert cfg.get("llm.roles.vision.model") == "qwen2.5vl:7b"


# ── Automatic + model removal ─────────────────────────────────────────────


def test_automatic_recompute_on_removal_uses_fallback(tmp_path):
    cfg = _make_cfg(tmp_path)
    before = ["qwen3:8b", "qwen2.5vl:7b", "qwen2.5-coder:7b",
              "llama3.1:8b", "nomic-embed-text"]
    apply_automatic(cfg, installed=before, hardware=HW_HIGH)
    assert cfg.get("llm.roles.chat.model") == "qwen3:8b"
    assert cfg.get("llm.roles.coder.model") == "qwen3:8b"

    after = ["qwen2.5-coder:7b", "llama3.1:8b", "nomic-embed-text"]
    resolution, changed = recompute_automatic_if_active(
        cfg, installed=after, hardware=HW_HIGH)
    assert changed is True
    # supported fallbacks fill the removed trusted roles; nothing fabricated.
    assert cfg.get("llm.roles.chat.model") == "llama3.1:8b"
    assert cfg.get("llm.roles.coder.model") == "qwen2.5-coder:7b"
    assert cfg.get("llm.meta.source") == "automatic"
    assert cfg.get("models.mode") == "automatic"


def test_automatic_empty_discovery_does_not_wipe_working_roles(tmp_path):
    cfg = _make_cfg(tmp_path)
    apply_automatic(cfg, installed=["qwen3:8b", "nomic-embed-text"],
                    hardware=HW_HIGH)
    before = _roles(cfg)
    resolution, changed = recompute_automatic_if_active(
        cfg, installed=[], hardware=HW_HIGH)
    # inconclusive (provider unavailable / cold start) -> never wiped.
    assert changed is False
    assert resolution is not None
    assert _roles(cfg) == before


# ── Custom isolation ──────────────────────────────────────────────────────


def test_custom_model_install_is_noop(tmp_path):
    cfg = _make_cfg(tmp_path)
    _set_custom_assign(cfg, chat="llama3.1:8b", coding="qwen2.5-coder:7b")
    apply_custom(cfg, installed=["llama3.1:8b", "qwen2.5-coder:7b",
                                 "nomic-embed-text"], hardware=HW_HIGH)
    assert cfg.get("models.mode") == "custom"
    before = _roles(cfg)

    # a new trusted model appears -> Custom intent is NOT overwritten.
    resolution, changed = recompute_automatic_if_active(
        cfg, installed=["llama3.1:8b", "qwen2.5-coder:7b", "qwen3:8b",
                        "nomic-embed-text"], hardware=HW_HIGH)
    assert resolution is None
    assert changed is False
    assert _roles(cfg) == before
    assert cfg.get("models.custom.assign.chat") == "llama3.1:8b"
    assert cfg.get("models.mode") == "custom"
    assert cfg.get("llm.meta.source") == "custom"


def test_custom_model_removal_preserves_intent_and_fallback(tmp_path):
    cfg = _make_cfg(tmp_path)
    _set_custom_assign(cfg, chat="gone:model", coding="qwen2.5-coder:7b")
    installed = ["llama3.1:8b", "qwen2.5-coder:7b", "nomic-embed-text"]
    apply_custom(cfg, installed=installed, hardware=HW_HIGH)
    # M3.2 behavior intact: intent preserved, runtime role fell back safely.
    assert cfg.get("models.custom.assign.chat") == "gone:model"
    assert cfg.get("llm.roles.chat.model") == "llama3.1:8b"  # safe fallback
    assert cfg.get("llm.roles.coder.model") == "qwen2.5-coder:7b"

    # the custom 'chat' model stays absent across a lifecycle refresh -> NOOP.
    before = _roles(cfg)
    resolution, changed = recompute_automatic_if_active(cfg, installed=installed,
                                                        hardware=HW_HIGH)
    assert resolution is None and changed is False
    assert _roles(cfg) == before
    assert cfg.get("models.custom.assign.chat") == "gone:model"
    assert cfg.get("models.mode") == "custom"


# ── Hardware change ───────────────────────────────────────────────────────


def test_automatic_hardware_change_reresolves_assignments(tmp_path):
    cfg = _make_cfg(tmp_path)
    installed = ["gemma4", "llama3.1:8b", "qwen2.5vl:7b", "nomic-embed-text"]
    apply_automatic(cfg, installed=installed, hardware=HW_HIGH)
    # On 8 GB, gemma4 (min_vram hint) is demoted -> supported llama wins.
    assert cfg.get("llm.roles.planner.model") == "llama3.1:8b"

    # 24 GB lifts the demotion -> trusted gemma4 wins for reasoning.
    resolution, changed = recompute_automatic_if_active(
        cfg, installed=installed, hardware=HW_BIG)
    assert changed is True
    assert cfg.get("llm.roles.planner.model") == "gemma4"


def test_custom_hardware_change_keeps_assignments_authoritative(tmp_path):
    cfg = _make_cfg(tmp_path)
    _set_custom_assign(cfg, chat="llama3.1:8b", reasoning="gemma4")
    installed = ["gemma4", "llama3.1:8b", "nomic-embed-text"]
    apply_custom(cfg, installed=installed, hardware=HW_BIG)
    assert cfg.get("llm.roles.planner.model") == "gemma4"

    # hardware refreshes to a state that would demote gemma4 -> untouched.
    before = _roles(cfg)
    resolution, changed = recompute_automatic_if_active(cfg, installed=installed,
                                                        hardware=HW_HIGH)
    assert resolution is None and changed is False
    assert _roles(cfg) == before
    assert cfg.get("models.custom.assign.reasoning") == "gemma4"
    assert cfg.get("llm.meta.source") == "custom"


# ── Startup ───────────────────────────────────────────────────────────────


def test_automatic_startup_produces_current_resolution(tmp_path):
    # First run on a fresh config: recompute populates llm.roles.*.
    cfg = _make_cfg(tmp_path)
    installed = ["qwen3:8b", "qwen2.5vl:7b", "nomic-embed-text"]
    resolution, changed = recompute_automatic_if_active(
        cfg, installed=installed, hardware=HW_HIGH)
    assert changed is True
    assert cfg.get("models.mode") == "automatic"
    assert cfg.get("llm.meta.source") == "automatic"
    assert cfg.get("llm.roles.chat.model") == "qwen3:8b"

    # Restart: load the persisted file again -> recompute is a no-op (state
    # already reflects the current hardware + installed models).
    cfg2 = Configuration(build_registry(), cfg.store.path)
    cfg2.initialize()
    res2, changed2 = recompute_automatic_if_active(
        cfg2, installed=installed, hardware=HW_HIGH)
    assert changed2 is False
    assert cfg2.get("llm.roles.chat.model") == "qwen3:8b"
    assert cfg2.get("llm.meta.source") == "automatic"


# ── No-op recomputation / idempotency ─────────────────────────────────────


def test_noop_recompute_emits_no_events(tmp_path):
    from cozmo.configuration.events import ConfigBus
    bus = ConfigBus()
    events = []
    bus.on_any(lambda ev: events.append(ev.path))
    cfg = _make_cfg(tmp_path, bus=bus)
    installed = ["qwen3:8b", "qwen2.5vl:7b", "nomic-embed-text"]
    apply_automatic(cfg, installed=installed, hardware=HW_HIGH)
    base = len(events)

    resolution, changed = recompute_automatic_if_active(
        cfg, installed=installed, hardware=HW_HIGH)
    assert changed is False
    assert len(events) == base  # no config writes -> no events -> no loop


def test_recompute_converges_after_one_change(tmp_path):
    bus_events = []
    from cozmo.configuration.events import ConfigBus
    bus = ConfigBus()
    bus.on_any(lambda ev: bus_events.append(ev.path))
    cfg = _make_cfg(tmp_path, bus=bus)
    installed = ["llama3.1:8b", "qwen2.5vl:7b", "nomic-embed-text"]
    apply_automatic(cfg, installed=installed, hardware=HW_HIGH)

    # one external change -> first recompute writes (emit), second is a no-op.
    recompute_automatic_if_active(cfg, installed=installed + ["qwen3:8b"],
                                  hardware=HW_HIGH)
    assert bus_events
    first = len(bus_events)
    recompute_automatic_if_active(cfg, installed=installed + ["qwen3:8b"],
                                  hardware=HW_HIGH)
    assert len(bus_events) == first  # converged, loop-free


# ── Provenance ────────────────────────────────────────────────────────────


def test_automatic_recompute_preserves_provenance(tmp_path):
    cfg = _make_cfg(tmp_path)
    # simulate a stale/custom provenance that must be corrected back to
    # automatic only when Automatic is authoritative
    apply_automatic(cfg, installed=["qwen3:8b", "nomic-embed-text"],
                    hardware=HW_HIGH)
    cfg.set("llm.meta.source", "custom", by="test")  # inconsistent state
    recompute_automatic_if_active(cfg, installed=["qwen3:8b", "nomic-embed-text"],
                                  hardware=HW_HIGH)
    assert cfg.get("models.mode") == "automatic"
    assert cfg.get("llm.meta.source") == "automatic"


# ── Runtime application ───────────────────────────────────────────────────


def test_changed_roles_reach_runtime_apply_path(tmp_path):
    applied = []
    reg = build_registry()
    reg.require_owner("runtime", lambda p, v, prev: applied.append((p, v)))
    cfg = Configuration(reg, tmp_path / "cozmo.toml")
    cfg.initialize()
    installed = ["llama3.1:8b", "qwen2.5-coder:7b", "nomic-embed-text"]
    recompute_automatic_if_active(cfg, installed=installed, hardware=HW_HIGH)
    assert ("llm.roles.chat.model", "llama3.1:8b") in applied

    # unchanged recompute must NOT re-trigger the runtime reload path.
    applied.clear()
    recompute_automatic_if_active(cfg, installed=installed, hardware=HW_HIGH)
    assert applied == []


# ── No automatic installation ─────────────────────────────────────────────


def test_recompute_seam_never_installs():
    import inspect
    from cozmo.configuration import resolver
    text = inspect.getsource(resolver)
    assert "pull" not in text
    assert "ModelInstaller" not in text
    assert "install_model" not in text
    assert "/api/pull" not in text


def test_recompute_returns_results_for_consumers(tmp_path):
    cfg = _make_cfg(tmp_path)
    resolution, changed = recompute_automatic_if_active(
        cfg, installed=["qwen3:8b", "nomic-embed-text"], hardware=HW_HIGH)
    assert changed is True
    data = resolution.to_dict()
    assert data["mode"] == "automatic"
    assert data["meta"]["source"] == "automatic"
    assert data["roleMap"]["chat"] == "qwen3:8b"
    # Embeddings stay internal: resolved to embedding.model, never a custom
    # capability and never part of the user-facing role map.
    assert resolution.embedding_model == "nomic-embed-text"
    assert "embedding" not in data["roleMap"]