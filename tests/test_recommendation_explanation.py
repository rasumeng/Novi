"""Structured recommendation explanation tests (Phase 6 Task 3).

Verifies that every workload recommendation carries structured explanation
data — winner provenance, hardware-fit basis, provisional state, and viable
alternatives — without inventing information, without model-name logic, and
without ever writing configuration.

Key contracts:
* recommendation remains pure advisory (no writes, no mutation of inputs)
* provenance is preserved (runtime / seed / name-inference)
* alternatives come from the recommendation engine's candidate set
* seed membership does not gate alternatives (an unseeded model with runtime
  evidence is a valid candidate)
"""

import pytest

from cozmo.configuration.bootstrap import build_registry, DEFAULT_CONFIG
from cozmo.configuration.hardware import (
    DetectionConfidence,
    GpuConfidence,
    GpuInfo,
    HardwareProfile,
)
from cozmo.configuration.manager import Configuration
from cozmo.configuration.model_records import (
    CapabilityEvidence,
    ModelRecord,
    ModelStatus,
)
from cozmo.configuration.qualification import Qualification
from cozmo.configuration.resolver import WORKLOADS, recommend


def hw(gpu="", vram=None, gpu_conf=GpuConfidence.UNKNOWN, ram=None,
       conf=DetectionConfidence.UNKNOWN) -> HardwareProfile:
    return HardwareProfile(
        gpu=GpuInfo(vendor="nvidia" if gpu else "", name=gpu,
                    vram_total_gb=vram, confidence=gpu_conf),
        ram_gb=ram, confidence=conf,
    )


HW_HIGH = hw("RTX 4060", 8.0, GpuConfidence.KNOWN_VRAM, 32.0, DetectionConfidence.HIGH)
HW_UNKNOWN = hw("", None, GpuConfidence.UNKNOWN, None, DetectionConfidence.UNKNOWN)


def runtime_record(name, caps, **extra):
    return ModelRecord(
        name=name,
        status=ModelStatus.INSTALLED,
        capabilities=[CapabilityEvidence(c, True, "runtime", 0.95) for c in caps],
        **extra,
    )


def seed_record(name, qual, caps, **extra):
    return ModelRecord(
        name=name,
        status=ModelStatus.INSTALLED,
        qualification=qual,
        capabilities=[CapabilityEvidence(c, True, "seed", 0.9) for c in caps],
        **extra,
    )


def _explanation(r, workload="general"):
    rec = r.workloads[workload]
    return rec.to_dict()["explanation"]


# ── 1. Structured explanation present ──────────────────────────────────────

def test_workload_recommendation_contains_structured_explanation():
    installed = [
        seed_record("qwen3:8b", Qualification.TRUSTED,
                    ["chat", "reasoning", "coding"]),
        seed_record("llama3.1:8b", Qualification.SUPPORTED,
                    ["chat", "reasoning"]),
    ]
    r = recommend(HW_HIGH, installed)
    for w in WORKLOADS:
        assert r.workloads[w].model  # a winner exists
        exp = _explanation(r, w)
        assert set(exp.keys()) == {"provenance", "hardwareFit", "alternatives", "provisional"}


# ── 2. Winner provenance preserved ─────────────────────────────────────────

def test_winner_provenance_source_preserved():
    # two runtime-evidenced candidates -> provenance.source == "runtime"
    r = recommend(HW_HIGH, [runtime_record("a:7b", ["chat"]),
                            runtime_record("b:7b", ["chat", "reasoning"])])
    exp = _explanation(r)
    assert exp["provenance"]["source"] == "runtime"
    assert exp["provenance"]["confidence"] == 0.95


def test_winner_provenance_seed_preserved():
    r = recommend(HW_HIGH, [seed_record("qwen3:8b", Qualification.TRUSTED, ["chat"])])
    exp = _explanation(r)
    assert exp["provenance"]["source"] == "seed"
    assert exp["provenance"]["confidence"] == 0.9


# ── 3. Hardware fit information preserved ──────────────────────────────────

def test_hardware_fit_basis_preserved_when_known():
    record = runtime_record("big:70b", ["chat"],
                            min_vram_gb=4.0, approx_ram_gb=4.0)
    r = recommend(HW_HIGH, [record])
    hwf = _explanation(r)["hardwareFit"]
    assert hwf["fit"] == "fits"
    assert hwf["confidence"] == "high"
    assert hwf["strength"] == "strong"
    assert set(hwf["basis"]) == {"curated VRAM hint", "explicit memory requirement"}


def test_hardware_fit_unknown_when_no_basis():
    r = recommend(HW_HIGH, [runtime_record("a:7b", ["chat"])])
    hwf = _explanation(r)["hardwareFit"]
    assert hwf["fit"] == "unknown"
    assert hwf["strength"] == "unknown"
    assert hwf["basis"] == []


# ── 4. Provisional state preserved ─────────────────────────────────────────

