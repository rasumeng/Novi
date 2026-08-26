"""Discovery payload assembly + compatibility views (Phase 5.5).

This module is the *orchestration/compatibility* layer. The recommendation
intelligence lives in ``recommendation.py`` (generic, evidence-based, operating
on ``ModelRecord`` + ``HardwareProfile``). Curated seed data
(``SEED_MODEL_FACTS``) is advisory evidence only: it may enrich a record or
supply optional seed-only advisory candidates, but it never defines the model
universe and is never the recommendation decision.

Candidate sources are distinct and pluggable:

1. installed runtime records   — authoritative evidence of locally installed
   models (from discovery).
2. user-referenced missing     — models referenced by configuration but not
   installed (surfaced separately, never recommended-for-install silently).
3. seed-only advisory records  — optional curated suggestions for models that
   are not currently discovered; built here and run through the same generic
   engine as everything else.
4. future remote records       — not implemented in Phase 5.5; the engine
   accepts any ``ModelRecord``, so a future source drops in without touching
   ``recommendation.py``.

The engine never knows which source produced a record.

Recommendations are advisory only and never write configuration — the sole
selection write path is ``resolver.apply_selection``.
"""

from __future__ import annotations

from typing import Optional

from .hardware import (
    HardwareProfile,
    detect_hardware,
)
from .model_records import (
    CapabilityEvidence,
    ModelIdentity,
    ModelRecord,
    ModelStatus,
)
from .model_seeds import ModelFact, SEED_MODEL_FACTS
from .qualification import Qualification
from .recommendation import (
    EvidenceStrength,
    HardwareFit,
    capability_support,
    evidence_grade,
    hardware_fit_for_record,
    merge_curated_evidence,
    positive_capability_names,
)


# Capabilities a user can pick / that get recommended for the user-facing
# roles. Anything outside this set (notably ``embeddings``) is internal and must
# never surface as a setup item, recommendation, or install target.
USER_FACING_CAPABILITIES = frozenset(("chat", "reasoning", "coding", "vision"))


def _source_label(source: Optional[str]) -> str:
    return {
        "runtime": "runtime reported",
        "seed": "seed/curated evidence",
        "name-inference": "weak name-based evidence",
        "reported": "reported evidence",
    }.get(source or "", "reported evidence")


def _enrich_record(record: ModelRecord) -> ModelRecord:
    """Merge optional curated seed evidence into a record (orchestration).

    Seed data may *augment* a record's evidence; it never gates anything. An
    unseeded record passes through unchanged and is evaluated identically.
    """
    fact = SEED_MODEL_FACTS.get(record.name)
    return merge_curated_evidence(record, fact)


