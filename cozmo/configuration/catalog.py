"""Curated model facts + recommendation engine.

Known-model metadata (size, RAM, capabilities, tested-with-Cozmo) that the
discovery layer cross-references to produce *explained* recommendations:

    Recommended (Tested with Cozmo)
    Recommended (Best for your hardware)
    Recommended (Works with Memory)
    Recommended (Supports Tool Calling)

Recommendations always carry a reason; never vague labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ModelFact:
    name: str
    display_name: str = ""
    approx_ram_gb: float = 4.0
    capabilities: list[str] = field(default_factory=lambda: ["chat"])
    tested_with_cozmo: bool = False
    works_with_memory: bool = False
    supports_tools: bool = False
    supports_vision: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "displayName": self.display_name or self.name,
            "approxRamGb": self.approx_ram_gb,
            "capabilities": self.capabilities,
            "testedWithCozmo": self.tested_with_cozmo,
            "worksWithMemory": self.works_with_memory,
            "supportsTools": self.supports_tools,
            "supportsVision": self.supports_vision,
        }


# Curated facts. Names only — the model is only "recommended" when it is
# actually installed on the user's machine (see ModelRecommendationEngine).
KNOWN_MODEL_FACTS: dict[str, ModelFact] = {
    m.name: m
    for m in [
        ModelFact("phi3:mini", "Phi-3 Mini", 4.0, tested_with_cozmo=True, supports_tools=True),
        ModelFact("llama3.2:3b", "Llama 3.2 3B", 4.0, tested_with_cozmo=True, works_with_memory=True, supports_tools=True),
        ModelFact("llama3.1:8b", "Llama 3.1 8B", 8.0, tested_with_cozmo=True, works_with_memory=True, supports_tools=True, capabilities=["chat", "reasoning", "tools"]),
        ModelFact("qwen2.5-coder:7b", "Qwen 2.5 Coder 7B", 8.0, tested_with_cozmo=True, supports_tools=True, capabilities=["chat", "coding", "tools"]),
        ModelFact("qwen2.5-coder:32b", "Qwen 2.5 Coder 32B", 24.0, tested_with_cozmo=True, supports_tools=True, capabilities=["chat", "coding", "tools"]),
        ModelFact("llama3.1:70b", "Llama 3.1 70B", 48.0, tested_with_cozmo=True, supports_tools=True, capabilities=["chat", "reasoning", "tools"]),
        ModelFact("llava:7b", "LLaVA 7B", 8.0, tested_with_cozmo=True, supports_vision=True, capabilities=["chat", "vision"]),
        ModelFact("llava:13b", "LLaVA 13B", 14.0, tested_with_cozmo=True, supports_vision=True, capabilities=["chat", "vision"]),
        ModelFact("nomic-embed-text", "Nomic Embed Text", 1.0, works_with_memory=True, capabilities=["embeddings"]),
        ModelFact("mxbai-embed-large", "MixedBread Embed Large", 2.0, works_with_memory=True, capabilities=["embeddings"]),
    ]
}


class HardwareProfile:
    """Rough host hardware facts (RAM only for now)."""

    def __init__(self, ram_gb: float = 16.0):
        self.ram_gb = ram_gb


def detect_hardware() -> HardwareProfile:
    try:
        import psutil
        return HardwareProfile(ram_gb=round(psutil.virtual_memory().total / (1024**3), 1))
    except Exception:
        try:
            import os
            # Cross-platform-ish heuristic fallback (Windows/Mac).
            import subprocess
            out = subprocess.run(
                ["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"],
                capture_output=True, text=True, timeout=5,
            )
            for line in out.stdout.splitlines():
                if line.strip().isdigit():
                    return HardwareProfile(ram_gb=round(int(line) / (1024**3), 1))
        except Exception:
            pass
    return HardwareProfile()


class ModelRecommendationEngine:
    """Produces recommendation records for discovered models."""

    def __init__(self, hardware: HardwareProfile | None = None):
        self.hardware = hardware or detect_hardware()

    def for_model(self, name: str, status: str = "installed") -> dict:
        fact = KNOWN_MODEL_FACTS.get(name)
        reasons: list[str] = []
        if fact is None:
            reasons.append("Untested with Cozmo")
            return {
                "name": name,
                "recommended": False,
                "tier": "experimental",
                "reasons": reasons,
                "displayName": name,
                "approxRamGb": None,
            }

        if fact.tested_with_cozmo:
            reasons.append("Tested with Cozmo")
        if self._fits_hardware(fact):
            reasons.append("Best for your hardware")
        if fact.works_with_memory:
            reasons.append("Works with Memory")
        if fact.supports_tools:
            reasons.append("Supports Tool Calling")

        return {
            "name": name,
            "recommended": bool(reasons),
            "tier": "supported" if fact.tested_with_cozmo else "experimental",
            "reasons": reasons,
            "displayName": fact.display_name or name,
            "approxRamGb": fact.approx_ram_gb,
        }

    def _fits_hardware(self, fact: ModelFact) -> bool:
        return fact.approx_ram_gb <= self.hardware.ram_gb

    def recommend_all(self, installed: list) -> list[dict]:
        return [self.for_model(m.name, m.status.value) for m in installed]


def build_catalog_payload(installed_models: list) -> dict:
    """Compose the full discovery payload the UI consumes."""
    engine = ModelRecommendationEngine()
    entries = []
    for m in installed_models:
        rec = engine.for_model(m.name, m.status.value)
        entries.append({
            "name": m.name,
            "status": m.status.value,
            "size": m.size,
            "capabilities": m.capability_flags,
            "recommended": rec["recommended"],
            "tier": rec["tier"],
            "reasons": rec["reasons"],
            "displayName": rec["displayName"],
            "approxRamGb": rec["approxRamGb"],
        })
    return {
        "hardware": {"ramGb": engine.hardware.ram_gb},
        "models": entries,
    }