"""Generic recommendation engine tests (Phase 5.5).

Covers the model-name-agnostic primitives in
``novi.configuration.recommendation``: evidence grading (unknown stays
unknown, explicit-only negatives), capability tri-state answers, memory
bounds, hardware fit (strong/weak/unknown, never fabricated), score + rank
components, and advisory seed enrichment. Also verifies the engine never
imports the curated seed table and never keys behaviour off model names.

Architecture invariants locked in here:
* Evidence strength is deterministic: runtime > trusted-seed >
  supported-seed > reported > experimental-seed > name-inference > none.
* Absence of a capability claim is *unknown*, never ``False``; a negative
  capability only comes from an explicit ``supported=False`` claim.
* Memory/hardware fit is never invented; missing values stay unknown.
* Seed metadata *augments* evidence (advisory); it never gates eligibility.
"""

import ast
from pathlib import Path

import pytest

from novi.configuration.hardware import (
    DetectionConfidence,
    GpuConfidence,
    GpuInfo,
    HardwareProfile,
)
from novi.configuration.model_records import (
    CapabilityEvidence,
    ModelIdentity,
    ModelRecord,
)
from novi.configuration.qualification import Qualification
from novi.configuration.recommendation import (
    ESTIMATE_OVERHEAD,
    EvidenceStrength,
    HardwareFit,
    capability_support,
    evidence_grade,
    hardware_fit_for_record,
    memory_bounds,
    merge_curated_evidence,
    positive_capability_names,
    rank_components,
    recommendation_score,
)
from novi.configuration.resolver import WORKLOADS, recommend


def _record(name="test:model", **kw) -> ModelRecord:
    return ModelRecord(name=name, **kw)


def _claims(*specs) -> list[CapabilityEvidence]:
    out = []
    for cap, supported, source in specs:
        out.append(CapabilityEvidence(cap, supported, source))
    return out


def hw(vram=None, ram=None, conf=DetectionConfidence.HIGH,
       gpu_conf=GpuConfidence.KNOWN_VRAM) -> HardwareProfile:
    return HardwareProfile(
        gpu=GpuInfo(vendor="nvidia" if vram else "", name="gpu",
                    vram_total_gb=vram, confidence=gpu_conf),
        ram_gb=ram, confidence=conf,
    )


# ── Evidence grading: strength ordering, unknown stays unknown ─────────────


def test_evidence_strength_ordering():
    """runtime > trusted-seed > supported-seed > reported >
    experimental-seed > name-inference > none."""
    claims = [
        CapabilityEvidence("chat", True, "runtime"),
        CapabilityEvidence("chat", True, "seed"),
        CapabilityEvidence("chat", True, "seed"),
        CapabilityEvidence("chat", True, "reported"),
        CapabilityEvidence("chat", True, "name-inference"),
    ]
    r = _record(capabilities=claims, qualification=Qualification.SUPPORTED)
    strength, _, _ = evidence_grade(r.capabilities, "chat", r.qualification)
    assert strength == EvidenceStrength.RUNTIME


def test_seed_strength_follows_qualification():
    trusted = _record(
        capabilities=_claims(("chat", True, "seed")),
        qualification=Qualification.TRUSTED)
    supported = _record(
        capabilities=_claims(("chat", True, "seed")),
        qualification=Qualification.SUPPORTED)
    experimental = _record(
        capabilities=_claims(("chat", True, "seed")),
        qualification=Qualification.EXPERIMENTAL)
    assert evidence_grade(trusted.capabilities, "chat", trusted.qualification)[0] \
        == EvidenceStrength.SEED_TRUSTED
    assert evidence_grade(supported.capabilities, "chat", supported.qualification)[0] \
        == EvidenceStrength.SEED_SUPPORTED
    assert evidence_grade(experimental.capabilities, "chat", experimental.qualification)[0] \
        == EvidenceStrength.SEED_EXPERIMENTAL


def test_missing_capability_is_unknown_not_false():
    r = _record(capabilities=_claims(("tools", True, "runtime")))
    strength, _, _ = evidence_grade(r.capabilities, "chat", r.qualification)
    assert strength == EvidenceStrength.NONE


def test_explicit_unsupported_is_not_positive_evidence():
    r = _record(capabilities=_claims(("vision", False, "runtime")))
    strength, _, _ = evidence_grade(r.capabilities, "vision", r.qualification)
    assert strength == EvidenceStrength.NONE


