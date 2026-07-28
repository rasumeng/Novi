"""
ModelRouter — capability-based model selection with resource awareness.

Resolves a capability set + complexity score to the optimal model.
Consults ResourceManager for VRAM and loaded-model status.
Prefers already-loaded models to avoid reload cost.

Architecture:
  Orchestrator.plan() → ModelRouter.resolve(requirements, preferred)
                         ↓
                    ResourceManager.best_available() / can_load()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Optional

from ..runtime.resources import ResourceManager

log = logging.getLogger("cozmo.model_router")


@dataclass
class ModelRequirement:
    """What a capability or task requires from a model."""

    capability: str = "chat"
    min_vram_gb: float = 0.0
    max_tokens: int = 4096
    supports_tools: bool = False
    supports_vision: bool = False
    supports_structured_output: bool = False
    temperature_range: tuple[float, float] = (0.0, 1.0)
    priority: int = 0


@dataclass
class ModelInfo:
    """Metadata about an available model."""

    name: str = ""
    capability: str = "chat"
    provider: str = "ollama"
    vram_required_gb: float = 0.0
    vram_available_gb: float = 0.0
    is_loaded: bool = False
    context_length: int = 4096
    supports_tools: bool = False
    supports_vision: bool = False
    latency_ms: float = 0.0
    cost_per_token: float = 0.0
    tags: list[str] = field(default_factory=list)


_CAPABILITY_PREFERENCE: dict[str, list[str]] = {
    "coding": ["coding", "planning", "research", "conversation"],
    "planning": ["planning", "research", "coding", "conversation"],
    "research": ["research", "conversation", "coding"],
    "conversation": ["conversation", "research"],
    "vision": ["vision", "conversation"],
}


class ModelRouter:
    """Selects the best model for a given set of requirements.

    Consults ResourceManager for VRAM and loaded-model status.
    """

    def __init__(
        self,
        default_model: str = "qwen3:8b",
        default_capability: str = "chat",
        resource_manager: Optional[ResourceManager] = None,
        capability_preferences: Optional[dict[str, list[str]]] = None,
    ):
        self.default_model = default_model
        self.default_capability = default_capability
        self.resource_manager = resource_manager or ResourceManager()
        self._models: dict[str, ModelInfo] = {}
        self._cap_preferences = capability_preferences or _CAPABILITY_PREFERENCE

    def register(self, model: ModelInfo):
        self._models[model.name] = model

    def populate_from_service(self, model_service, config: dict | None = None):
        """Populate router registry from ModelService discovery + config role hints.

        Reads discovered models from model_service.list_available(),
        assigns capability/supports_tools/supports_vision from config
        role→model mappings and heuristics.
        """
        if config is None:
            config = {}
        roles_cfg = config.get("llm", {}).get("roles", {})
        role_model_map: dict[str, str] = {}
        for role, spec in roles_cfg.items():
            if isinstance(spec, dict):
                m = spec.get("model", "")
            else:
                m = spec
            if m:
                role_model_map[role] = m

        available = model_service.list_available() if hasattr(model_service, 'list_available') else {}
        seen = set()
        for provider_name, models in available.items():
            for m in models:
                if m.name in seen:
                    continue
                seen.add(m.name)
                capability = "chat"
                supports_tools = True
                supports_vision = False
                for role, rm in role_model_map.items():
                    if rm == m.name or m.name.endswith(rm.split(":")[0]):
                        if role == "vision":
                            capability = "vision"
                            supports_vision = True
                            supports_tools = False
                        elif role == "coder":
                            capability = "coding"
                        elif role == "planner":
                            capability = "planning"
                info = ModelInfo(
                    name=m.name,
                    capability=capability,
                    provider=provider_name,
                    supports_tools=supports_tools,
                    supports_vision=supports_vision,
                )
                self._models[m.name] = info

    def _model_matches_requirements(
        self, model: ModelInfo, req: ModelRequirement
    ) -> bool:
        """Check if a single model meets the given requirements."""
        if req.capability and model.capability != req.capability:
            return False
        if req.supports_tools and not model.supports_tools:
            return False
        if req.supports_vision and not model.supports_vision:
            return False
        return True

    def resolve(
        self,
        requirements: Optional[list[ModelRequirement]] = None,
        preferred: Optional[str] = None,
        complexity_score: Optional[object] = None,
    ) -> str:
        """Resolve capability needs to a model name.

        Uses capability + complexity to narrow candidates.
        Priority:
        1. preferred model (if fits VRAM and meets requirements)
        2. Already-loaded model matching capability
        3. Model matching capability that fits VRAM + complexity tier
        4. Default model
        """
        req = requirements[0] if requirements else None

        if preferred:
            if preferred in self._models:
                info = self._models[preferred]
                if (req is None or self._model_matches_requirements(info, req)) and \
                   self.resource_manager.can_load(preferred, info.vram_required_gb):
                    return preferred
                log.info("preferred model %s fails requirements or VRAM, falling back", preferred)
            else:
                return preferred

        cap = self.default_capability
        if req:
            cap = req.capability

        # Upgrade capability tier based on complexity
        cap = self._complexity_tier(cap, complexity_score)

        # Single canonical requirement — same cap used for search, validation, and selection
        if req and cap != req.capability:
            req = replace(req, capability=cap)

        candidates = self._find_models(cap)
        if not candidates:
            return self.default_model

        for m in candidates:
            if req is not None and not self._model_matches_requirements(m, req):
                continue
            if self.resource_manager.is_loaded(m.name):
                return m.name

        for m in candidates:
            if req is not None and not self._model_matches_requirements(m, req):
                continue
            if self.resource_manager.can_load(m.name, m.vram_required_gb):
                return m.name

        best = self.resource_manager.best_available(
            [m.name for m in candidates],
            min_vram_gb=candidates[0].vram_required_gb if candidates else 0,
        )
        if best:
            return best

        log.warning("no model fits VRAM for capability %s, using default", cap)
        return self.default_model

    @staticmethod
    def _complexity_tier(base_capability: str, complexity_score: object | None) -> str:
        """Upgrade capability tier when complexity demands it."""
        if complexity_score is None:
            return base_capability
        score = getattr(complexity_score, "score", 0)
        if score < 4:
            return base_capability
        upgrades = {
            "conversation": "research",
            "research": "planning",
            "coding": "coding",
            "planning": "planning",
        }
        return upgrades.get(base_capability, base_capability)

    def _find_models(self, capability: str) -> list[ModelInfo]:
        """Find models matching capability, with preference fallback chain."""
        pref_order = self._cap_preferences.get(capability, ["conversation"])
        seen = set()
        result = []
        for cap in pref_order:
            for m in self._models.values():
                if m.capability == cap and m.name not in seen:
                    result.append(m)
                    seen.add(m.name)
        # Add any remaining unmatched models
        for m in self._models.values():
            if m.name not in seen:
                result.append(m)
                seen.add(m.name)
        return result

    def list_available(self, capability: str = "") -> list[ModelInfo]:
        if not capability:
            return list(self._models.values())
        return [m for m in self._models.values() if m.capability == capability]
