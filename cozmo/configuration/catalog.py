"""Curated model facts + qualification + recommendation engine.

Known-model metadata (size, RAM, capabilities, caveats) plus a first-class
``qualification`` grade (trusted / supported / experimental / incompatible)
that the future Automatic resolution layer consumes.

Qualification is independent of installation status and hardware fit — see
``qualification.py``. Recommendations are derived (later) from qualification +
hardware + capabilities; this module only assembles the facts.

Recommendations always carry a reason; never vague labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .hardware import (
    HardwareProfile,
    detect_hardware,
)
from .qualification import Qualification


@dataclass
class ModelFact:
    name: str
    display_name: str = ""
    approx_ram_gb: float = 4.0
    qualification: Qualification = Qualification.EXPERIMENTAL
    capabilities: list[str] = field(default_factory=lambda: ["chat"])
    caveats: list[str] = field(default_factory=list)
    # Real, measured/model-documented VRAM requirement in GB. ``None`` means
    # "do not know" — never fabricated, never compared as a guessed number.
    vram_required_gb: Optional[float] = None
    # Conservative curated hint (from user testing, not a measured capacity):
    # this model is not a good automatic choice below this many GB of VRAM.
    # ``None`` means no such known constraint. Encodes the Gemma-4-on-8GB caveat
    # so the resolver can respect it without inventing a compatibility matrix.
    min_vram_gb: Optional[float] = None
    works_with_memory: bool = False
    supports_tools: bool = False
    supports_vision: bool = False

    @property
    def tested_with_cozmo(self) -> bool:
        """Back-compat: qualification with direct evidence."""
        return self.qualification.has_evidence

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "displayName": self.display_name or self.name,
            "approxRamGb": self.approx_ram_gb,
            "vramRequiredGb": self.vram_required_gb,
            "minVramGb": self.min_vram_gb,
            "qualification": self.qualification.value,
            "capabilities": self.capabilities,
            "caveats": self.caveats,
            "testedWithCozmo": self.tested_with_cozmo,
            "worksWithMemory": self.works_with_memory,
            "supportsTools": self.supports_tools,
            "supportsVision": self.supports_vision,
        }


# Curated facts. Names only — a model is only "recommended" when it is actually
# installed on the user's machine. Qualification is evidence of a reliable Cozmo
# experience; it is NOT a claim of fitness for every GPU/hardware config.
KNOWN_MODEL_FACTS: dict[str, ModelFact] = {
    m.name: m
    for m in [
        # ── Trusted seed models (explicit evidence from real user testing) ──
        ModelFact("gemma4:e4b", "Gemma 4 E4B", 4.0, Qualification.TRUSTED,
                  capabilities=["chat", "reasoning", "tools"], supports_tools=True),
        ModelFact("qwen3:8b", "Qwen 3 8B", 8.0, Qualification.TRUSTED,
                  capabilities=["chat", "reasoning", "coding", "tools"], supports_tools=True),
        ModelFact("qwen2.5vl:7b", "Qwen 2.5 VL 7B", 8.0, Qualification.TRUSTED,
                  capabilities=["chat", "vision", "tools"], supports_tools=True, supports_vision=True),
        # ── Trusted overall, WITH a hardware caveat ─────────────────────────
        # Gemma 4 is trusted overall, but has performed poorly/sluggishly on
        # lower-VRAM systems. Specifically: Gemma 4:12b is too slow on 8 GB
        # VRAM, while gemma4:e4b performs well on 8 GB VRAM. This is a caveat,
        # not a fabricated GPU/model compatibility matrix.
        ModelFact("gemma4", "Gemma 4", 12.0, Qualification.TRUSTED,
                  capabilities=["chat", "reasoning", "tools"], supports_tools=True,
                  min_vram_gb=12.0,
                  caveats=[
                      "Gemma 4:12b is sluggish on systems with 8 GB VRAM; "
                      "gemma4:e4b is the recommended variant for lower-VRAM machines.",
                  ]),
        # ── Supported (known to work and reasonable, below trusted) ─────────
        ModelFact("phi3:mini", "Phi-3 Mini", 4.0, Qualification.SUPPORTED, supports_tools=True),
        ModelFact("llama3.2:3b", "Llama 3.2 3B", 4.0, Qualification.SUPPORTED,
                  works_with_memory=True, supports_tools=True),
        ModelFact("llama3.1:8b", "Llama 3.1 8B", 8.0, Qualification.SUPPORTED,
                  works_with_memory=True, supports_tools=True,
                  capabilities=["chat", "reasoning", "tools"]),
        ModelFact("qwen2.5-coder:7b", "Qwen 2.5 Coder 7B", 8.0, Qualification.SUPPORTED,
                  supports_tools=True, capabilities=["chat", "coding", "tools"]),
        ModelFact("qwen2.5-coder:32b", "Qwen 2.5 Coder 32B", 24.0, Qualification.SUPPORTED,
                  supports_tools=True, capabilities=["chat", "coding", "tools"]),
        ModelFact("llama3.1:70b", "Llama 3.1 70B", 48.0, Qualification.SUPPORTED,
                  supports_tools=True, capabilities=["chat", "reasoning", "tools"]),
        ModelFact("llava:7b", "LLaVA 7B", 8.0, Qualification.SUPPORTED,
                  supports_vision=True, capabilities=["chat", "vision"]),
        ModelFact("llava:13b", "LLaVA 13B", 14.0, Qualification.SUPPORTED,
                  supports_vision=True, capabilities=["chat", "vision"]),
        ModelFact("nomic-embed-text", "Nomic Embed Text", 1.0, Qualification.SUPPORTED,
                  works_with_memory=True, capabilities=["embeddings"]),
        ModelFact("mxbai-embed-large", "MixedBread Embed Large", 2.0, Qualification.SUPPORTED,
                  works_with_memory=True, capabilities=["embeddings"]),
    ]
}


class ModelRecommendationEngine:
    """Produces recommendation records for discovered models."""

    def __init__(self, hardware: HardwareProfile | None = None):
        self.hardware = hardware or detect_hardware()

    def for_model(self, name: str, status: str = "installed") -> dict:
        from .eligibility import hardware_fit_for, HardwareFit
        fact = KNOWN_MODEL_FACTS.get(name)
        reasons: list[str] = []

        # Unknown models stay experimental/unqualified — never auto-promoted.
        if fact is None:
            reasons.append("Untested with Cozmo")
            return {
                "name": name,
                "recommended": False,
                "tier": "experimental",
                "qualification": Qualification.EXPERIMENTAL.value,
                "reasons": reasons,
                "displayName": name,
                "approxRamGb": None,
                "caveats": [],
            }

        fit = hardware_fit_for(fact, self.hardware)
        if fact.qualification.has_evidence:
            reasons.append(f"Qualified: {fact.qualification.value}")
        if fit == HardwareFit.FITS:
            reasons.append("Best for your hardware")
        elif fit == HardwareFit.UNKNOWN:
            reasons.append("Hardware fit unknown")
        if fact.works_with_memory:
            reasons.append("Works with Memory")
        if fact.supports_tools:
            reasons.append("Supports Tool Calling")

        # Incompatible is never treated as trusted/supported and never
        # recommended. Keep the legacy two-value ``tier`` for UI compat.
        if fact.qualification == Qualification.INCOMPATIBLE:
            tier = "experimental"
            recommended = False
        else:
            tier = (
                "supported"
                if fact.qualification.has_evidence
                else "experimental"
            )
            recommended = (
                fact.qualification.has_evidence
                and fit != HardwareFit.DOES_NOT_FIT
            ) or bool(fact.works_with_memory or fact.supports_tools)

        return {
            "name": name,
            "recommended": recommended,
            "tier": tier,
            "qualification": fact.qualification.value,
            "reasons": reasons,
            "displayName": fact.display_name or name,
            "approxRamGb": fact.approx_ram_gb,
            "caveats": fact.caveats,
        }

    def recommend_all(self, installed: list) -> list[dict]:
        return [self.for_model(m.name, m.status.value) for m in installed]


def build_catalog_payload(installed_models: list) -> dict:
    """Compose the full discovery payload the UI consumes.

    Each entry carries installation status, qualification, capabilities,
    caveats, and an ``eligibility`` block (hardware fit + confidence + Automatic
    / Custom eligibility) derived from current hardware + installed models +
    catalog. Eligibility is derived state — never persisted.
    """
    from .eligibility import evaluate_eligibility  # local import: avoid cycle
    engine = ModelRecommendationEngine()
    entries = []
    for m in installed_models:
        rec = engine.for_model(m.name, m.status.value)
        elig = evaluate_eligibility(
            m.name, installed_status=m.status, hardware=engine.hardware,
            discovered_capabilities=m.capability_flags)
        entries.append({
            "name": m.name,
            "status": m.status.value,
            "size": m.size,
            "capabilities": m.capability_flags,
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
                "eligibleAutomatic": elig.eligible_automatic,
                "eligibleCustom": elig.eligible_custom,
            },
        })
    return {
        "hardware": {"ramGb": engine.hardware.ram_gb},
        "models": entries,
    }