def test_negative_capability_only_from_explicit_claim():
    r = _record(capabilities=_claims(("vision", False, "runtime")))
    assert capability_support(r.capabilities, "vision") == (False, "runtime", None)
    # absence of any claim stays unknown, never False.
    r2 = _record(capabilities=_claims(("chat", True, "runtime")))
    assert capability_support(r2.capabilities, "vision") == (None, None, None)


def test_unknown_claim_does_not_resolve_capability():
    r = _record(capabilities=[CapabilityEvidence("vision", None, "runtime")])
    assert capability_support(r.capabilities, "vision") == (None, None, None)


def test_positive_capability_names_only_positive():
    r = _record(capabilities=[
        CapabilityEvidence("chat", True, "runtime"),
        CapabilityEvidence("vision", False, "runtime"),
        CapabilityEvidence("tools", None, "runtime"),
    ])
    assert positive_capability_names(r) == {"chat"}


# ── Memory bounds: derived, never fabricated ───────────────────────────────


def test_memory_bounds_from_disk_size():
    r = _record(size_bytes=int(8 * (1024 ** 3)))
    assert memory_bounds(r) == (8.0, 8.0, ["disk size"])


def test_memory_bounds_from_parameters_and_quantization():
    r = _record(
        parameter_count="7.6B",
        identity=ModelIdentity(name="x", quantization="q4_k_m"),
    )
    lower, upper, labels = memory_bounds(r)
    # q4_k_m = 0.59 B/param -> ~4.48 GB
    assert lower == pytest.approx(7.6 * 0.59, rel=1e-6)
    assert upper == lower
    assert labels == ["parameter count + quantization"]


def test_memory_bounds_none_when_no_basis():
    assert memory_bounds(_record(name="bare")) is None


def test_memory_bounds_uses_conservative_min_and_max():
    r = _record(
        size_bytes=int(5 * (1024 ** 3)),
        parameter_count="7.6B",
        identity=ModelIdentity(name="x", quantization="q4_k_m"),
    )
    lower, upper, _ = memory_bounds(r)
    assert lower == pytest.approx(7.6 * 0.59, rel=1e-6)
    assert upper == 5.0


# ── Hardware fit: strong, weak, unknown — never invented ───────────────────


def test_strong_fit_fits_when_both_hint_and_ram_ok():
    r = _record(min_vram_gb=12.0, approx_ram_gb=16.0)
    d = hardware_fit_for_record(r, hw(vram=24.0, ram=32.0))
    assert d.fit == HardwareFit.FITS
    assert d.strength == "strong"


def test_strong_fit_does_not_fit_when_vram_hint_exceeded():
    r = _record(min_vram_gb=12.0, approx_ram_gb=16.0)
    d = hardware_fit_for_record(r, hw(vram=8.0, ram=32.0))
    assert d.fit == HardwareFit.DOES_NOT_FIT
    assert d.strength == "strong"


def test_strong_fit_unknown_without_hints():
    d = hardware_fit_for_record(_record(name="bare"), hw(vram=8.0, ram=32.0))
    assert d.fit == HardwareFit.UNKNOWN
    assert d.strength == "unknown"


def test_weak_fit_fits_when_vram_comfortably_exceeds_estimate():
    r = _record(
        parameter_count="7.6B",
        identity=ModelIdentity(name="x", quantization="q4_k_m"),
    )
    # estimate 4.48 GB; 8 GB >= 4.48 * 1.25 -> FITS.
    d = hardware_fit_for_record(r, hw(vram=8.0, ram=32.0))
    assert d.fit == HardwareFit.FITS
    assert d.strength == "weak"


def test_weak_fit_does_not_fit_when_lower_bound_exceeds_vram():
    r = _record(size_bytes=int(20 * (1024 ** 3)))
    d = hardware_fit_for_record(r, hw(vram=8.0, ram=32.0))
    assert d.fit == HardwareFit.DOES_NOT_FIT
    assert d.strength == "weak"


def test_weak_fit_unknown_in_margin_between_bounds():
    r = _record(
        size_bytes=int(7 * (1024 ** 3)),
        parameter_count="7.6B",
        identity=ModelIdentity(name="x", quantization="q8_0"),
    )
    d = hardware_fit_for_record(r, hw(vram=8.0, ram=32.0))
    assert d.fit == HardwareFit.UNKNOWN


