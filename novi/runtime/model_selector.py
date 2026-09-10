"""ModelSelector — primary-model resolution.

The user's selected primary model (``llm.primary_model``) is the ONLY model
ever used. ``resolve()`` returns it verbatim; capability facts describe the
selected model and requirement checks may reject, but nothing here ever
substitutes, ranks, upgrades, or falls back.

Contract:
* ``resolve()`` returns exactly the user's primary model or raises
  ``ModelUnavailableError``. It never picks another candidate.
* ``capabilities()`` / ``model_capabilities(name)`` are descriptive only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..models import ModelUnavailableError
from ..configuration.model_records import CapabilityState

log = logging.getLogger("novi.model_selector")


@dataclass(frozen=True)
class ModelCapabilities:
    """Capability facts of the SELECTED model. Descriptive only."""

    capabilities: frozenset = field(default_factory=frozenset)
    supports_tools: bool = False
    supports_vision: bool = False
    supports_reasoning: bool = False
    supports_coding: bool = False
    supports_audio: bool = False

    @property
    def supports_thinking(self) -> bool:
        """UI alias: Thinking == reasoning."""
        return self.supports_reasoning

    def to_dict(self) -> dict:
        return {
            "vision": self.supports_vision,
            "tools": self.supports_tools,
            "reasoning": self.supports_reasoning,
            "thinking": self.supports_thinking,
            "audio": self.supports_audio,
            "coding": self.supports_coding,
            "capabilities": sorted(self.capabilities),
        }


def _normalize_capability(cap: str) -> str:
    """Thinking is UI alias for reasoning."""
    if cap == "thinking":
        return "reasoning"
    return cap


def model_capability_state(model_name: str, capability: str) -> CapabilityState:
    """Tri-state capability answer for ``model_name`` / ``capability``."""
    cap = _normalize_capability(capability)
    from ..configuration.model_seeds import SEED_MODEL_FACTS
    from ..configuration.discovery import cached_runtime_capabilities, _CACHE

    fact = SEED_MODEL_FACTS.get(model_name)
    cached_caps = set(cached_runtime_capabilities(model_name))
    has_cached_entry = False
    for (url, cached_name) in list(_CACHE._entries):
        if cached_name == model_name and _CACHE.get(url, cached_name) is not None:
            has_cached_entry = True
            break

    if fact is not None:
        caps = set(fact.capabilities)
        if getattr(fact, "supports_audio", False):
            caps.add("audio")
        if getattr(fact, "supports_tools", False):
            caps.add("tools")
        if getattr(fact, "supports_vision", False):
            caps.add("vision")
        caps |= cached_caps
        if cap in caps:
            return CapabilityState.SUPPORTED
        return CapabilityState.UNSUPPORTED
    if cap in cached_caps:
        return CapabilityState.SUPPORTED
    if has_cached_entry:
        return CapabilityState.UNSUPPORTED
    return CapabilityState.UNKNOWN


def model_capabilities(model_name: str) -> ModelCapabilities:
    """Derive capability facts for a model name. Detection only."""
    from ..configuration.model_seeds import SEED_MODEL_FACTS
    from ..configuration.discovery import cached_runtime_capabilities

    fact = SEED_MODEL_FACTS.get(model_name)
    caps = set(fact.capabilities) if fact else set()
    caps |= set(cached_runtime_capabilities(model_name))

    if not caps and fact is None:
        return ModelCapabilities()

    supports_audio = bool(fact and getattr(fact, "supports_audio", False)) or "audio" in caps
    return ModelCapabilities(
        capabilities=frozenset(caps),
        supports_tools=bool(fact and fact.supports_tools) or "tools" in caps,
        supports_vision=bool((fact and fact.supports_vision) or "vision" in caps),
        supports_reasoning="reasoning" in caps,
        supports_coding="coding" in caps,
        supports_audio=supports_audio,
    )


class ModelSelector:
    """Primary-model resolver. Never substitutes, ranks, or falls back."""

    def __init__(self, model_service=None, ollama_url: str = "http://localhost:11434"):
        self.model_service = model_service
        self.ollama_url = ollama_url

    def resolve(self) -> str:
        """Return the primary model (llm.primary_model) verbatim."""
        if self.model_service is None:
            return ""
        # ``resolve_primary`` is the production contract.  Keep the older
        # duck-typed ``resolve`` shape at this boundary for embedded callers
        # and test doubles; it still supplies exactly one selected model and
        # does not permit ranking or fallback.
        if hasattr(self.model_service, "resolve_primary"):
            _, model_name = self.model_service.resolve_primary()
        else:
            _, model_name = self.model_service.resolve("primary")
        if not model_name:
            raise ModelUnavailableError(None, [])
        return model_name

    def capability_state(self, model_name: str, capability: str) -> CapabilityState:
        """Tri-state state for a specific model/capability."""
        return model_capability_state(model_name, _normalize_capability(capability))

    def verify(self, model_name: str, capability: str, timeout: float = 3.0) -> CapabilityState:
        """Live verification with bounded /api/show fallback."""
        cap = _normalize_capability(capability)
        state = model_capability_state(model_name, cap)
        if state != CapabilityState.UNKNOWN:
            return state
        try:
            from ..configuration.discovery import ModelDiscovery
            discovery = ModelDiscovery(self.ollama_url, timeout=timeout)
            per = discovery.verify_capabilities(model_name, {cap})
            return per.get(cap, CapabilityState.VERIFICATION_FAILED)
        except Exception:
            return CapabilityState.VERIFICATION_FAILED

    def capabilities(self) -> ModelCapabilities:
        """Capability facts of the primary model. Detection only."""
        return model_capabilities(self.resolve())

    def validate(self, *, supports_vision: bool = False,
                 supports_audio: bool = False, supports_tools: bool = False,
                 supports_reasoning: bool = False) -> None:
        """Requirement check on the primary model. Rejects, never substitutes."""
        model_name = self.resolve()
        caps = model_capabilities(model_name)
        if supports_vision and not caps.supports_vision:
            raise ModelUnavailableError(
                model_name, [],
                detail=("The model you're currently using doesn't support image input. "
                        "Choose a vision-capable model to analyze images."),
            )
        if supports_audio and not caps.supports_audio:
            raise ModelUnavailableError(
                model_name, [],
                detail=("The model you're currently using doesn't support audio input. "
                        "Choose an audio-capable model to analyze audio."),
            )
        if supports_tools and not caps.supports_tools:
            raise ModelUnavailableError(
                model_name, [],
                detail=("The model you're currently using doesn't support tool calling. "
                        "Choose a tool-capable model to use agent tools."),
            )
        if supports_reasoning and not caps.supports_reasoning:
            raise ModelUnavailableError(
                model_name, [],
                detail=("The model you're currently using doesn't support reasoning. "
                        "Choose a reasoning-capable model for this task."),
            )
