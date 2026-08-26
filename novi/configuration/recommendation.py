"""Generic, model-name-agnostic recommendation primitives (Phase 5.5).

This module is the recommendation *engine*. It operates purely on
:class:`~novi.configuration.model_records.ModelRecord` and
:class:`~novi.configuration.hardware.HardwareProfile` values. It has no
knowledge of any curated seed table, candidate source, or model family:

* It never imports or iterates ``SEED_MODEL_FACTS``.
* It never keys behaviour off model names (no ``if model == ...``).
* It never fabricates capability or hardware facts: unknown stays unknown.

The engine consumes evidence already assembled on the record (provenance-rich
``CapabilityEvidence`` claims) and the detected hardware. Whatever produced the
record — an installed runtime inventory, a config-referenced missing model, a
seed-only advisory record, or a future remote source — is irrelevant here.

Evidence strength is deterministic and generic:

    runtime > trusted-seed > supported-seed > reported > name-inference > none

A model whose capability claim comes from runtime evidence is graded the same
whether or not it also has curated seed metadata: merely existing in the seed
table is never itself evidence strength.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Iterable, Optional, Tuple

from .hardware import HardwareProfile
from .model_records import CapabilityEvidence, ModelRecord
from .qualification import Qualification


# ── Evidence strength ──────────────────────────────────────────────────────

class EvidenceStrength(IntEnum):
    """Deterministic, generic strength of the best capability claim.

    Ordering (strong → weak): runtime > trusted-seed > supported-seed >
    reported > experimental-seed > name-inference > none.

    ``REPORTED`` is a positive claim whose provenance is not explicit (e.g.
    legacy capability flags or name-only callers). It is honest-but-unknown:
    stronger than a name guess, weaker than curated seed evidence.
    """

    NONE = 0
    NAME_INFERENCE = 1
    SEED_EXPERIMENTAL = 2
    REPORTED = 3
    SEED_SUPPORTED = 4
    SEED_TRUSTED = 5
    RUNTIME = 6


_QUAL_STRENGTH = {
    Qualification.TRUSTED: EvidenceStrength.SEED_TRUSTED,
    Qualification.SUPPORTED: EvidenceStrength.SEED_SUPPORTED,
    Qualification.EXPERIMENTAL: EvidenceStrength.SEED_EXPERIMENTAL,
}

_QUAL_CONFIDENCE = {
    Qualification.TRUSTED: 0.9,
    Qualification.SUPPORTED: 0.8,
    Qualification.EXPERIMENTAL: 0.5,
}

# The ``confidence`` used when a claim carries none of its own. Seed claims are
# refined by the record's qualification (see ``_claim_strength``).
_DEFAULT_SOURCE_CONFIDENCE = {
    "runtime": 0.95,
    "reported": 0.5,
    "name-inference": 0.7,
}


def _claim_strength(
    claim: CapabilityEvidence,
    qualification: Optional[Qualification] = None,
) -> Tuple[EvidenceStrength, float]:
    """Map one positive claim to (strength, confidence)."""
    source = (claim.source or "").lower()
    confidence = claim.confidence if claim.confidence is not None else None

    if source == "runtime":
        return (
            EvidenceStrength.RUNTIME,
            confidence if confidence is not None else _DEFAULT_SOURCE_CONFIDENCE["runtime"],
        )
    if source == "name-inference":
        return (
            EvidenceStrength.NAME_INFERENCE,
            confidence if confidence is not None else _DEFAULT_SOURCE_CONFIDENCE["name-inference"],
        )
    if source == "seed":
        strength = _QUAL_STRENGTH.get(qualification, EvidenceStrength.SEED_EXPERIMENTAL)
        return (
            strength,
            confidence if confidence is not None else _QUAL_CONFIDENCE.get(qualification, 0.5),
        )
    # Positive claim with unknown/unstated provenance: reported, honest-but-weak.
    return (
        EvidenceStrength.REPORTED,
        confidence if confidence is not None else _DEFAULT_SOURCE_CONFIDENCE["reported"],
    )


def evidence_grade(
    claims: Iterable[CapabilityEvidence],
    capability: str,
    qualification: Optional[Qualification] = None,
) -> Tuple[EvidenceStrength, Optional[str], Optional[float]]:
    """Strongest positive evidence for ``capability`` among ``claims``.

    Returns ``(strength, source, confidence)``. ``strength`` is
    ``EvidenceStrength.NONE`` when there is no positive claim (capability
    unknown or explicitly unsupported). Never fabricates a claim.
    """
    best_strength = EvidenceStrength.NONE
    best_source: Optional[str] = None
    best_conf: Optional[float] = None
    for claim in claims or ():
        if claim.capability != capability:
            continue
        if claim.supported is not True:
            continue
        strength, conf = _claim_strength(claim, qualification)
        if strength > best_strength:
            best_strength = strength
            best_source = claim.source
            best_conf = conf
    return (best_strength, best_source, best_conf)


def capability_support(
    claims: Iterable[CapabilityEvidence], capability: str
) -> Tuple[Optional[bool], Optional[str], Optional[float]]:
    """Tri-state capability answer from explicit claims.

    Returns ``(supported, source, confidence)`` where ``supported`` is ``True``
    or ``False`` only when a claim says so explicitly; ``None`` (unknown)
    otherwise. A negative is never implied by absence of evidence.
    """
    for claim in claims or ():
        if claim.capability != capability:
            continue
        if claim.supported is None:
            continue  # an unknown claim does not resolve the question
        return (claim.supported, claim.source, claim.confidence)
    return (None, None, None)


def positive_capability_names(record: ModelRecord) -> set[str]:
    """Advisory capability names the record positively claims."""
    return {c.capability for c in record.capabilities if c.supported is True}


def merge_curated_evidence(
    record: ModelRecord, fact=None
) -> ModelRecord:
    """Merge optional curated seed evidence into a copy of ``record``.

    Pure orchestration helper: it takes an opaque curated ``fact`` object
    (never the seed table) and adds its capability claims as ``seed`` evidence
    when the record has no stronger claim, filling presentation/metadata gaps.
    This is how seed data *augments* evidence without gating eligibility.
    """
    if fact is None:
        return record

    caps = list(record.capabilities)
    claimed = {c.capability for c in caps if c.supported is not False}
    note = "curated seed metadata (non-authoritative)"
    for cap in getattr(fact, "capabilities", None) or ():
        if cap not in claimed:
            caps.append(CapabilityEvidence(cap, True, "seed", 0.9, note))
    if getattr(fact, "supports_tools", False) and "tools" not in claimed:
        caps.append(CapabilityEvidence("tools", True, "seed", 0.9, note))
    if getattr(fact, "supports_vision", False) and "vision" not in claimed:
        caps.append(CapabilityEvidence("vision", True, "seed", 0.9, note))

    qual = record.qualification
    if qual == Qualification.EXPERIMENTAL:
        qual = getattr(fact, "qualification", qual)

    metadata = dict(record.metadata)
    if getattr(fact, "works_with_memory", False):
        metadata["works_with_memory"] = True

    return ModelRecord(
        name=record.name,
        provider=record.provider,
        runtime=record.runtime,
        status=record.status,
        identity=record.identity,
        source_kind=record.source_kind,
        source_url=record.source_url,
        format=record.format,
        size_bytes=record.size_bytes,
        parameter_count=record.parameter_count,
        context_length=record.context_length,
        license=record.license or getattr(fact, "license", None),
        capabilities=caps,
        capability_flags=dict(record.capability_flags),
        qualification=qual,
        display_name=record.display_name
        or (getattr(fact, "display_name", "") or record.name),
        approx_ram_gb=record.approx_ram_gb
        if record.approx_ram_gb is not None
        else getattr(fact, "approx_ram_gb", None),
        min_vram_gb=record.min_vram_gb
        if record.min_vram_gb is not None
        else getattr(fact, "min_vram_gb", None),
        caveats=list(record.caveats) if record.caveats
        else list(getattr(fact, "caveats", None) or ()),
        metadata=metadata,
        stale=record.stale,
    )


# ── Hardware / memory fit ──────────────────────────────────────────────────

# Conservative overhead applied to memory *estimates* before a positive fit is
# claimed. Estimates are weak evidence; a model is only "estimated to fit" when
# the detected VRAM comfortably exceeds the estimate.
ESTIMATE_OVERHEAD = 1.25


class HardwareFit(str, Enum):
    """Whether we have evidence a model fits the detected hardware.

    ``UNKNOWN`` means "not enough evidence to prove fit" — deliberately
    different from ``DOES_NOT_FIT``. Fit is never invented.
    """

    FITS = "fits"
    DOES_NOT_FIT = "does_not_fit"
    UNKNOWN = "unknown"


@dataclass
class HardwareFitDecision:
    """A hardware-fit evaluation with provenance."""

    fit: HardwareFit = HardwareFit.UNKNOWN
    # "strong" = explicit requirement/hint; "weak" = derived from size/params;
    # "unknown" = no basis for an answer.
    strength: str = "unknown"
    sources: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


# Bytes-per-parameter for common Ollama quantization names (approximation).
_QUANT_BYTES_PER_PARAM: dict[str, float] = {
    "q2_k": 0.35,
    "q3_k_m": 0.47,
    "q4_0": 0.56,
    "q4_1": 0.61,
    "q4_k_m": 0.59,
    "q5_0": 0.68,
    "q5_1": 0.74,
    "q5_k_m": 0.68,
    "q6_k": 0.80,
    "q8_0": 1.06,
    "f16": 2.0,
    "bf16": 2.0,
    "f32": 4.0,
}


def _quant_bytes_per_param(quant: Optional[str]) -> Optional[float]:
    q = (quant or "").lower().strip()
    return _QUANT_BYTES_PER_PARAM.get(q)


_PARAM_RE = re.compile(r"([\d.]+)\s*[bB]\b")


def _parameter_billions(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    match = _PARAM_RE.search(str(value))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def memory_bounds(record: ModelRecord) -> Optional[Tuple[float, float, list[str]]]:
    """Weak memory bounds from generic record metadata, or ``None``.

    Returns ``(lower_gb, upper_gb, source_labels)``. ``lower`` is the most
    conservative lower bound (disk size, or a parameter+quantization estimate
    when disk size is absent); ``upper`` is the largest known estimate. When
    neither is derivable, returns ``None`` — an estimate is never fabricated.
    """
    bounds: list[tuple[float, str]] = []
    if record.size_bytes:
        gb = record.size_bytes / (1024 ** 3)
        if gb > 0:
            bounds.append((gb, "disk size"))
    params = _parameter_billions(record.parameter_count)
    if params:
        bpp = _quant_bytes_per_param(record.identity.quantization
                                     if record.identity else None)
        if bpp:
            bounds.append((params * bpp, "parameter count + quantization"))
    if not bounds:
        return None
    lower = min(g for g, _ in bounds)
    upper = max(g for g, _ in bounds)
    labels = sorted({label for _, label in bounds})
    return (lower, upper, labels)


def _strong_fit(record: ModelRecord, hardware: HardwareProfile) -> HardwareFit:
    """Strong fit from explicit requirements/hints (never invented).

    Mirrors the strict combine: DOES_NOT_FIT wins; FITS requires BOTH a
    VRAM-side and a RAM-side known fit; otherwise UNKNOWN.
    """
    vram = hardware.gpu.vram_total_gb
    ram = hardware.ram_gb
    vram_fit = HardwareFit.UNKNOWN
    ram_fit = HardwareFit.UNKNOWN
    if record.min_vram_gb is not None and vram is not None:
        vram_fit = (
            HardwareFit.FITS if vram >= record.min_vram_gb
            else HardwareFit.DOES_NOT_FIT
        )
    if record.approx_ram_gb is not None and ram is not None:
        ram_fit = (
            HardwareFit.FITS if ram >= record.approx_ram_gb
            else HardwareFit.DOES_NOT_FIT
        )
    if (vram_fit == HardwareFit.DOES_NOT_FIT
            or ram_fit == HardwareFit.DOES_NOT_FIT):
        return HardwareFit.DOES_NOT_FIT
    if (vram_fit == HardwareFit.FITS and ram_fit == HardwareFit.FITS):
        return HardwareFit.FITS
    return HardwareFit.UNKNOWN


def _estimate_fit(record: ModelRecord, hardware: HardwareProfile) -> Tuple[HardwareFit, list[str]]:
    """Weak fit from size/parameter estimates, with a conservative margin.

    ``DOES_NOT_FIT`` only when even the lower bound exceeds VRAM (a clear,
    defensible over-budget condition); ``FITS`` only when VRAM comfortably
    exceeds the upper bound; otherwise ``UNKNOWN``.
    """
    vram = hardware.gpu.vram_total_gb
    if vram is None:
        return (HardwareFit.UNKNOWN, [])
    bounds = memory_bounds(record)
    if bounds is None:
        return (HardwareFit.UNKNOWN, [])
    lower, upper, labels = bounds
    if lower > vram:
        return (HardwareFit.DOES_NOT_FIT, labels)
    if vram >= upper * ESTIMATE_OVERHEAD:
        return (HardwareFit.FITS, labels)
    return (HardwareFit.UNKNOWN, labels)


def hardware_fit_for_record(
    record: ModelRecord, hardware: HardwareProfile
) -> HardwareFitDecision:
    """Evaluate how well ``record`` fits ``hardware``, using record evidence.

    Prefers strong/direct evidence (explicit memory requirements and curated
    hints), then falls back to weak size/parameter/quantization estimates.
    Unknown values stay unknown: absence of a requirement or of hardware facts
    is never converted into DOES_NOT_FIT or FITS without a defensible basis.
    """
    strong = _strong_fit(record, hardware)
    if strong != HardwareFit.UNKNOWN:
        sources: list[str] = []
        if record.min_vram_gb is not None:
            sources.append("curated VRAM hint")
        if record.approx_ram_gb is not None:
            sources.append("explicit memory requirement")
        return HardwareFitDecision(
            fit=strong,
            strength="strong",
            sources=sources,
            reasons=_fit_reasons(strong, "strong", sources),
        )

    weak, est_labels = _estimate_fit(record, hardware)
    if weak != HardwareFit.UNKNOWN:
        return HardwareFitDecision(
            fit=weak,
            strength="weak",
            sources=est_labels,
            reasons=_fit_reasons(weak, "weak", est_labels),
        )

    return HardwareFitDecision(
        fit=HardwareFit.UNKNOWN,
        strength="unknown",
        reasons=_fit_reasons(HardwareFit.UNKNOWN, "unknown", []),
    )


def _fit_reasons(
    fit: HardwareFit, strength: str, sources: list[str]
) -> list[str]:
    if fit == HardwareFit.FITS:
        if strength == "weak":
            return [f"Estimated to fit detected hardware (from {', '.join(sources)})"]
        return ["Fits detected hardware"]
    if fit == HardwareFit.DOES_NOT_FIT:
        if strength == "weak":
            return [f"Estimated not to fit detected hardware (from {', '.join(sources)})"]
        return ["Does not fit detected hardware"]
    return ["Hardware fit unknown — insufficient evidence"]


# ── Workload matching + scoring ────────────────────────────────────────────

@dataclass
class RecommendationScore:
    """Generic score of one record against one required capability."""

    capability: str
    strength: EvidenceStrength = EvidenceStrength.NONE
    source: Optional[str] = None
    confidence: Optional[float] = None
    fit: HardwareFitDecision = field(default_factory=HardwareFitDecision)
    installed: bool = True
    breadth: int = 0
    qualification: Qualification = Qualification.EXPERIMENTAL


def recommendation_score(
    record: ModelRecord,
    capability: str,
    hardware: HardwareProfile,
    installed: bool = True,
) -> RecommendationScore:
    """Score ``record`` as a candidate for ``capability`` on ``hardware``.

    Pure. Capability coverage, evidence strength/confidence, hardware fit and
    breadth are all derived from the record + hardware; nothing is invented.
    """
    strength, source, confidence = evidence_grade(
        record.capabilities, capability, record.qualification)
    return RecommendationScore(
        capability=capability,
        strength=strength,
        source=source,
        confidence=confidence,
        fit=hardware_fit_for_record(record, hardware),
        installed=installed,
        breadth=len(positive_capability_names(record)),
        qualification=record.qualification,
    )


def rank_components(score: RecommendationScore) -> tuple:
    """Sortable primary rank for a candidate score.

    Ordering is purely evidence-based: hardware over-budget first, then fit
    confidence, then capability evidence strength, then evidence confidence,
    then capability breadth. Name is deliberately NOT a component here — it is
    applied later as a final, deterministic tie-break only.
    """
    fit = score.fit.fit
    return (
        1 if fit == HardwareFit.DOES_NOT_FIT else 0,
        1 if fit == HardwareFit.UNKNOWN else 0,
        -int(score.strength),
        -(score.confidence or 0.0),
        -score.breadth,
    )