def test_fit_unknown_when_no_vram_known():
    r = _record(size_bytes=int(20 * (1024 ** 3)))
    d = hardware_fit_for_record(r, hw(vram=None, ram=32.0))
    assert d.fit == HardwareFit.UNKNOWN


def test_estimate_overhead_applied_to_positive_fit():
    r = _record(
        parameter_count="7.6B",
        identity=ModelIdentity(name="x", quantization="q4_k_m"),
    )
    est = memory_bounds(r)[1]
    # Just barely over the overhead margin -> not FITS.
    d = hardware_fit_for_record(r, hw(vram=est * ESTIMATE_OVERHEAD * 0.9, ram=32.0))
    assert d.fit == HardwareFit.UNKNOWN


# ── Score + rank components ────────────────────────────────────────────────


def test_rank_orders_over_budget_last():
    """DOES_NOT_FIT ranks last (worse) than any fitting candidate."""
    over = _record(
        capabilities=_claims(("chat", True, "runtime")), min_vram_gb=12.0)
    ok = _record(
        capabilities=_claims(("chat", True, "seed")),
        qualification=Qualification.TRUSTED)
    s_over = recommendation_score(over, "chat", hw(vram=8.0, ram=32.0))
    s_ok = recommendation_score(ok, "chat", hw(vram=8.0, ram=32.0))
    assert rank_components(s_over)[0] == 1   # DOES_NOT_FIT
    assert rank_components(s_ok)[0] == 0
    assert rank_components(s_ok) < rank_components(s_over)


def test_rank_orders_known_fit_before_unknown_fit():
    known = _record(
        capabilities=_claims(("chat", True, "seed")),
        qualification=Qualification.TRUSTED, min_vram_gb=6.0,
        approx_ram_gb=16.0)
    unknown = _record(
        capabilities=_claims(("chat", True, "seed")),
        qualification=Qualification.TRUSTED)
    s_known = recommendation_score(known, "chat", hw(vram=8.0, ram=32.0))
    s_unknown = recommendation_score(unknown, "chat", hw(vram=8.0, ram=32.0))
    assert rank_components(s_known)[1] == 0
    assert rank_components(s_unknown)[1] == 1
    assert rank_components(s_known) < rank_components(s_unknown)


def test_rank_orders_strength_then_confidence_then_breadth():
    trusted = _record(
        capabilities=_claims(("chat", True, "seed")),
        qualification=Qualification.TRUSTED)
    supported = _record(
        capabilities=_claims(("chat", True, "seed")),
        qualification=Qualification.SUPPORTED)
    broad = _record(
        capabilities=_claims(("chat", True, "seed"), ("tools", True, "seed")),
        qualification=Qualification.SUPPORTED)
    h = hw(vram=8.0, ram=32.0)
    st = rank_components(recommendation_score(trusted, "chat", h))
    ss = rank_components(recommendation_score(supported, "chat", h))
    sb = rank_components(recommendation_score(broad, "chat", h))
    # strength first (trusted wins), then breadth within equal strength.
    assert st < sb < ss


def test_rank_components_excludes_name():
    """Name is a tie-break applied at sort time, never a quality signal."""
    a = _record(capabilities=_claims(("chat", True, "seed")),
                qualification=Qualification.TRUSTED)
    b = _record(capabilities=_claims(("chat", True, "seed")),
                qualification=Qualification.TRUSTED)
    h = hw(vram=8.0, ram=32.0)
    assert rank_components(recommendation_score(a, "chat", h)) == \
        rank_components(recommendation_score(b, "chat", h))


# ── Seed enrichment is advisory, never gating ──────────────────────────────


class _Fact:
    """Duck-typed curated fact (never the seed table)."""

    def __init__(self, **kw):
        self.capabilities = kw.get("capabilities", [])
        self.qualification = kw.get("qualification", Qualification.EXPERIMENTAL)
        self.display_name = kw.get("display_name", "")
        self.min_vram_gb = kw.get("min_vram_gb")
        self.approx_ram_gb = kw.get("approx_ram_gb")
        self.caveats = kw.get("caveats", [])
        self.supports_tools = kw.get("supports_tools", False)
        self.supports_vision = kw.get("supports_vision", False)
        self.works_with_memory = kw.get("works_with_memory", False)
        self.license = kw.get("license")


