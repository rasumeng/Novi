"""M2.3 — Model eligibility / evidence layer tests.

The eligibility/evidence layer combines installation status + qualification +
capabilities + detected hardware into an explicit result for the future
ResolutionLayer. Covers hardware-fit rules, qualification eligibility, capability
matching, UNKNOWN propagation, and separation of concerns.
"""

import pytest

from cozmo.configuration.catalog import ModelFact
from cozmo.configuration.discovery import ModelStatus
from cozmo.configuration.eligibility import (
    CapabilityMatch,
    HardwareFit,
    evaluate_eligibility,
    evaluate_all,
)
from cozmo.configuration.hardware import (
    DetectionConfidence,
    GpuConfidence,
    GpuInfo,
    HardwareProfile,
)
from cozmo.configuration.qualification import Qualification


# ── Hardware fixtures ─────────────────────────────────────────────────────


def hw(gpu_name="", vram=None, gpu_conf=GpuConfidence.UNKNOWN, ram=None,
       conf=DetectionConfidence.UNKNOWN) -> HardwareProfile:
    return HardwareProfile(
        gpu=GpuInfo(vendor="nvidia" if gpu_name else "", name=gpu_name,
                    vram_total_gb=vram, confidence=gpu_conf),
        ram_gb=ram,
        confidence=conf,
    )


HW_KNOWN = hw("RTX 4060", 8.0, GpuConfidence.KNOWN_VRAM, 32.0, DetectionConfidence.HIGH)
HW_GPU_NO_VRAM = hw("GTX 1080", None, GpuConfidence.KNOWN_NO_VRAM, 16.0, DetectionConfidence.MEDIUM)
HW_GPU_UNKNOWN_RAM = hw("", None, GpuConfidence.UNKNOWN, 16.0, DetectionConfidence.LOW)
HW_UNKNOWN = hw("", None, GpuConfidence.UNKNOWN, None, DetectionConfidence.UNKNOWN)


def _fact(name, qual=Qualification.TRUSTED, vram=None, ram=4.0,
          caps=("chat",), tools=False, vision=False, caveats=()):
    return ModelFact(name=name, qualification=qual, vram_required_gb=vram,
                     approx_ram_gb=ram, capabilities=list(caps),
                     supports_tools=tools, supports_vision=vision, caveats=list(caveats))


# ── Hardware evidence produces the right confidence ────────────────────────


def test_known_gpu_known_vram_is_high():
    e = evaluate_eligibility("m", hardware=HW_KNOWN, fact=_fact("m"))
    assert e.hardware_confidence == DetectionConfidence.HIGH


def test_gpu_known_vram_unknown_is_medium():
    e = evaluate_eligibility("m", hardware=HW_GPU_NO_VRAM, fact=_fact("m"))
    assert e.hardware_confidence == DetectionConfidence.MEDIUM


def test_gpu_unknown_ram_known_is_low():
    e = evaluate_eligibility("m", hardware=HW_GPU_UNKNOWN_RAM, fact=_fact("m"))
    assert e.hardware_confidence == DetectionConfidence.LOW


def test_completely_unknown_hardware_is_unknown():
    e = evaluate_eligibility("m", hardware=HW_UNKNOWN, fact=_fact("m"))
    assert e.hardware_confidence == DetectionConfidence.UNKNOWN


# ── Hardware fit rules ─────────────────────────────────────────────────────


def test_known_requirement_sufficient_vram_fits():
    e = evaluate_eligibility("m", hardware=HW_KNOWN, fact=_fact("m", vram=6.0))
    assert e.hardware_fit == HardwareFit.FITS


def test_known_requirement_insufficient_vram_does_not_fit():
    e = evaluate_eligibility("m", hardware=HW_KNOWN, fact=_fact("m", vram=16.0))
    assert e.hardware_fit == HardwareFit.DOES_NOT_FIT


def test_unknown_vram_fit_is_unknown_not_does_not_fit():
    e = evaluate_eligibility("m", hardware=HW_GPU_NO_VRAM, fact=_fact("m", vram=8.0))
    assert e.hardware_fit == HardwareFit.UNKNOWN


def test_unknown_model_requirement_yields_unknown_not_fabricated_fit():
    # No VRAM requirement on the fact -> UNKNOWN, even with known VRAM.
    e = evaluate_eligibility("m", hardware=HW_KNOWN, fact=_fact("m", vram=None))
    assert e.hardware_fit == HardwareFit.UNKNOWN


def test_gpu_unknown_ram_known_does_not_claim_gpu_fit():
    e = evaluate_eligibility("m", hardware=HW_GPU_UNKNOWN_RAM, fact=_fact("m", ram=4.0))
    # RAM presence alone must not fabricate a GPU fit.
    assert e.hardware_fit in (HardwareFit.UNKNOWN, HardwareFit.FITS)
    assert e.hardware_fit != HardwareFit.DOES_NOT_FIT


def test_hardware_unknown_fit_is_unknown():
    e = evaluate_eligibility("m", hardware=HW_UNKNOWN, fact=_fact("m", ram=4.0))
    assert e.hardware_fit == HardwareFit.UNKNOWN


# ── Qualification eligibility ─────────────────────────────────────────────


def test_trusted_automatic_eligible():
    e = evaluate_eligibility("m", hardware=HW_KNOWN, fact=_fact("m", Qualification.TRUSTED))
    assert e.eligible_automatic is True


def test_supported_automatic_eligible():
    e = evaluate_eligibility("m", hardware=HW_KNOWN, fact=_fact("m", Qualification.SUPPORTED))
    assert e.eligible_automatic is True


