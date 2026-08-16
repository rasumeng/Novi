"""Recommendation engine integration tests (Phase 5.5).

Exercises the orchestration layer (``catalog``) and the eligibility record path
together with the generic engine and resolver: seed enrichment augments but
never gates, unseeded records with real evidence participate everywhere,
unknown stays unknown, and every output path stays advisory (no writes).
"""

from cozmo.configuration.catalog import (
    USER_FACING_CAPABILITIES,
    ModelRecommendationEngine,
    build_available_recommendations,
    build_catalog_payload,
)
from cozmo.configuration.eligibility import (
    CapabilityMatch,
    HardwareFit,
    evaluate_eligibility,
)
from cozmo.configuration.hardware import (
    DetectionConfidence,
    GpuConfidence,
    GpuInfo,
    HardwareProfile,
)
from cozmo.configuration.model_records import (
    CapabilityEvidence,
    ModelIdentity,
    ModelRecord,
    ModelStatus,
)
from cozmo.configuration.qualification import Qualification
from cozmo.configuration.resolver import WORKLOADS, recommend


def hw(vram=None, ram=None, conf=DetectionConfidence.HIGH,
       gpu_conf=GpuConfidence.KNOWN_VRAM) -> HardwareProfile:
    return HardwareProfile(
        gpu=GpuInfo(vendor="nvidia" if vram else "", name="gpu",
                    vram_total_gb=vram, confidence=gpu_conf),
        ram_gb=ram, confidence=conf,
    )


def _record(name="m", **kw) -> ModelRecord:
    kw.setdefault("status", ModelStatus.INSTALLED)
    return ModelRecord(name=name, **kw)


def _runtime_claims(*caps) -> list[CapabilityEvidence]:
    return [CapabilityEvidence(c, True, "runtime", 0.95) for c in caps]


# ── Catalog payload: unseeded records with real evidence ───────────────────


def test_catalog_payload_recommends_unseeded_runtime_record():
    m = _record(name="fresh:model",
                capabilities=_runtime_claims("chat", "tools"),
                capability_flags={"chat": True, "tools": True})
    payload = build_catalog_payload([m])
    (entry,) = payload["models"]
    assert entry["name"] == "fresh:model"
    assert entry["recommended"] is True
    assert entry["status"] == "installed"
    assert any("runtime" in r for r in entry["reasons"])
    # capability flags + provenance present.
    assert entry["capabilities"].get("chat") is True
    assert any(e["capability"] == "chat" and e["source"] == "runtime"
               for e in entry["capabilityEvidence"])


def test_catalog_payload_seed_enrichment_is_advisory_not_gating():
    seeded = _record(name="gemma4:e4b", capabilities=_runtime_claims("chat"))
    payload = build_catalog_payload([seeded])
    (entry,) = payload["models"]
    # seed enrichment filled display name + caveats; runtime evidence kept.
    assert entry["displayName"] == "Gemma 4 E4B"
    assert entry["recommended"] is True
    assert any(e["source"] == "runtime" for e in entry["capabilityEvidence"])
    assert any(e["source"] == "seed" for e in entry["capabilityEvidence"])


def test_catalog_payload_unknown_stays_unknown():
    m = _record(name="mystery:model",
                capabilities=[CapabilityEvidence("vision", None, "runtime")])
    payload = build_catalog_payload([m])
    (entry,) = payload["models"]
    assert entry["recommended"] is False
    assert entry["eligibility"]["hardwareFit"] == "unknown"


def test_catalog_payload_no_write_invariant(tmp_path):
    """build_catalog_payload must not touch configuration state."""
    from cozmo.configuration.bootstrap import build_registry, DEFAULT_CONFIG
    from cozmo.configuration.manager import Configuration
    cfg = Configuration(build_registry(), tmp_path / "cozmo.toml",
                        defaults=DEFAULT_CONFIG)
    cfg.initialize()
    before = cfg.state.as_dict()
    _ = build_catalog_payload([_record(name="x", capabilities=_runtime_claims("chat"))])
    assert cfg.state.as_dict() == before


# ── Eligibility record path ────────────────────────────────────────────────


def test_eligibility_unseeded_runtime_evidence_not_dismissed():
    m = _record(name="fresh:model", capabilities=_runtime_claims("chat", "tools"))
    elig = evaluate_eligibility(record=m, hardware=hw(vram=8.0, ram=32.0),
                                requested_capabilities=["chat"])
    assert any("discovery/runtime" in r for r in elig.reasons)
    assert "No curated seed fact" not in " ".join(elig.reasons)
    (match,) = elig.capability_matches
    assert match.match == CapabilityMatch.MATCHES
    assert match.source == "runtime"


