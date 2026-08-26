"""Model eligibility / evidence layer.

Combines, for one model, the currently known environment:

    hardware + qualification + capabilities + installation status

into an explicit eligibility/evidence result. It only answers: "what is this
model a candidate for, what evidence supports it, and what prevents it from
being a candidate?" It never selects anything.

Eligibility is evidence-based (Phase 5.5): it consumes a
:class:`~novi.configuration.model_records.ModelRecord` and its provenance-rich
capability evidence. An unseeded model with real runtime capability evidence is
evaluated on that evidence — absence of curated seed metadata is never treated
as proof of incompatibility.

Cardinal rules:
* Hardware fit is never invented: an unknown requirement or unknown VRAM yields
  ``unknown``, which is distinct from ``does_not_fit``. See ``HardwareFit``.
* Unknown is NOT treated as does_not_fit.
* Qualification is independent of hardware and installation status.
* Eligibility is derived state — recomputed from current hardware + installed
  models + seed facts, never persisted to config.toml.
* No Automatic/Custom model mode. Eligibility reports hardware fit,
  hardware confidence, qualification, and capability matches only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .discovery import ModelStatus
from .hardware import (  # noqa: F401 (re-exported for consumers)
    DetectionConfidence,
    HardwareProfile,
    detect_hardware,
)
from .model_records import ModelRecord
from .model_seeds import ModelFact, SEED_MODEL_FACTS
from .qualification import Qualification
from .recommendation import (
    HardwareFit,  # noqa: F401 (re-exported for consumers)
    capability_support,
    hardware_fit_for_record,
    merge_curated_evidence,
    positive_capability_names,
)


class CapabilityMatch(str, Enum):
    """Result of asking whether a model provides a requested capability."""

    MATCHES = "matches"
    NO_MATCH = "no_match"
    UNKNOWN = "unknown"


@dataclass
class CapabilityMatchEvidence:
    """Result of matching model capability evidence against a request.

    Preserves the distinction between curated seed evidence and coarse
    runtime/discovery inference; the requested capability is matched against
    whatever real evidence exists without inventing a result.
    """

    capability: str
    match: CapabilityMatch
    source: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "capability": self.capability,
            "match": self.match.value,
            "source": self.source,
            "reason": self.reason,
        }


@dataclass
class ModelEligibility:
    """Eligibility/evidence for a single model in the current environment."""

    name: str
    installation_status: ModelStatus
    qualification: Qualification
    capabilities: list[str] = field(default_factory=list)
    hardware_fit: HardwareFit = HardwareFit.UNKNOWN
    hardware_confidence: DetectionConfidence = DetectionConfidence.UNKNOWN
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    capability_matches: list[CapabilityMatchEvidence] = field(default_factory=list)
    fact: Optional[ModelFact] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "installationStatus": self.installation_status.value,
            "qualification": self.qualification.value,
            "capabilities": self.capabilities,
            "hardwareFit": self.hardware_fit.value,
            "hardwareConfidence": self.hardware_confidence.value,
            "reasons": self.reasons,
            "caveats": self.caveats,
            "capabilityMatches": [c.to_dict() for c in self.capability_matches]
            if self.capability_matches else None,
        }


def _vram_fit(fact: Optional[ModelFact], hardware: HardwareProfile) -> HardwareFit:
    """VRAM-based fit using only real, known values (never invented).

    - requirement known AND VRAM known → compare.
    - either side unknown → ``unknown`` (never does_not_fit, never fabricated).
    """
    if fact is None or fact.vram_required_gb is None:
        return HardwareFit.UNKNOWN
    vram = hardware.gpu.vram_total_gb
    if vram is None:
        return HardwareFit.UNKNOWN
    if vram >= fact.vram_required_gb:
        return HardwareFit.FITS
    return HardwareFit.DOES_NOT_FIT


def _ram_fit(fact: Optional[ModelFact], hardware: HardwareProfile) -> HardwareFit:
    """RAM-based fit as secondary evidence (real values only)."""
    if fact is None:
        return HardwareFit.UNKNOWN
    ram = hardware.ram_gb
    if ram is None:
        return HardwareFit.UNKNOWN
    if ram >= fact.approx_ram_gb:
        return HardwareFit.FITS
    return HardwareFit.DOES_NOT_FIT


def _combine_fit(vram: HardwareFit, ram: HardwareFit) -> HardwareFit:
    """Combine VRAM + RAM fit. DOES_NOT_FIT wins; otherwise UNKNOWN unless both
    known-fits. Today no model has a VRAM requirement, so this mostly yields
    UNKNOWN (honest) or RAM-derived DOES_NOT_FIT (a known RAM shortfall)."""
    if vram == HardwareFit.DOES_NOT_FIT or ram == HardwareFit.DOES_NOT_FIT:
        return HardwareFit.DOES_NOT_FIT
    if vram == HardwareFit.FITS and ram == HardwareFit.FITS:
        return HardwareFit.FITS
    return HardwareFit.UNKNOWN


def hardware_fit_for(
    fact: Optional[ModelFact], hardware: HardwareProfile
) -> HardwareFit:
    """Public helper: single source of truth for hardware fit of a model fact.

    Used by both the eligibility evaluator and the recommendation engine so they
    never disagree about fit. Never invents a requirement or a VRAM number.
    """
    vram_fit = _vram_fit(fact, hardware)
    ram_fit = _ram_fit(fact, hardware)
    return _combine_fit(vram_fit, ram_fit)


def evaluate_eligibility(
    name: str = "",
    installed_status: ModelStatus = ModelStatus.INSTALLED,
    hardware: Optional[HardwareProfile] = None,
    discovered_capabilities: Optional[dict[str, bool]] = None,
    requested_capabilities: Optional[list[str]] = None,
    fact: Optional[ModelFact] = None,
    record: Optional[ModelRecord] = None,
) -> ModelEligibility:
    """Evaluate one model against the currently known environment.

    ``record``: preferred input (Phase 5.5). When given, eligibility is derived
    from the record's provenance-rich capability evidence and generic hardware
    fit; a curated seed ``fact`` (looked up by name when not passed) only
    augments that evidence. An unseeded record with real runtime capability
    evidence is evaluated on that evidence, never dismissed for lacking a seed
    fact.

    Without a record, the legacy path applies: ``fact`` defaults to
    ``SEED_MODEL_FACTS.get(name)``; pass one explicitly to decouple from the
    shared seed table (tests). ``discovered_capabilities`` are the capability
    flags from model discovery (runtime + inference).
    """
    if hardware is None:
        hardware = detect_hardware()

    if record is not None:
        return _evaluate_record(
            record=record,
            installed_status=installed_status,
            hardware=hardware,
            requested_capabilities=requested_capabilities,
            fact=fact,
        )

    if fact is None:
        fact = SEED_MODEL_FACTS.get(name)

    qual = fact.qualification if fact is not None else Qualification.EXPERIMENTAL
    capabilities = list(fact.capabilities) if fact is not None else []

    hardware_fit = hardware_fit_for(fact, hardware)
    installed = installed_status == ModelStatus.INSTALLED

    reasons: list[str] = []
    caveats: list[str] = []
    if fact is None:
        if discovered_capabilities and any(
            v is True for v in discovered_capabilities.values()
        ):
            reasons.append("No curated seed metadata; capability evidence from discovery")
        else:
            reasons.append("No curated seed fact — unqualified / experimental")
    else:
        reasons.append(f"Quality: {qual.value}")
        if fact.works_with_memory:
            reasons.append("Works with Memory")
        if fact.supports_tools:
            reasons.append("Supports Tool Calling")
        caveats = list(fact.caveats)

    if not installed:
        reasons.append("Not installed")
    if hardware_fit == HardwareFit.DOES_NOT_FIT:
        reasons.append("Does not fit detected hardware")
    elif hardware_fit == HardwareFit.FITS:
        reasons.append("Fits detected hardware")
    else:
        reasons.append("Hardware fit unknown (not enough evidence)")
    if qual == Qualification.INCOMPATIBLE:
        caveats.append("Marked incompatible; not recommended")

    # Capability evaluation: preserve seed vs discovery distinction. Match a
    # requested capability against curated + inferred evidence; never fabricate.
    capability_matches: list[CapabilityMatchEvidence] = []
    for req in requested_capabilities or []:
        curated = bool(fact) and req in fact.capabilities
        inferred = bool(discovered_capabilities) and discovered_capabilities.get(req, False)
        if curated:
            capability_matches.append(CapabilityMatchEvidence(
                req, CapabilityMatch.MATCHES, "catalog",
                "curated catalog capability"))
        elif req in (discovered_capabilities or {}):
            capability_matches.append(CapabilityMatchEvidence(
                req, CapabilityMatch.MATCHES if inferred else CapabilityMatch.NO_MATCH,
                "discovery", "coarse inference from model name"))
        else:
            capability_matches.append(CapabilityMatchEvidence(
                req, CapabilityMatch.UNKNOWN, "",
                "no catalog or discovery evidence for this capability"))

    return ModelEligibility(
        name=name,
        installation_status=installed_status,
        qualification=qual,
        capabilities=capabilities,
        hardware_fit=hardware_fit,
        hardware_confidence=hardware.confidence,
        reasons=reasons,
        caveats=caveats,
        capability_matches=capability_matches,
        fact=fact,
    )


def _evaluate_record(
    *,
    record: ModelRecord,
    installed_status: ModelStatus,
    hardware: HardwareProfile,
    requested_capabilities: Optional[list[str]],
    fact: Optional[ModelFact],
) -> ModelEligibility:
    """Evidence-based eligibility from a ModelRecord (Phase 5.5)."""
    if fact is None:
        fact = SEED_MODEL_FACTS.get(record.name)
    record = merge_curated_evidence(record, fact)

    qual = record.qualification
    capabilities = sorted(positive_capability_names(record))
    fit = hardware_fit_for_record(record, hardware).fit
    installed = installed_status == ModelStatus.INSTALLED

    reasons: list[str] = []
    caveats: list[str] = []
    if fact is not None:
        reasons.append(f"Quality: {qual.value}")
        if record.metadata.get("works_with_memory"):
            reasons.append("Works with Memory")
        if "tools" in capabilities:
            reasons.append("Supports Tool Calling")
        caveats = list(record.caveats)
    else:
        if capabilities:
            reasons.append("Capability evidence from discovery/runtime")
        else:
            reasons.append("No capability evidence")

    if not installed:
        reasons.append("Not installed")
    if fit == HardwareFit.DOES_NOT_FIT:
        reasons.append("Does not fit detected hardware")
    elif fit == HardwareFit.FITS:
        reasons.append("Fits detected hardware")
    else:
        reasons.append("Hardware fit unknown (not enough evidence)")
    if qual == Qualification.INCOMPATIBLE:
        caveats.append("Marked incompatible; not recommended")

    capability_matches: list[CapabilityMatchEvidence] = []
    for req in requested_capabilities or []:
        supported, source, _conf = capability_support(record.capabilities, req)
        if supported is True:
            capability_matches.append(CapabilityMatchEvidence(
                req, CapabilityMatch.MATCHES, source or "runtime",
                "capability evidence"))
        elif supported is False:
            capability_matches.append(CapabilityMatchEvidence(
                req, CapabilityMatch.NO_MATCH, source or "discovery",
                "reported unsupported"))
        else:
            capability_matches.append(CapabilityMatchEvidence(
                req, CapabilityMatch.UNKNOWN, "",
                "no evidence for this capability"))

    return ModelEligibility(
        name=record.name,
        installation_status=installed_status,
        qualification=qual,
        capabilities=capabilities,
        hardware_fit=fit,
        hardware_confidence=hardware.confidence,
        reasons=reasons,
        caveats=caveats,
        capability_matches=capability_matches,
        fact=fact,
    )