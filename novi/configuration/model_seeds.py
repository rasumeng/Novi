"""Model seed facts — curated, NON-authoritative evidence (Phase 5E).

This module is the **only** place curated model facts may live, and it is
explicitly *not* authoritative:

* It does NOT define the model universe.
* It does NOT determine whether an installed model exists.
* It does NOT prevent unknown models from appearing in discovery.
* It is NOT required for a model to receive recommendations.
* It is NOT the authoritative capability source (runtime-reported and
  measured evidence rank alongside it).
* It is NOT the only input to eligibility.

The facts here are seed/evidence metadata about known models. A model absent
from this table is treated exactly like any other discovered model — it is
fully usable, discoverable, and recommendable on its own runtime evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .qualification import Qualification


@dataclass
class ModelFact:
    """Curated seed evidence for a known model (advisory, not authoritative)."""

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
    # this model is a poor choice below this many GB of VRAM. ``None`` means
    # no such known constraint.
    min_vram_gb: Optional[float] = None
    works_with_memory: bool = False
    supports_tools: bool = False
    supports_vision: bool = False
    supports_audio: bool = False
    # Structured identity metadata (classification only, never routing logic).
    family: Optional[str] = None
    variant: Optional[str] = None
    size_tier: Optional[str] = None
    quantization: Optional[str] = None
    license: Optional[str] = None
    source: str = "seed"

    @property
    def tested_with_novi(self) -> bool:
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
            "testedWithNovi": self.tested_with_novi,
            "worksWithMemory": self.works_with_memory,
            "supportsTools": self.supports_tools,
            "supportsVision": self.supports_vision,
            "supportsAudio": self.supports_audio,
            "family": self.family,
            "variant": self.variant,
            "sizeTier": self.size_tier,
            "quantization": self.quantization,
            "license": self.license,
            "source": self.source,
        }


# Curated seed facts. Qualification is evidence of a reliable Novi
# experience; it is NOT a claim of fitness for every GPU/hardware config and
# it does not gate discovery/recommendation for models absent here.
SEED_MODEL_FACTS: dict[str, ModelFact] = {
    m.name: m
    for m in [
        # ── Trusted seed models (explicit evidence from real user testing) ──
        ModelFact("gemma4:e4b", "Gemma 4 E4B", 4.0, Qualification.TRUSTED,
                  capabilities=["chat", "reasoning", "tools"], supports_tools=True,
                  family="gemma4", variant="e4b"),
        ModelFact("qwen3:8b", "Qwen 3 8B", 8.0, Qualification.TRUSTED,
                  capabilities=["chat", "reasoning", "coding", "tools"], supports_tools=True,
                  family="qwen3", size_tier="8b"),
        ModelFact("qwen2.5vl:7b", "Qwen 2.5 VL 7B", 8.0, Qualification.TRUSTED,
                  capabilities=["chat", "vision", "tools"], supports_tools=True, supports_vision=True,
                  family="qwen2.5vl", size_tier="7b"),
        # ── Trusted overall, WITH a hardware caveat ─────────────────────────
        # Gemma 4 is trusted overall, but has performed poorly/sluggishly on
        # lower-VRAM systems. This is a caveat, not a fabricated GPU/model
        # compatibility matrix.
        ModelFact("gemma4", "Gemma 4", 12.0, Qualification.TRUSTED,
                  capabilities=["chat", "reasoning", "tools"], supports_tools=True,
                  min_vram_gb=12.0,
                  caveats=[
                      "Gemma 4:12b is sluggish on systems with 8 GB VRAM; "
                      "gemma4:e4b is the recommended variant for lower-VRAM machines.",
                  ],
                  family="gemma4"),
        # ── Supported (known to work and reasonable, below trusted) ─────────
        ModelFact("phi3:mini", "Phi-3 Mini", 4.0, Qualification.SUPPORTED,
                  supports_tools=True, family="phi3"),
        ModelFact("llama3.2:3b", "Llama 3.2 3B", 4.0, Qualification.SUPPORTED,
                  works_with_memory=True, supports_tools=True,
                  family="llama3.2", size_tier="3b"),
        ModelFact("llama3.1:8b", "Llama 3.1 8B", 8.0, Qualification.SUPPORTED,
                  works_with_memory=True, supports_tools=True,
                  capabilities=["chat", "reasoning", "tools"],
                  family="llama3.1", size_tier="8b"),
        ModelFact("qwen2.5-coder:7b", "Qwen 2.5 Coder 7B", 8.0, Qualification.SUPPORTED,
                  supports_tools=True, capabilities=["chat", "coding", "tools"],
                  family="qwen2.5-coder", size_tier="7b"),
        ModelFact("qwen2.5-coder:32b", "Qwen 2.5 Coder 32B", 24.0, Qualification.SUPPORTED,
                  supports_tools=True, capabilities=["chat", "coding", "tools"],
                  family="qwen2.5-coder", size_tier="32b"),
        ModelFact("llama3.1:70b", "Llama 3.1 70B", 48.0, Qualification.SUPPORTED,
                  supports_tools=True, capabilities=["chat", "reasoning", "tools"],
                  family="llama3.1", size_tier="70b"),
        ModelFact("llava:7b", "LLaVA 7B", 8.0, Qualification.SUPPORTED,
                  supports_vision=True, capabilities=["chat", "vision"],
                  family="llava", size_tier="7b"),
        ModelFact("llava:13b", "LLaVA 13B", 14.0, Qualification.SUPPORTED,
                  supports_vision=True, capabilities=["chat", "vision"],
                  family="llava", size_tier="13b"),
        ModelFact("nomic-embed-text", "Nomic Embed Text", 1.0, Qualification.SUPPORTED,
                  works_with_memory=True, capabilities=["embeddings"],
                  family="nomic-embed-text"),
        ModelFact("mxbai-embed-large", "MixedBread Embed Large", 2.0, Qualification.SUPPORTED,
                  works_with_memory=True, capabilities=["embeddings"],
                  family="mxbai-embed-large"),
    ]
}