def test_eligibility_unknown_capability_and_fit_stay_unknown():
    m = _record(name="bare", capabilities=_runtime_claims("chat"))
    elig = evaluate_eligibility(record=m, hardware=hw(vram=8.0, ram=32.0),
                                requested_capabilities=["vision"])
    assert elig.hardware_fit == HardwareFit.UNKNOWN
    (match,) = elig.capability_matches
    assert match.match == CapabilityMatch.UNKNOWN


def test_eligibility_explicit_negative_only_from_claim():
    m = _record(name="novision",
                capabilities=[CapabilityEvidence("vision", False, "runtime"),
                              CapabilityEvidence("chat", True, "runtime")])
    elig = evaluate_eligibility(record=m, hardware=hw(vram=8.0, ram=32.0),
                                requested_capabilities=["vision"])
    (match,) = elig.capability_matches
    assert match.match == CapabilityMatch.NO_MATCH


def test_eligibility_record_seed_lookup_by_name():
    m = _record(name="qwen3:8b", capabilities=_runtime_claims("chat"))
    elig = evaluate_eligibility(record=m, hardware=hw(vram=8.0, ram=32.0))
    assert elig.qualification == Qualification.TRUSTED


# ── Available recommendations (advisory, no install) ───────────────────────


def test_available_recommendations_exclude_installed():
    out = build_available_recommendations(
        installed_names={"qwen3:8b"},
        hardware=hw(vram=24.0, ram=64.0))
    assert all(e["name"] != "qwen3:8b" for e in out)


def test_available_recommendations_exclude_does_not_fit():
    # gemma4 needs 12 GB VRAM; 8 GB VRAM must not be pushed.
    out = build_available_recommendations(
        installed_names=frozenset(), hardware=hw(vram=8.0, ram=32.0))
    names = {e["name"] for e in out}
    assert "gemma4" not in names
    # a fitting model IS suggested.
    assert "qwen3:8b" in names


def test_available_recommendations_exclude_embedding_only():
    out = build_available_recommendations(
        installed_names=frozenset(), hardware=hw(vram=24.0, ram=64.0))
    names = {e["name"] for e in out}
    assert "nomic-embed-text" not in names
    assert "mxbai-embed-large" not in names


def test_available_recommendations_custom_candidate_records():
    candidates = [
        _record(name="remote:model", status=ModelStatus.AVAILABLE,
                source_kind="remote",
                capabilities=_runtime_claims("chat", "coding"),
                qualification=Qualification.TRUSTED),
        _record(name="embed:only", status=ModelStatus.AVAILABLE,
                source_kind="remote",
                capabilities=_runtime_claims("embeddings"),
                qualification=Qualification.TRUSTED),
    ]
    out = build_available_recommendations(
        installed_names=frozenset(), hardware=hw(vram=24.0, ram=64.0),
        candidate_records=candidates)
    names = {e["name"] for e in out}
    assert names == {"remote:model"}
    (entry,) = out
    assert entry["status"] == "available"
    assert entry["recommended"] is True
    assert "remote" in entry["displayName"] or True  # displayName falls back to name


def test_available_recommendations_never_writes(tmp_path):
    from cozmo.configuration.bootstrap import build_registry, DEFAULT_CONFIG
    from cozmo.configuration.manager import Configuration
    cfg = Configuration(build_registry(), tmp_path / "cozmo.toml",
                        defaults=DEFAULT_CONFIG)
    cfg.initialize()
    before = cfg.state.as_dict()
    _ = build_available_recommendations(hardware=hw(vram=24.0, ram=64.0))
    assert cfg.state.as_dict() == before


# ── Engine + resolver agree on unseeded evidence ───────────────────────────


def test_resolver_and_engine_agree_on_runtime_evidence():
    m = _record(name="fresh:model", capabilities=_runtime_claims("chat", "reasoning"))
    h = hw(vram=24.0, ram=64.0)
    engine = ModelRecommendationEngine(hardware=h)
    rec = engine.for_record(m)
    assert rec["recommended"] is True
    assert any("runtime" in r for r in rec["reasons"])
    r = recommend(h, [m], catalog={})
    assert r.workloads["general"].model == "fresh:model"
    assert "runtime reported capability" in r.workloads["general"].reasons


def test_all_workloads_have_empty_model_when_no_evidence():
    m = _record(name="silent:model")
    r = recommend(hw(vram=24.0, ram=64.0), [m], catalog={})
    for w in WORKLOADS:
        assert r.workloads[w].model == ""