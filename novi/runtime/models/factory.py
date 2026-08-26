"""ModelRuntime — thin execution boundary between Novi's model selection and LangChain.

Novi resolves WHICH model to use (``llm.workloads.<workload>.model``, verbatim,
via ``ModelSelector`` / ``ModelService``). This layer receives that
already-resolved identity and turns it into a LangChain runnable/model by
delegating construction to the existing provider layer (``novi.providers``).

Responsibilities:
  * accept an already-resolved (provider, model, provider-config) identity
  * reject an empty/unset model selection with :class:`ModelUnavailableError`
    BEFORE any LangChain client is constructed
  * delegate construction to ``novi.providers`` (ChatOllama / ChatOpenAI)
  * preserve the resolved model string unchanged and the provider config
  * optionally bind tools onto the already-created LangChain model

ModelRuntime MUST NOT:
  * call ``recommend()`` / ``ModelRecommendationEngine`` / ``apply_selection()``
  * write configuration
  * inspect hardware
  * choose between models, substitute, fall back, or rank
  * parse model names or contain model-name conditionals
  * contain VRAM/RAM thresholds or provider-selection heuristics

This is an execution layer, never a policy layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ...models import ModelUnavailableError
from ...providers import create_provider

log = logging.getLogger("novi.model_runtime")


@dataclass(frozen=True)
class ResolvedModel:
    """Model identity already resolved by Novi's selection system.

    The immutable hand-off between Novi model selection and LangChain model
    construction. Contains ONLY what is needed to construct/use the
    already-selected model:

      * ``provider`` — which provider owns this model (``ollama``/``openai``)
      * ``model``    — the verbatim ``llm.workloads.<workload>.model`` value
      * ``config``   — provider settings (url/base_url, api key env, reasoning)
      * ``supports_tools`` — descriptive capability: may tools be bound onto
        this model. Informational only — never used to select/substitute.

    Per-call knobs (``temperature``) stay method parameters on
    ``ModelRuntime``; they vary per invocation and are not part of the
    selection identity.

    Explicitly ABSENT: recommendation state, candidate lists, hardware/VRAM
    ranking, fallback candidates, workload-selection logic, and persistence
    information.
    """

    provider: str
    model: str
    config: dict = field(default_factory=dict)
    supports_tools: bool = True


class ModelRuntime:
    """Turns a resolved selection into a LangChain runnable/model.

    Execution layer only — it never selects a model.
    """

    def __init__(self, provider_factory: Callable[..., Any] = create_provider):
        self._provider_factory = provider_factory
        self._providers: dict[str, Any] = {}

    # ── public API ──────────────────────────────────────────────────────

    def create_chat_model(self, resolved: ResolvedModel, temperature: float = 0.0):
        """Return the LangChain chat model for an already-resolved selection.

        Raises :class:`ModelUnavailableError` when the selection is empty,
        before any LangChain client is constructed.
        """
        return self._provider_for(resolved).get_chat_model(temperature)

    def bind_tools(self, resolved: ResolvedModel, tools: list,
                   temperature: float = 0.0):
        """Bind tools onto the LangChain chat model for a resolved selection."""
        return self._provider_for(resolved).bind_tools(tools, temperature)

    def clear(self):
        """Drop cached provider instances (e.g. after re-discovery)."""
        self._providers.clear()

    # ── internal ────────────────────────────────────────────────────────

    def _provider_for(self, resolved: ResolvedModel):
        if not resolved.model:
            raise ModelUnavailableError(
                "runtime", None, [],
                detail="No model is selected — refusing to construct a LangChain model.",
            )
        key = f"{resolved.provider}:{resolved.model}"
        if key not in self._providers:
            self._providers[key] = self._provider_factory(
                resolved.provider, resolved.model, resolved.config)
        return self._providers[key]