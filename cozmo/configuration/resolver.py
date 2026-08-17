"""Model recommendation + selection.

Workloads are the persisted user surface: ``llm.workloads.{general,research,
code}.model``. Selection is user intent and is written verbatim through the
configuration framework — it is never silently substituted when a model is not
installed. Recommendation is pure, advisory output: it never writes any config.

Design constraints honoured here:
* ``recommend()`` is a pure function of (hardware, installed, catalog). It
  produces per-workload advice and evidence only; it never mutates state.
* ``apply_selection()`` is the ONLY write path for workload selection. It
  persists exactly ``llm.workloads.*`` and never re-derives or falls back.
* Ranking is evidence-based (Phase 5.5): capability evidence strength, hardware
  fit, evidence confidence and breadth. An unseeded model with real runtime
  capability evidence is ranked on that evidence — not penalized for lacking
  seed membership. A deterministic name tie-break is used ONLY as a final,
  stable ordering mechanism, never as a quality signal.
* VRAM is never invented; a missing requirement stays unknown. Curated VRAM
  caveats are respected via the ``min_vram_gb`` hint.
* Capabilities belong to models, not config: a recommendation carries the
  model's derived capabilities (incl. vision) as evidence only.
* Capability evidence may come from curated seed facts, runtime-reported
  capabilities, or weak name inference. Unknown models with real evidence
  participate; pure name-inference-only models rank last among candidates.
* The catalog is NOT authoritative and does NOT define the model universe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .hardware import DetectionConfidence, HardwareProfile, detect_hardware
from .model_records import CapabilityEvidence, ModelRecord, ModelStatus
from .model_seeds import ModelFact, SEED_MODEL_FACTS
from .qualification import Qualification
from .recommendation import (
    EvidenceStrength,
    HardwareFit,
    hardware_fit_for_record,
    merge_curated_evidence,
    rank_components,
    recommendation_score,
)


# The persisted selection surface. A workload is a model selection slot; the
# selected model's capabilities are derived from the model itself.
WORKLOADS = ["general", "research", "code"]

# Capability each workload is recommended against (capabilities belong to
# models; this only drives which installed model fits best).
WORKLOAD_CAPABILITY = {
    "general": "chat",
    "research": "reasoning",
    "code": "coding",
}


def _installed_names(installed) -> set[str]:
    names = set()
    for m in (installed or []):
        name = m.name if hasattr(m, "name") else m
        if name:
            names.add(name)
    return names


@dataclass
class _CandidateEvidence:
    """Advisory capability evidence for one installed candidate.

    The candidate is a generic ``ModelRecord`` (whatever source produced it);
    recommendation decisions are made from its evidence, never from seed
    membership or model name.
    """

    name: str
    record: ModelRecord

    @property
    def capabilities(self) -> set[str]:
        return {c.capability for c in self.record.capabilities if c.supported is True}

    def score(self, capability: str, hw: HardwareProfile):
        return recommendation_score(self.record, capability, hw, installed=True)


def _build_record(
    name: str,
    claims: list[CapabilityEvidence],
    fact: Optional[ModelFact],
    base: Optional[ModelRecord],
) -> ModelRecord:
    """Assemble a generic candidate record, merging optional seed evidence."""
    if base is None:
        base = ModelRecord(name=name, status=ModelStatus.INSTALLED)
    if not base.capabilities and claims:
        base = ModelRecord(
            name=base.name, provider=base.provider, runtime=base.runtime,
            status=base.status, identity=base.identity,
            source_kind=base.source_kind, source_url=base.source_url,
            format=base.format, size_bytes=base.size_bytes,
            parameter_count=base.parameter_count,
            context_length=base.context_length, license=base.license,
            capabilities=claims, capability_flags=dict(base.capability_flags),
            qualification=base.qualification, display_name=base.display_name,
            approx_ram_gb=base.approx_ram_gb, min_vram_gb=base.min_vram_gb,
            caveats=list(base.caveats), metadata=dict(base.metadata),
            stale=base.stale,
        )
    return merge_curated_evidence(base, fact)


def _candidate_evidence(installed, catalog: Optional[dict]) -> dict[str, _CandidateEvidence]:
    """Build per-installed-model capability evidence.

    ``catalog``: explicit {name: ModelFact} override (tests) or ``None`` to
    use the curated seed facts as *advisory enrichment only*.
    """
    facts = catalog if catalog is not None else SEED_MODEL_FACTS
    out: dict[str, _CandidateEvidence] = {}
    for m in (installed or []):
        if isinstance(m, dict):
            name = m.get("name")
            names = {c for c in (m.get("capability_names") or ()) if isinstance(c, str)}
            claims = [CapabilityEvidence(c, True, "reported", None) for c in names]
            base = None
        elif hasattr(m, "name"):
            name = m.name
            base = m if isinstance(m, ModelRecord) else None
            claims = list(getattr(m, "capabilities", None) or ())
            if not claims:
                names = getattr(m, "capability_names", None)
                names = names() if callable(names) else (names or ())
                claims = [CapabilityEvidence(c, True, "reported", None) for c in names]
        else:
            name = m
            claims = []
            base = None
        if not isinstance(name, str) or not name:
            continue
        if name in out:
            continue
        fact = facts.get(name) if isinstance(facts, dict) else None
        out[name] = _CandidateEvidence(
            name=name, record=_build_record(name, claims, fact, base))
    return out


def _candidate_rank(
    cand: _CandidateEvidence, capability: str, hw: HardwareProfile,
) -> Optional[tuple]:
    """Return sortable primary rank for an installed candidate, or None.

    Ranking is purely evidence-based: capability coverage first (a candidate
    with no positive evidence for ``capability`` is not a candidate), then
    hardware fit, then capability evidence strength/confidence, then breadth.
    A curated INCOMPATIBLE grade is the only hard exclusion. A deterministic
    name tie-break is applied at sort time only, never as a quality signal.
    """
    if cand.record.qualification == Qualification.INCOMPATIBLE:
        return None
    score = cand.score(capability, hw)
    if score.strength == EvidenceStrength.NONE:
        return None
    return (rank_components(score), score)


_STRENGTH_LABELS = {
    EvidenceStrength.RUNTIME: "runtime",
    EvidenceStrength.SEED_TRUSTED: "trusted-seed",
    EvidenceStrength.SEED_SUPPORTED: "supported-seed",
    EvidenceStrength.REPORTED: "reported",
    EvidenceStrength.SEED_EXPERIMENTAL: "experimental-seed",
    EvidenceStrength.NAME_INFERENCE: "name-inference",
    EvidenceStrength.NONE: "none",
}


def _strength_label(strength: EvidenceStrength) -> str:
    return _STRENGTH_LABELS.get(strength, "none")


def _alt_reasons(score: RecommendationScore) -> list[str]:
    """Canonical short reasons for a non-winning candidate."""
    reasons = []
    if score.strength == EvidenceStrength.RUNTIME:
        reasons.append("runtime capability evidence")
    elif score.strength == EvidenceStrength.SEED_TRUSTED:
        reasons.append("trusted curated evidence")
    elif score.strength == EvidenceStrength.SEED_SUPPORTED:
        reasons.append("curated evidence")
    elif score.strength == EvidenceStrength.REPORTED:
        reasons.append("reported capability evidence")
    elif score.strength == EvidenceStrength.SEED_EXPERIMENTAL:
        reasons.append("experimental / unverified")
    elif score.strength == EvidenceStrength.NAME_INFERENCE:
        reasons.append("weak name-based evidence")
    if score.fit.fit == HardwareFit.FITS:
        reasons.append("fits detected hardware")
    elif score.fit.fit == HardwareFit.DOES_NOT_FIT:
        reasons.append("does not fit detected hardware")
    elif score.fit.fit == HardwareFit.UNKNOWN:
        reasons.append("hardware fit unknown")
    return reasons


@dataclass
class RecommendationExplanation:
    """Structured, additive explanation for one recommendation.

    Derived evidence describing why ``model`` won for the workload: the
    winning capability claim's provenance, the hardware-fit basis, the viable
    alternatives that were considered, and whether the advice is provisional.
    Like the recommendation itself it is advisory and never persisted.
    """

    provenance: Optional[dict] = None
    hardwareFit: Optional[dict] = None
    alternatives: list[dict] = field(default_factory=list)
    provisional: bool = False

    def to_dict(self) -> dict:
        return {
            "provenance": self.provenance,
            "hardwareFit": self.hardwareFit,
            "alternatives": self.alternatives,
            "provisional": self.provisional,
        }


@dataclass
class WorkloadRecommendation:
    """Advisory per-workload recommendation (derived, never persisted)."""

    workload: str
    model: str
    capability: str = ""
    qualification: Optional[Qualification] = None
    hardware_confidence: DetectionConfidence = DetectionConfidence.UNKNOWN
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    vision_capable: bool = False
    explanation: Optional[RecommendationExplanation] = None

    def to_dict(self) -> dict:
        return {
            "workload": self.workload,
            "model": self.model,
            "capability": self.capability,
            "qualification": self.qualification.value if self.qualification else "",
            "hardwareConfidence": self.hardware_confidence.value,
            "reasons": self.reasons,
            "caveats": self.caveats,
            "capabilities": self.capabilities,
            "visionCapable": self.vision_capable,
            "explanation": self.explanation.to_dict() if self.explanation else None,
        }


@dataclass
class Recommendations:
    """Pure advisory output of ``recommend()``."""

    workloads: dict[str, WorkloadRecommendation] = field(default_factory=dict)
    provisional: bool = False
    hardware_confidence: DetectionConfidence = DetectionConfidence.UNKNOWN

    def to_dict(self) -> dict:
        return {
            "hardwareConfidence": self.hardware_confidence.value,
            "provisional": self.provisional,
            "workloads": {w: r.to_dict() for w, r in self.workloads.items()},
        }


def _pick_workload_model(
    workload: str, capability: str, hw: HardwareProfile,
    evidence: dict[str, _CandidateEvidence], seen: set[str],
    provisional: bool = False,
) -> Optional[tuple[str, WorkloadRecommendation]]:
    """Deterministically pick the best installed model for ``capability``.

    Returns (model_name, WorkloadRecommendation) or None if no usable model.
    ``seen`` tracks models already recommended; a not-yet-used model with an
    identical rank is preferred (better coverage), but reuse-avoidance never
    outranks VRAM/evidence/qualification/breadth constraints.
    """
    ranked = []
    for name, cand in evidence.items():
        res = _candidate_rank(cand, capability, hw)
        if res is not None:
            ranked.append((res[0], name, cand, res[1]))
    ranked.sort(key=lambda x: (*x[0], x[1] in seen, x[1]))

    if not ranked:
        return None

    _, name, cand, score = ranked[0]
    grade = score.strength
    fit = score.fit.fit
    qual = cand.record.qualification
    reasons = [f"capability '{capability}'"]
    if grade == EvidenceStrength.RUNTIME:
        reasons.append("runtime reported capability")
    elif grade == EvidenceStrength.SEED_TRUSTED:
        reasons.append("trusted seed evidence")
    elif grade == EvidenceStrength.SEED_SUPPORTED:
        reasons.append("supported seed evidence")
    elif grade == EvidenceStrength.REPORTED:
        reasons.append("reported capability evidence")
    elif grade == EvidenceStrength.SEED_EXPERIMENTAL:
        reasons.append("experimental / unverified (last resort)")
    elif grade == EvidenceStrength.NAME_INFERENCE:
        reasons.append("weak name-based capability evidence only (last resort)")
    if fit == HardwareFit.FITS:
        reasons.append("fits detected hardware")
    elif fit == HardwareFit.DOES_NOT_FIT:
        reasons.append("does not fit detected hardware (last resort)")
    elif fit == HardwareFit.UNKNOWN:
        reasons.append("hardware fit unknown")
    caveats = list(cand.record.caveats)
    low_confidence = grade in (EvidenceStrength.SEED_EXPERIMENTAL,
                               EvidenceStrength.NAME_INFERENCE) \
        or fit == HardwareFit.DOES_NOT_FIT
    if low_confidence:
        caveats.append("No trusted/supported candidate installed; using "
                       "experimental model as last resort.")

    alternatives = []
    for _, alt_name, alt_cand, alt_score in ranked[1:]:
        alternatives.append({
            "model": alt_name,
            "fit": alt_score.fit.fit.value,
            "strength": _strength_label(alt_score.strength),
            "capability": capability,
            "qualification": alt_score.qualification.value,
            "reasons": _alt_reasons(alt_score),
        })

    selection = WorkloadRecommendation(
        workload=workload,
        model=name,
        capability=capability,
        qualification=qual,
        hardware_confidence=hw.confidence,
        reasons=reasons,
        caveats=caveats,
        capabilities=sorted(cand.capabilities),
        vision_capable=bool("vision" in cand.capabilities),
        explanation=RecommendationExplanation(
            provenance={
                "source": score.source or "",
                "confidence": score.confidence,
            },
            hardwareFit={
                "fit": score.fit.fit.value,
                "confidence": hw.confidence.value,
                "strength": score.fit.strength,
                "basis": list(score.fit.sources),
            },
            alternatives=alternatives,
            provisional=provisional,
        ),
    )
    return name, selection


def recommend(
    hardware: Optional[HardwareProfile] = None,
    installed=None,
    catalog: Optional[dict] = None,
) -> Recommendations:
    """Compute advisory per-workload model recommendations.

    Pure: accepts (hardware, installed, catalog) and returns evidence; it never
    reads or writes configuration.

    ``installed``: iterable of model names (strings) or ModelRecord/DiscoveredModel
    objects (their ``.name`` and capability evidence are used). ``catalog``
    defaults to the curated seed facts and is advisory only.
    """
    if hardware is None:
        hardware = detect_hardware()

    evidence = _candidate_evidence(installed, catalog)

    recs = Recommendations(hardware_confidence=hardware.confidence)

    if hardware.confidence in (DetectionConfidence.LOW,
                               DetectionConfidence.UNKNOWN):
        recs.provisional = True

    seen: set[str] = set()
    for workload, capability in WORKLOAD_CAPABILITY.items():
        result = _pick_workload_model(workload, capability, hardware,
                                      evidence, seen, provisional=recs.provisional)
        if result is None:
            # No usable candidate: recommend nothing, never fabricate.
            recs.workloads[workload] = WorkloadRecommendation(
                workload=workload,
                model="",
                capability=capability,
                hardware_confidence=hardware.confidence,
                reasons=["no installed candidate for capability"],
            )
            continue
        name, selection = result
        seen.add(name)
        recs.workloads[workload] = selection

    return recs


def apply_selection(
    configuration, workloads, installed=None, by: str = "user",
) -> dict:
    """Persist the user's workload selection through the configuration framework.

    The ONLY write path for model selection. Writes exactly
    ``llm.workloads.<workload>.model`` for each workload. Values are persisted
    verbatim — a model that is not installed is kept, reported as
    ``not-installed``, and never silently substituted.

    ``workloads``: {workload: model-name} mapping. ``installed``: optional
    iterable of installed model names (or ModelRecord objects) used only to
    compute the ``installed``/``not-installed``/``unset`` status labels.

    Returns {"workloads": {workload: {"model": ..., "status": ...}}}.
    """
    installed_names = _installed_names(installed)
    result = {}
    for workload in WORKLOADS:
        model = workloads.get(workload) if isinstance(workloads, dict) else ""
        if not isinstance(model, str):
            model = ""
        model = model.strip()
        configuration.set(f"llm.workloads.{workload}.model", model, by=by)
        if not model:
            status = "unset"
        elif installed is not None and model not in installed_names:
            status = "not-installed"
        elif installed is not None:
            status = "installed"
        else:
            status = "configured"
        result[workload] = {"model": model, "status": status}
    return {"workloads": result}