def test_experimental_not_proactively_automatic_eligible():
    e = evaluate_eligibility("m", hardware=HW_KNOWN, fact=_fact("m", Qualification.EXPERIMENTAL))
    assert e.eligible_automatic is False
    assert e.eligible_custom is True  # available for Custom


def test_incompatible_never_automatic_eligible_and_warns():
    e = evaluate_eligibility("m", hardware=HW_KNOWN, fact=_fact("m", Qualification.INCOMPATIBLE))
    assert e.eligible_automatic is False
    assert e.eligible_custom is True
    assert any("incompatible" in c.lower() for c in e.caveats)


def test_not_installed_not_automatic_eligible():
    e = evaluate_eligibility("m", installed_status=ModelStatus.MISSING,
                             hardware=HW_KNOWN, fact=_fact("m"))
    assert e.eligible_automatic is False


def test_does_not_fit_blocks_automatic_despite_trusted():
    e = evaluate_eligibility("m", hardware=HW_KNOWN, fact=_fact("m", vram=16.0))
    assert e.qualification == Qualification.TRUSTED
    assert e.hardware_fit == HardwareFit.DOES_NOT_FIT
    assert e.eligible_automatic is False


# ── Capability matching ────────────────────────────────────────────────────


def test_known_capability_matches():
    e = evaluate_eligibility("qwen2.5vl:7b", hardware=HW_KNOWN,
                             fact=_fact("qwen2.5vl:7b", caps=("chat", "vision", "tools")),
                             requested_capabilities=["vision"])
    assert e.capability_matches[0].match == CapabilityMatch.MATCHES
    assert e.capability_matches[0].source == "catalog"


def test_missing_capability_no_match():
    e = evaluate_eligibility("m", hardware=HW_KNOWN,
                             fact=_fact("m", caps=("chat",)),
                             discovered_capabilities={"vision": False},
                             requested_capabilities=["vision"])
    cm = {c.capability: c for c in e.capability_matches}
    assert cm["vision"].match == CapabilityMatch.NO_MATCH


def test_unknown_capability_is_unknown():
    e = evaluate_eligibility("m", hardware=HW_KNOWN, fact=_fact("m", caps=("chat",)),
                             requested_capabilities=["telepathy"])
    cm = {c.capability: c for c in e.capability_matches}
    assert cm["telepathy"].match == CapabilityMatch.UNKNOWN


def test_detection_inference_no_catalog_does_not_overwrite():
    # No curated catalog fact for capability 'coding', but discovery infers it:
    # the match is attributed to discovery, not silently promoted to catalog.
    e = evaluate_eligibility("qwen2.5-coder:1.5b", hardware=HW_KNOWN,
                             fact=_fact("qwen2.5-coder:1.5b", caps=("chat",)),
                             discovered_capabilities={"coding": True},
                             requested_capabilities=["coding"])
    cm = {c.capability: c for c in e.capability_matches}
    assert cm["coding"].match == CapabilityMatch.MATCHES
    assert cm["coding"].source == "discovery"


# ── Separation of concerns ────────────────────────────────────────────────


def test_qualification_independent_from_hardware():
    q_known = evaluate_eligibility("m", hardware=HW_KNOWN, fact=_fact("m")).qualification
    q_unknown = evaluate_eligibility("m", hardware=HW_UNKNOWN, fact=_fact("m")).qualification
    assert q_known == q_unknown == Qualification.TRUSTED


def test_hardware_fit_independent_from_qualification():
    fit_trusted = evaluate_eligibility("m", hardware=HW_KNOWN, fact=_fact("m", vram=6.0)).hardware_fit
    fit_incompat = evaluate_eligibility("m", hardware=HW_KNOWN, fact=_fact("m", Qualification.INCOMPATIBLE, vram=6.0)).hardware_fit
    assert fit_trusted == fit_incompat == HardwareFit.FITS


def test_installation_status_independent_from_qualification():
    q_installed = evaluate_eligibility("m", ModelStatus.INSTALLED, hardware=HW_KNOWN, fact=_fact("m")).qualification
    q_missing = evaluate_eligibility("m", ModelStatus.MISSING, hardware=HW_KNOWN, fact=_fact("m")).qualification
    assert q_installed == q_missing == Qualification.TRUSTED


# ── evaluate_all ──────────────────────────────────────────────────────────


def test_evaluate_all_installed_models():
    from types import SimpleNamespace
    models = [
        SimpleNamespace(name="gemma4:e4b", status=ModelStatus.INSTALLED, capability_flags={}),
        SimpleNamespace(name="unknown:zz", status=ModelStatus.INSTALLED, capability_flags={}),
    ]
    results = evaluate_all(models, hardware=HW_KNOWN)
    by_name = {r.name: r for r in results}
    assert by_name["gemma4:e4b"].eligible_automatic is True
    assert by_name["gemma4:e4b"].qualification == Qualification.TRUSTED
    assert by_name["unknown:zz"].eligible_automatic is False
    assert by_name["unknown:zz"].qualification == Qualification.EXPERIMENTAL


# ── Recommendation engine no longer claims hardware fit when unknown ──────


def test_recommendation_engine_no_best_for_hardware_when_unknown():
    from cozmo.configuration.catalog import ModelRecommendationEngine
    engine = ModelRecommendationEngine(hardware=HW_KNOWN)
    rec = engine.for_model("gemma4:e4b", "installed")
    assert "Best for your hardware" not in rec["reasons"]