class ModelRecommendationEngine:
    """Produces recommendation records for discovered models.

    Pure advisory: never writes configuration.
    """

    def __init__(self, hardware: Optional[HardwareProfile] = None):
        self.hardware = hardware or detect_hardware()

    def for_record(self, record: ModelRecord) -> dict:
        """Recommendation record for one model, from its generic evidence.

        The model is evaluated on its own capability evidence and hardware
        fit. Curated qualification is advisory evidence; absence of seed
        metadata never disqualifies a model with real runtime evidence.
        """
        name = record.name
        qual = record.qualification
        claims = list(record.capabilities)
        positive = positive_capability_names(record)
        user_facing = USER_FACING_CAPABILITIES & positive
        reasons: list[str] = []
        caveats = list(record.caveats)

        if qual == Qualification.INCOMPATIBLE:
            reasons.append("Marked incompatible; not recommended")
            return {
                "name": name,
                "recommended": False,
                "tier": "experimental",
                "qualification": qual.value,
                "reasons": reasons,
                "displayName": record.display_name or name,
                "approxRamGb": record.approx_ram_gb,
                "caveats": caveats,
            }

        if not positive:
            reasons.append("Untested with Novi")
            reasons.append("No capability evidence")
            return {
                "name": name,
                "recommended": False,
                "tier": "experimental",
                "qualification": qual.value,
                "reasons": reasons,
                "displayName": record.display_name or name,
                "approxRamGb": record.approx_ram_gb,
                "caveats": caveats,
            }

        if qual.has_evidence:
            reasons.append(f"Qualified: {qual.value}")

        # Per-capability evidence reasons with provenance.
        best_grade = EvidenceStrength.NONE
        for cap in sorted(user_facing):
            grade, source, _conf = evidence_grade(claims, cap, qual)
            if grade > best_grade:
                best_grade = grade
            reasons.append(f"Supports capability '{cap}' ({_source_label(source)})")

        if "tools" in positive:
            reasons.append("Supports Tool Calling")
        if record.metadata.get("works_with_memory"):
            reasons.append("Works with Memory")

        fit = hardware_fit_for_record(record, self.hardware)
        reasons.extend(fit.reasons)

        # Recommendation decision is evidence-based, not seed-membership-based:
        # a positive capability claim of at least ``reported`` strength (i.e.
        # runtime, trusted/supported seed, or an explicit report) is required.
        # Name-inference-only and experimental-seed-only evidence stays below
        # the bar for a confident recommendation.
        recommended = best_grade > EvidenceStrength.SEED_EXPERIMENTAL
        tier = "supported" if qual.has_evidence else "experimental"

        if fit.fit == HardwareFit.DOES_NOT_FIT:
            recommended = False
            tier = "experimental"
        if not user_facing:
            recommended = False
            tier = "experimental"
            reasons.append("Internal capability only; not user-facing")
        if not recommended and best_grade == EvidenceStrength.NAME_INFERENCE:
            reasons.append("Weak name-based capability evidence only")

        return {
            "name": name,
            "recommended": recommended,
            "tier": tier,
            "qualification": qual.value,
            "reasons": reasons,
            "displayName": record.display_name or name,
            "approxRamGb": record.approx_ram_gb,
            "caveats": caveats,
        }

    def for_model(
        self,
        name: str,
        status: str = "installed",
        capabilities: Optional[list[str]] = None,
        evidence_sources: Optional[set[str]] = None,
    ) -> dict:
        """Legacy name-based wrapper used by tests and simple callers.

        Builds a record from the name (enriching with curated seed evidence)
        and runs it through the generic engine. ``status`` is advisory and does
        not affect the result.
        """
        fact = SEED_MODEL_FACTS.get(name)
        sources = set(evidence_sources or ())
        only_inference = bool(sources) and sources <= {"name-inference"}
        claim_source = "name-inference" if only_inference else "reported"
        claims = [
            CapabilityEvidence(c, True, claim_source, None)
            for c in (capabilities or ())
        ]
        record = ModelRecord(
            name=name,
            status=ModelStatus.INSTALLED,
            capabilities=claims,
            qualification=fact.qualification if fact else Qualification.EXPERIMENTAL,
            display_name=fact.display_name if fact else name,
            approx_ram_gb=fact.approx_ram_gb if fact else None,
            min_vram_gb=fact.min_vram_gb if fact else None,
            caveats=list(fact.caveats) if fact else [],
        )
        record = merge_curated_evidence(record, fact)
        return self.for_record(record)


def build_catalog_payload(installed_models: list) -> dict:
    """Compose the full discovery payload the UI consumes.

    Each entry carries installation status, qualification, capabilities,
    caveats, rich identity/runtime metadata (family, quantization, parameter
    count, context length, format, license), capability evidence with
    provenance, and an ``eligibility`` block (hardware fit + confidence).
    Eligibility is derived state — never persisted. No Automatic/Custom
    eligibility fields. Installed records pass through the generic engine.
    """
    from .eligibility import evaluate_eligibility  # local import: avoid cycle
    engine = ModelRecommendationEngine()
    entries = []
    for m in installed_models:
        record = _enrich_record(m)
        rec = engine.for_record(record)
        elig = evaluate_eligibility(
            installed_status=m.status,
            hardware=engine.hardware,
            record=record,
        )
        entry = {
            "name": record.name,
            "status": record.status.value,
            "size": record.size_bytes,
            "capabilities": record.capability_flags,
            "recommended": rec["recommended"],
            "tier": rec["tier"],
            "qualification": rec["qualification"],
            "reasons": rec["reasons"],
            "displayName": rec["displayName"],
            "approxRamGb": rec["approxRamGb"],
            "caveats": rec["caveats"],
            "eligibility": {
                "hardwareFit": elig.hardware_fit.value,
                "hardwareConfidence": elig.hardware_confidence.value,
            },
            "capabilityEvidence": [e.to_dict() for e in record.capabilities],
        }
        identity = record.identity
        if identity is not None:
            entry.update({
                "family": identity.family,
                "variant": identity.variant,
                "quantization": identity.quantization,
            })
        entry.update({
            "parameterCount": record.parameter_count,
            "contextLength": record.context_length,
            "format": record.format,
            "license": record.license,
            "stale": bool(record.stale),
        })
        entries.append(entry)
    return {
        "hardware": {
            "ramGb": engine.hardware.ram_gb,
            "gpu": {
                "name": engine.hardware.gpu.name,
                "vramTotalGb": engine.hardware.gpu.vram_total_gb,
                "vendor": engine.hardware.gpu.vendor,
            },
            "confidence": engine.hardware.confidence.value,
        },
        "models": entries,
    }