def test_provisional_propagates_into_explanation():
    r_high = recommend(HW_HIGH, [seed_record("qwen3:8b", Qualification.TRUSTED, ["chat"])])
    assert r_high.provisional is False
    assert _explanation(r_high)["provisional"] is False

    r_unknown = recommend(HW_UNKNOWN, [seed_record("qwen3:8b", Qualification.TRUSTED, ["chat"])])
    assert r_unknown.provisional is True
    assert _explanation(r_unknown)["provisional"] is True


# ── 5. Alternatives come from the engine ───────────────────────────────────

def test_alternatives_are_viable_candidates_from_engine():
    installed = [
        seed_record("qwen3:8b", Qualification.TRUSTED,
                    ["chat", "reasoning", "coding"]),
        seed_record("llama3.1:8b", Qualification.SUPPORTED, ["chat", "reasoning"]),
        seed_record("qwen2.5-coder:7b", Qualification.SUPPORTED, ["chat", "coding"]),
    ]
    r = recommend(HW_HIGH, installed)
    exp = _explanation(r, "general")
    winner = r.workloads["general"].model
    assert winner == "qwen3:8b"
    assert exp["alternatives"], "expected viable alternatives"
    for alt in exp["alternatives"]:
        assert alt["model"] != winner
        assert set(alt.keys()) == {"model", "fit", "strength", "capability",
                                   "qualification", "reasons"}
        assert alt["capability"] == "chat"
        assert alt["strength"] in {
            "runtime", "trusted-seed", "supported-seed", "reported",
            "experimental-seed", "name-inference"}
    models = [a["model"] for a in exp["alternatives"]]
    assert "llama3.1:8b" in models or "qwen2.5-coder:7b" in models


def test_no_alternatives_when_single_candidate():
    r = recommend(HW_HIGH, [seed_record("qwen3:8b", Qualification.TRUSTED, ["chat"])])
    exp = _explanation(r)
    assert exp["alternatives"] == []


# ── 6/7. Unseeded runtime-evidenced models ─────────────────────────────────

def test_unseeded_runtime_evidenced_model_can_be_winner():
    # catalog={} -> no seed enrichment at all; runtime evidence alone wins.
    r = recommend(HW_HIGH, [runtime_record("custom:7b", ["chat", "reasoning"])], catalog={})
    rec = r.workloads["general"]
    assert rec.model == "custom:7b"
    assert _explanation(r)["provenance"]["source"] == "runtime"


def test_unseeded_runtime_evidenced_model_can_be_alternative():
    r = recommend(HW_HIGH, [runtime_record("winner:7b", ["chat", "reasoning"]),
                            runtime_record("runnerup:7b", ["chat"])], catalog={})
    rec = r.workloads["general"]
    assert rec.model == "winner:7b"
    models = [a["model"] for a in _explanation(r)["alternatives"]]
    assert "runnerup:7b" in models


# ── 8. Seed membership does not gate alternatives ──────────────────────────

def test_seed_membership_does_not_determine_alternative_validity():
    # A seeded-but-not-installed model must never appear as an alternative.
    catalog = {"ghost:8b": "placeholder"}  # dict truthiness irrelevant; not installed
    installed = [
        runtime_record("a:7b", ["chat"]),
        runtime_record("b:7b", ["chat", "reasoning"]),
    ]
    r = recommend(HW_HIGH, installed, catalog=catalog)
    models = [a["model"] for a in _explanation(r, "general")["alternatives"]]
    assert "ghost:8b" not in models
    assert "a:7b" in models or "b:7b" in models


# ── 9. No model-name logic (covered by architecture guard) ─────────────────

def test_explanation_contains_no_hardcoded_model_names():
    # Proves the payload is name-agnostic evidence, not a model-name table.
    r = recommend(HW_HIGH, [runtime_record("a:7b", ["chat", "reasoning", "coding"]),
                            runtime_record("b:7b", ["chat", "reasoning", "coding"])],
                  catalog={})
    for w in WORKLOADS:
        assert r.workloads[w].model  # deterministic winner (name tie-break)
    text = str(_explanation(r))
    for token in ("llama", "qwen", "gemma", "mistral", "phi"):
        assert token not in text.lower()


# ── 10/11. Pure + never writes ─────────────────────────────────────────────

def test_recommend_remains_pure_and_deterministic():
    installed = [runtime_record("a:7b", ["chat", "reasoning"]),
                 runtime_record("b:7b", ["chat"])]
    before = [m.to_dict() for m in installed]
    r1 = recommend(HW_HIGH, installed, catalog={})
    r2 = recommend(HW_HIGH, installed, catalog={})
    assert r1.to_dict() == r2.to_dict()
    after = [m.to_dict() for m in installed]
    assert before == after  # inputs not mutated


def test_explanation_generation_never_writes_configuration(tmp_path):
    reg = build_registry()
    cfg = Configuration(reg, tmp_path / "cozmo.toml", defaults=DEFAULT_CONFIG)
    cfg.initialize()
    before = cfg.snapshot()
    installed = [seed_record("qwen3:8b", Qualification.TRUSTED,
                             ["chat", "reasoning", "coding"])]
    recommend(HW_HIGH, installed)
    assert cfg.snapshot() == before
    assert cfg.get("llm.workloads.general.model") == ""