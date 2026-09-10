"""Model recommendation + selection.

``llm.primary_model`` is the single persisted user surface. Selection is user
intent and is written verbatim through the configuration framework — it is
never silently substituted when a model is not installed. Recommendation is
pure, advisory output: it never writes any config.

Design constraints honoured here:
* ``recommend()`` is a pure function of (hardware, installed, catalog). It
  produces one overall primary-model recommendation and evidence only.
* ``apply_selection()`` is the ONLY write path for model selection. It
  persists exactly ``llm.primary_model`` and never re-derives or falls back.
* Ranking is evidence-based: capability evidence strength, hardware fit,
  evidence confidence and breadth. Deterministic name tie-break last only.
* VRAM is never invented; a missing requirement stays unknown.
* Capabilities belong to models, not config.
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


# The persisted selection surface: one user-configured primary model.
PRIMARY_MODEL_KEY = "llm.primary_model"

# Execution strategies select behavior, never models. All strategies use the
# primary model. Kept for classification compatibility only.
STRATEGIES = ("chat", "code", "research")

# Overall recommendation weighs general capability breadth: a primary brain
# must handle conversation, tools, and reasoning. Coding/research are
# execution strategies, not hard binary capabilities.
PRIMARY_CAPABILITIES = ("chat", "tools", "reasoning", "coding")


def get_primary_model(configuration=None, config: dict | None = None) -> str:
    """Return the user's selected primary model (verbatim, may be "")."""
    if configuration is not None:
        try:
            value = configuration.get(PRIMARY_MODEL_KEY, "")
        except Exception:
            value = ""
        return value.strip() if isinstance(value, str) else ""
    cfg = config or {}
    llm = cfg.get("llm", {}) if isinstance(cfg, dict) else {}
    value = llm.get("primary_model", "") if isinstance(llm, dict) else ""
    return value.strip() if isinstance(value, str) else ""


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
class PrimaryRecommendation:
    """Advisory primary-model recommendation (derived, never persisted)."""

    model: str
    qualification: Optional[Qualification] = None
    hardware_confidence: DetectionConfidence = DetectionConfidence.UNKNOWN
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    vision_capable: bool = False
    explanation: Optional[RecommendationExplanation] = None

    def to_dict(self) -> dict:
        return {
            "model": self.model,
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
    """Pure advisory output of ``recommend()``: one primary recommendation."""

    primary: PrimaryRecommendation | None = None
    provisional: bool = False
    hardware_confidence: DetectionConfidence = DetectionConfidence.UNKNOWN

    def to_dict(self) -> dict:
        return {
            "hardwareConfidence": self.hardware_confidence.value,
            "provisional": self.provisional,
            "primary": self.primary.to_dict() if self.primary else None,
        }


def _pick_primary_model(
    hw: HardwareProfile,
    evidence: dict[str, _CandidateEvidence],
    provisional: bool = False,
) -> Optional[tuple[str, PrimaryRecommendation]]:
    """Deterministically pick best installed model overall.

    Scores each candidate across PRIMARY_CAPABILITIES and picks highest
    aggregate evidence. Returns (model_name, PrimaryRecommendation) or None.
    """
    scored = []
    for name, cand in evidence.items():
        if cand.record.qualification == Qualification.INCOMPATIBLE:
            continue
        per_cap = []
        total = 0
        best_strength = EvidenceStrength.NONE
        for capability in PRIMARY_CAPABILITIES:
            res = _candidate_rank(cand, capability, hw)
            if res is None:
                continue
            rank, score = res
            per_cap.append((capability, rank, score))
            total += 1
        if not per_cap:
            continue
        per_cap.sort(key=lambda x: x[1])
        _, _, best = per_cap[0]
        scored.append((total, per_cap[0][1], name, cand, best, per_cap))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], *x[1], x[2]))
    total, _, name, cand, score, per_cap = scored[0]
    grade = score.strength
    fit = score.fit.fit
    qual = cand.record.qualification
    reasons = [f"covers {total} primary capabilities"]
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
    if grade in (EvidenceStrength.SEED_EXPERIMENTAL, EvidenceStrength.NAME_INFERENCE) \
            or fit == HardwareFit.DOES_NOT_FIT:
        caveats.append("No trusted/supported candidate installed; using experimental model as last resort.")
    alternatives = []
    for _, _, alt_name, alt_cand, alt_score, _ in scored[1:]:
        alternatives.append({
            "model": alt_name,
            "fit": alt_score.fit.fit.value,
            "strength": _strength_label(alt_score.strength),
            "qualification": alt_score.qualification.value,
            "reasons": _alt_reasons(alt_score),
        })
    selection = PrimaryRecommendation(
        model=name,
        qualification=qual,
        hardware_confidence=hw.confidence,
        reasons=reasons,
        caveats=caveats,
        capabilities=sorted(cand.capabilities),
        vision_capable=bool("vision" in cand.capabilities),
        explanation=RecommendationExplanation(
            provenance={"source": score.source or "", "confidence": score.confidence},
            hardwareFit={"fit": score.fit.fit.value, "confidence": hw.confidence.value,
                         "strength": score.fit.strength, "basis": list(score.fit.sources)},
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
    """Compute advisory primary-model recommendation.

    Pure: accepts (hardware, installed, catalog) and returns evidence; it never
    reads or writes configuration.
    """
    if hardware is None:
        hardware = detect_hardware()
    evidence = _candidate_evidence(installed, catalog)
    recs = Recommendations(hardware_confidence=hardware.confidence)
    if hardware.confidence in (DetectionConfidence.LOW, DetectionConfidence.UNKNOWN):
        recs.provisional = True
    result = _pick_primary_model(hardware, evidence, provisional=recs.provisional)
    if result is None:
        recs.primary = PrimaryRecommendation(
            model="",
            hardware_confidence=hardware.confidence,
            reasons=["no installed candidate"],
        )
    else:
        _, recs.primary = result
    return recs


def _normalize_model_value(value) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def apply_selection(configuration, model: str = "", installed=None, by: str = "user") -> dict:
    """Persist the user's primary model selection.

    The ONLY write path for model selection. Writes exactly
    ``llm.primary_model`` verbatim.
    """
    model = _normalize_model_value(model)
    configuration.set(PRIMARY_MODEL_KEY, model, by=by)
    installed_names = _installed_names(installed)
    if not model:
        status = "unset"
    elif installed is not None and model not in installed_names:
        status = "not-installed"
    elif installed is not None:
        status = "installed"
    else:
        status = "configured"
    return {"model": model, "status": status, "primary": {"model": model, "status": status}}