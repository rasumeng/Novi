"""ModelSelector — strict workload → model resolution.

A workload's configured model is the ONLY model ever used for that workload.
``resolve()`` returns ``llm.workloads.<workload>.model`` verbatim; capability
facts describe the selected model and requirement checks may reject, but
nothing here ever substitutes, ranks, upgrades, or falls back.

Replaces the legacy ModelRouter (capability-based search with VRAM / loaded-
model preference, complexity-tier upgrades, capability-preference chains, and
``default_model`` fallback). Those behaviors are gone.

Contract:
* ``resolve(workload)`` returns exactly the user's selected model or raises
  ``ModelUnavailableError``. It never picks another candidate.
* ``capabilities(workload)`` / ``model_capabilities(name)`` are descriptive
  only — they never create a selection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..configuration.resolver import WORKLOADS
from ..models import ModelUnavailableError

log = logging.getLogger("novi.model_selector")


@dataclass(frozen=True)
class ModelCapabilities:
    """Capability facts of the SELECTED model.

    Descriptive only — never used to pick or substitute a different model.
    Canonical backend capabilities: vision, tools, reasoning, audio, coding.
    Reasoning is canonical; Thinking is UI label for reasoning.
    """

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


def model_capabilities(model_name: str) -> ModelCapabilities:
    """Derive capability facts for a model name.

    Authoritative-only: curated seed facts plus *measured* runtime-reported
    capabilities from the metadata cache. Weak name inference is deliberately
    excluded — the runtime never trusts a name substring for capability
    validation. Detection only — capabilities never influence selection.

    Canonical set is vision/tools/reasoning/audio(+coding). Reasoning is
    canonical; thinking is UI alias. Audio is strictly model-derived, never
    inferred from tools.

    Unknown models stay unknown: no fabricated capability claims.
    """
    from ..configuration.model_seeds import SEED_MODEL_FACTS
    from ..configuration.discovery import cached_runtime_capabilities

    fact = SEED_MODEL_FACTS.get(model_name)
    caps = set(fact.capabilities) if fact else set()
    caps |= set(cached_runtime_capabilities(model_name))

    if not caps and fact is None:
        return ModelCapabilities()

    # Seed supports_* are canonical model-derived evidence (audio strictly model-derived)
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
    """Strict workload → model resolver. Never substitutes, ranks, or falls back."""

    def __init__(self, model_service=None):
        self.model_service = model_service

    def resolve(self, workload: str) -> str:
        """Return the configured model for ``workload``.

        Reads ``llm.workloads.<workload>.model`` through ModelService. Raises
        ``ModelUnavailableError`` when the workload is unset or its configured
        model is not installed. With no model service wired (headless/test
        runtimes) it returns "" so the caller's existing error path applies.
        """
        self._check_workload(workload)
        if self.model_service is None:
            return ""
        _, model_name = self.model_service.resolve(workload)
        if not model_name:
            raise ModelUnavailableError(workload, None, [])
        return model_name

    def capabilities(self, workload: str) -> ModelCapabilities:
        """Capability facts of the selected model. Detection only; advisory."""
        return model_capabilities(self.resolve(workload))

    def validate(self, workload: str, *, supports_vision: bool = False, supports_audio: bool = False, supports_tools: bool = False, supports_reasoning: bool = False) -> None:
        """Requirement check on the SELECTED model.

        May reject with ``ModelUnavailableError``; never returns a substitute.
        All checks are strictly model-derived (audio never inferred from tools).
        """
        model_name = self.resolve(workload)
        caps = model_capabilities(model_name)
        if supports_vision and not caps.supports_vision:
            raise ModelUnavailableError(
                workload, model_name, [],
                detail=(f"Model '{model_name}' for workload '{workload}' "
                        f"does not support image input. Select a vision-capable model for the {workload} workload."),
            )
        if supports_audio and not caps.supports_audio:
            raise ModelUnavailableError(
                workload, model_name, [],
                detail=(f"Model '{model_name}' for workload '{workload}' "
                        f"does not support audio input. Select an audio-capable model for the {workload} workload."),
            )
        if supports_tools and not caps.supports_tools:
            raise ModelUnavailableError(
                workload, model_name, [],
                detail=(f"Model '{model_name}' for workload '{workload}' "
                        f"does not support tool calling. Select a tool-capable model for the {workload} workload."),
            )
        if supports_reasoning and not caps.supports_reasoning:
            raise ModelUnavailableError(
                workload, model_name, [],
                detail=(f"Model '{model_name}' for workload '{workload}' "
                        f"does not support reasoning. Select a reasoning-capable model for the {workload} workload."),
            )

    @staticmethod
    def _check_workload(workload: str):
        # Deep is an implementation alias for research — normalize before validation.
        if workload == "deep":
            workload = "research"
        if workload not in WORKLOADS:
            raise ValueError(
                f"Unknown workload '{workload}'. Valid workloads: {', '.join(WORKLOADS)}"
            )

    def workload_capabilities(self, workload: str) -> ModelCapabilities:
        """Capabilities of the currently assigned model for ``workload``.

        Deep is an alias for research. Never substitutes models.
        """
        if workload == "deep":
            workload = "research"
        try:
            model = self.resolve(workload)
        except Exception:
            return ModelCapabilities()
        if not model:
            return ModelCapabilities()
        return model_capabilities(model)