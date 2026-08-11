"""Model eligibility / evidence layer (M2.3).

Combines, for one model, the currently known environment:

    hardware (M2.1) + qualification (M2.2) + capabilities + installation status

into an explicit eligibility/evidence result the future ``ResolutionLayer``
can consume. This checkpoint deliberately does NOT decide which model Automatic
chooses — it only answers: "what is this model a candidate for, what evidence
supports it, and what prevents it from being a candidate?"

Cardinal rules:
* Hardware fit is never invented: an unknown requirement or unknown VRAM yields
  ``unknown``, which is distinct from ``does_not_fit``. See ``HardwareFit``.
* Unknown is NOT treated as does_not_fit.
* Qualification is independent of hardware and installation status.
* Eligibility is derived state — recomputed from current hardware + installed
  models + catalog, never persisted to config.toml.
* Experimental is NOT proactively Automatic-eligible (last-resort fallback is a
  later concern; we only expose the info the resolver needs). Incompatible is
  never Automatic-eligible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .catalog import KNOWN_MODEL_FACTS, ModelFact
from .discovery import ModelStatus
from .hardware import (  # noqa: F401 (re-exported for consumers)
    DetectionConfidence,
    HardwareProfile,
    detect_hardware,
)
from .qualification import Qualification


class HardwareFit(str, Enum):
    """Whether we have positive evidence a model fits the detected hardware.

    ``UNKNOWN`` means "we don't have enough hardware information to prove it
    fits" — deliberately different from ``DOES_NOT_FIT`` ("we know it doesn't").
    """

    FITS = "fits"
    DOES_NOT_FIT = "does_not_fit"
    UNKNOWN = "unknown"


class CapabilityMatch(str, Enum):
    """Result of asking whether a model provides a requested capability."""

    MATCHES = "matches"
    NO_MATCH = "no_match"
    UNKNOWN = "unknown"


@dataclass
class CapabilityEvidence:
    """Result of matching catalog/discovery capability evidence against a request.

    Preserves the distinction between curated catalog evidence and coarse
    discovery inference; the requested capability is matched against whatever
    real evidence exists without inventing a result.
    """

    capability: str
    match: CapabilityMatch
    source: str = ""
    reason: str = ""


@dataclass
class ModelEligibility:
    """Eligibility/evidence for a single model in the current environment."""

    name: str
    installation_status: ModelStatus
    qualification: Qualification
    capabilities: list[str] = field(default_factory=list)
    hardware_fit: HardwareFit = HardwareFit.UNKNOWN
    hardware_confidence: DetectionConfidence = DetectionConfidence.UNKNOWN
    eligible_automatic: bool = False
    eligible_custom: bool = False
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    capability_matches: list[CapabilityEvidence] = field(default_factory=list)
    fact: Optional[ModelFact] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "installationStatus": self.installation_status.value,
            "qualification": self.qualification.value,
            "capabilities": self.capabilities,
            "hardwareFit": self.hardware_fit.value,
            "hardwareConfidence": self.hardware_confidence.value,
            "eligibleAutomatic": self.eligible_automatic,
            "eligibleCustom": self.eligible_custom,
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
    UNKNOWN (honest) or RAM-derived DOes_NOT_FIT (a known RAM shortfall)."""
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
    name: str,
    installed_status: ModelStatus = ModelStatus.INSTALLED,
    hardware: Optional[HardwareProfile] = None,
    discovered_capabilities: Optional[dict[str, bool]] = None,
    requested_capabilities: Optional[list[str]] = None,
    fact: Optional[ModelFact] = None,
) -> ModelEligibility:
    """Evaluate one model against the currently known environment.

    ``fact`` defaults to ``KNOWN_MODEL_FACTS.get(name)``; pass one explicitly
    to decouple from the shared catalog (tests). ``discovered_capabilities`` are
    the coarse inference flags from model discovery.
    """
    if fact is None:
        fact = KNOWN_MODEL_FACTS.get(name)
    if hardware is None:
        hardware = detect_hardware()

    qual = fact.qualification if fact is not None else Qualification.EXPERIMENTAL
    capabilities = list(fact.capabilities) if fact is not None else []

    hardware_fit = hardware_fit_for(fact, hardware)

    installed = installed_status == ModelStatus.INSTALLED

    # Automatic eligibility: installed + qualification-selectable +
    # not-known-not-to-fit. UNKNOWN fit does NOT block.
    eligible_automatic = (
        installed
        and qual.automatically_selectable
        and hardware_fit != HardwareFit.DOES_NOT_FIT
    )

    # Custom eligibility: usable if installed (warn on incompatible).
    eligible_custom = installed

    reasons: list[str] = []
    caveats: list[str] = []
    if fact is None:
        reasons.append("No curated catalog fact — unqualified / experimental")
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
        caveats.append("Marked incompatible; not recommended for Automatic use")

    # Capability evaluation: preserve catalog vs discovery distinction. Match a
    # requested capability against curated + inferred evidence; never fabricate.
    capability_matches: list[CapabilityEvidence] = []
    for req in requested_capabilities or []:
        curated = bool(fact) and req in fact.capabilities
        inferred = bool(discovered_capabilities) and discovered_capabilities.get(req, False)
        if curated:
            capability_matches.append(CapabilityEvidence(
                req, CapabilityMatch.MATCHES, "catalog",
                "curated catalog capability"))
        elif req in (discovered_capabilities or {}):
            capability_matches.append(CapabilityEvidence(
                req, CapabilityMatch.MATCHES if inferred else CapabilityMatch.NO_MATCH,
                "discovery", "coarse inference from model name"))
        else:
            capability_matches.append(CapabilityEvidence(
                req, CapabilityMatch.UNKNOWN, "",
                "no catalog or discovery evidence for this capability"))

    return ModelEligibility(
        name=name,
        installation_status=installed_status,
        qualification=qual,
        capabilities=capabilities,
        hardware_fit=hardware_fit,
        hardware_confidence=hardware.confidence,
        eligible_automatic=eligible_automatic,
        eligible_custom=eligible_custom,
        reasons=reasons,
        caveats=caveats,
        capability_matches=capability_matches,
        fact=fact,
    )


def evaluate_all(
    installed_models,
    hardware: Optional[HardwareProfile] = None,
) -> list[ModelEligibility]:
    """Evaluate a sequence of discovered models (objects exposing ``name``,
    ``status``, and optionally ``capability_flags``)."""
    if hardware is None:
        hardware = detect_hardware()
    out = []
    for m in installed_models:
        status = m.status if hasattr(m, "status") else ModelStatus.INSTALLED
        caps = getattr(m, "capability_flags", None)
        out.append(evaluate_eligibility(
            m.name, installed_status=status, hardware=hardware,
            discovered_capabilities=caps))
    return out