def test_merge_curated_evidence_fills_gaps_and_keeps_stronger_claims():
    r = _record(
        name="m",
        capabilities=_claims(("chat", True, "runtime")),
        qualification=Qualification.EXPERIMENTAL)
    merged = merge_curated_evidence(
        r, _Fact(capabilities=["chat", "tools"], display_name="M",
                 qualification=Qualification.TRUSTED,
                 caveats=["note"], min_vram_gb=6.0))
    # runtime claim untouched (not downgraded to seed).
    assert evidence_grade(merged.capabilities, "chat", merged.qualification)[0] \
        == EvidenceStrength.RUNTIME
    assert positive_capability_names(merged) == {"chat", "tools"}
    assert merged.display_name == "M"
    assert merged.qualification == Qualification.TRUSTED
    assert merged.caveats == ["note"]
    assert merged.min_vram_gb == 6.0


def test_merge_curated_evidence_is_pure_and_advisory():
    r = _record(name="m", capabilities=_claims(("chat", True, "runtime")))
    merged = merge_curated_evidence(r, _Fact(capabilities=["tools"]))
    assert r.capabilities[0].capability == "chat"  # original untouched
    assert len(merged.capabilities) == 2
    assert r is not merged


def test_merge_curated_evidence_none_passthrough():
    r = _record(name="m")
    assert merge_curated_evidence(r, None) is r


def test_engine_has_no_seed_table_and_no_model_names():
    """Engine module must not import the seed table or special-case names."""
    path = Path(__file__).parent.parent / "novi" / "configuration" / "recommendation.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        elif isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
    assert not any("model_seeds" in i for i in imports)
    assert not any("SEED_MODEL_FACTS" in i for i in imports)
    # Check code only, not the module docstring (which may reference these).
    if tree.body and isinstance(tree.body[0], ast.Expr) \
            and isinstance(tree.body[0].value, ast.Constant):
        tree.body = tree.body[1:]
    body = ast.unparse(tree)
    assert "SEED_MODEL_FACTS" not in body
    assert "if model ==" not in body and "if name ==" not in body


# ── Resolver: evidence-based, seeded and unseeded ranked on evidence ───────


def test_unseeded_model_with_runtime_evidence_is_recommendable():
    record = _record(
        name="fresh:model",
        capabilities=_claims(("chat", True, "runtime"),
                             ("reasoning", True, "runtime")),
        qualification=Qualification.EXPERIMENTAL)
    r = recommend(hw(vram=8.0, ram=32.0), [record], catalog={})
    assert r.workloads["general"].model == "fresh:model"
    assert "runtime reported capability" in r.workloads["general"].reasons


def test_catalog_disabled_still_recommends_unseeded_evidence():
    a = _record(name="a", capabilities=_claims(("chat", True, "runtime")))
    b = _record(name="b", capabilities=_claims(("chat", True, "runtime")))
    r = recommend(hw(vram=8.0, ram=32.0), [a, b], catalog={})
    assert r.workloads["general"].model in {"a", "b"}
    assert r.workloads["general"].model in {"a", "b"}


def test_unknown_model_with_no_evidence_never_recommended():
    r = recommend(hw(vram=8.0, ram=32.0), ["random-unknown:99b"], catalog={})
    for w in WORKLOADS:
        assert r.workloads[w].model == ""
        assert "no installed candidate" in r.workloads[w].reasons[0]


def test_seed_membership_alone_is_not_evidence_strength():
    """With equivalent runtime evidence, an unseeded model ranks equal to a
    seeded one for the same capability (no seed penalty / no seed boost)."""
    seeded = _record(
        name="seeded:model",
        capabilities=_claims(("chat", True, "runtime")),
        qualification=Qualification.TRUSTED)
    unseeded = _record(
        name="unseeded:model",
        capabilities=_claims(("chat", True, "runtime")),
        qualification=Qualification.EXPERIMENTAL)
    h = hw(vram=8.0, ram=32.0)
    s1 = recommendation_score(seeded, "chat", h)
    s2 = recommendation_score(unseeded, "chat", h)
    assert s1.strength == s2.strength == EvidenceStrength.RUNTIME
    assert rank_components(s1)[:2] == rank_components(s2)[:2]