def _seed_advisory_records() -> list[ModelRecord]:
    """Seed-only advisory candidate records.

    Curated suggestions for models not currently discovered. Each is an
    ordinary ``ModelRecord`` whose evidence is labeled ``seed``; the generic
    engine evaluates them exactly like installed or future-remote records.
    """
    records: list[ModelRecord] = []
    for name, fact in SEED_MODEL_FACTS.items():
        note = "curated seed metadata (non-authoritative)"
        caps = [
            CapabilityEvidence(c, True, "seed", 0.9, note)
            for c in fact.capabilities
        ]
        claimed = set(fact.capabilities)
        if fact.supports_tools and "tools" not in claimed:
            caps.append(CapabilityEvidence("tools", True, "seed", 0.9, note))
        if fact.supports_vision and "vision" not in claimed:
            caps.append(CapabilityEvidence("vision", True, "seed", 0.9, note))
        records.append(ModelRecord(
            name=name,
            status=ModelStatus.AVAILABLE,
            identity=ModelIdentity(
                name=name, family=fact.family, variant=fact.variant,
                size_tier=fact.size_tier, quantization=fact.quantization),
            source_kind="seed",
            qualification=fact.qualification,
            capabilities=caps,
            capability_flags={c.capability: True for c in caps if c.supported is True},
            display_name=fact.display_name,
            approx_ram_gb=fact.approx_ram_gb,
            min_vram_gb=fact.min_vram_gb,
            caveats=list(fact.caveats),
            license=fact.license,
            metadata={"works_with_memory": True} if fact.works_with_memory else {},
        ))
    return records


def build_available_recommendations(
    installed_names=frozenset(),
    hardware: Optional[HardwareProfile] = None,
    candidate_records: Optional[list[ModelRecord]] = None,
) -> list[dict]:
    """Advisory suggestions for models Novi recommends but lacks installed.

    This is the "recommended but missing" signal that drives the
    explicit-consent setup flow. Evidence is capability evidence + qualification
    + hardware fit only. It deliberately does NOT read configuration, does NOT
    run the resolver, and never installs anything — installing always requires
    an explicit user action through the model-install endpoint.

    Candidates come from ``candidate_records`` when supplied (a future remote/
    registry source), else seed-only advisory records. Every candidate flows
    through the same generic engine — the engine never knows the source.
    Embedding-only models are excluded outright: ``embedding.model`` stays an
    internal setting.
    """
    engine = ModelRecommendationEngine(hardware=hardware)
    installed = set(installed_names or ())
    candidates = (
        candidate_records if candidate_records is not None
        else _seed_advisory_records()
    )
    out = []
    for record in candidates:
        if record.name in installed:
            continue
        if not any(c in USER_FACING_CAPABILITIES
                   for c in positive_capability_names(record)):
            continue
        rec = engine.for_record(record)
        if not rec["recommended"]:
            continue
        # A model that is known NOT to fit the detected hardware is never pushed
        # as a setup install — installing it could not change resolution.
        if hardware_fit_for_record(record, engine.hardware).fit == HardwareFit.DOES_NOT_FIT:
            continue
        identity = record.identity
        out.append({
            "name": record.name,
            "status": "available",
            "size": None,
            "capabilities": record.capability_flags,
            "recommended": True,
            "tier": rec["tier"],
            "qualification": rec["qualification"],
            "reasons": rec["reasons"],
            "displayName": rec["displayName"],
            "approxRamGb": rec["approxRamGb"],
            "caveats": rec["caveats"],
            "family": identity.family if identity else None,
            "variant": identity.variant if identity else None,
            "quantization": identity.quantization if identity else None,
            "license": record.license,
        })
    return out