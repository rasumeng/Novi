"""ModelService — resolves the primary model and coordinates providers.

Reads strictly from ``llm.primary_model``. No workload slots, no shims.
"""

from __future__ import annotations

import logging

from .registry import ModelRegistry as _ModelRegistry
from ..configuration.resolver import PRIMARY_MODEL_KEY, get_primary_model
from ..providers import ModelInfo, PROVIDER_REGISTRY, create_provider, parse_model_spec

log = logging.getLogger("novi.models.service")


class ModelUnavailableError(Exception):
    """Raised when the primary model is not found or lacks a capability."""

    def __init__(self, configured: str, available: list[str],
                 detail: str = ""):
        self.configured = configured
        self.available = available
        if detail:
            msg = detail
        else:
            msg = (f"Model '{configured}' not found. "
                   f"Available: {', '.join(available) if available else '(none)'}")
        super().__init__(msg)


class ModelService:
    """Coordinates providers and resolves the primary model.

    Model *construction* is delegated to the ``ModelRuntime`` boundary
    (``novi/runtime/models``). This class never constructs a LangChain
    model directly and never selects between candidates.
    """

    def __init__(self, config: dict, registry: _ModelRegistry, runtime=None):
        self._config = config
        self._registry = registry
        self._runtime = runtime
        from ..services.inference_coordinator import InferenceCoordinator
        self.inference = InferenceCoordinator()

    def update_configuration(self, config: dict):
        """Swap backing config so resolution reflects current primary model."""
        self._config = config

    # ── public API ──────────────────────────────────────────────────────

    def resolve_primary(self) -> tuple[str, str]:
        """Resolve primary model to (provider_name, model_name)."""
        provider_name, model_name = self._resolve_spec()[:2]
        return provider_name, model_name

    def resolve_primary_snapshot(self):
        """Pin provider settings along with identity for a complete memory job."""
        from copy import deepcopy
        from ..runtime.models import ResolvedModel
        provider, model, config = self._resolve_spec()
        if not model:
            raise ModelUnavailableError('', [])
        return ResolvedModel(provider, model, deepcopy(config))

    def bind_model(self, model_name: str, tools: list,
                   temperature: float = 0.0):
        resolved = self._resolved_for_model(model_name)
        return self._get_runtime().bind_tools(resolved, tools, temperature)

    def client_for_model(self, model_name: str,
                         temperature: float = 0.0):
        resolved = self._resolved_for_model(model_name)
        return self._get_runtime().create_chat_model(resolved, temperature)

    def client(self, temperature: float = 0.0):
        provider_name, model_name, prov_cfg = self._resolve_spec()
        from ..runtime.models import ResolvedModel
        resolved = ResolvedModel(
            provider=provider_name, model=model_name, config=prov_cfg)
        return self._get_runtime().create_chat_model(resolved, temperature)

    def list_available(self) -> dict[str, list[ModelInfo]]:
        result: dict[str, list[ModelInfo]] = {}
        for m in self._registry.list_all():
            result.setdefault(m.provider, []).append(m)
        return result

    def memory_client(self, resolved, **limits):
        return self._get_runtime().create_memory_client(resolved, **limits)

    def refresh(self):
        """Force re-discovery from all configured providers."""
        self._registry.clear()
        if self._runtime is not None:
            self._runtime.clear()

        providers_cfg = self._config.get("providers", {})
        for provider_name in PROVIDER_REGISTRY:
            prov_cfg = providers_cfg.get(provider_name, {})
            try:
                provider = create_provider(provider_name, "", prov_cfg)
                models = provider.list_models()
                if models:
                    self._registry.update(provider_name, models)
            except Exception as e:
                log.warning("refresh: provider '%s' failed: %s", provider_name, e)

    def validate(self) -> list[ModelUnavailableError]:
        """Check primary model. Returns list of errors (non-raising)."""
        errors: list[ModelUnavailableError] = []
        spec = get_primary_model(config=self._config)
        if not spec:
            return errors
        provider_name, model_name, _ = self._parse_spec(spec)
        if model_name and not self._registry.validate(model_name):
            available = [m.name for m in self._registry.list_all()]
            errors.append(ModelUnavailableError(model_name, available))
        return errors

    # ── internal ────────────────────────────────────────────────────────

    def _get_primary_spec(self) -> str:
        llm = self._config.get("llm", {}) if isinstance(self._config, dict) else {}
        spec = llm.get("primary_model", "") if isinstance(llm, dict) else ""
        # Tolerate legacy dict shape {model: "..."}.
        if isinstance(spec, dict):
            spec = spec.get("model", "") or ""
        return spec if isinstance(spec, str) else ""

    def _parse_spec(self, spec) -> tuple[str, str, dict]:
        providers_cfg = self._config.get("providers", {})
        default_provider = providers_cfg.get("default", "ollama")
        return parse_model_spec(spec, providers_cfg, default_provider)

    def _resolve_spec(self) -> tuple[str, str, dict]:
        spec = self._get_primary_spec()
        provider_name, model_name, prov_cfg = self._parse_spec(spec)

        if model_name and not self._registry.validate(model_name):
            available = [m.name for m in self._registry.list_all()]
            raise ModelUnavailableError(model_name, available)

        return provider_name, model_name, prov_cfg

    def _resolved_for_model(self, model_name: str):
        from ..runtime.models import ResolvedModel

        info = self._registry.find(model_name)
        if info:
            provider_name = info.provider
        else:
            provider_name = self._config.get("providers", {}).get("default", "ollama")

        providers_cfg = self._config.get("providers", {})
        prov_cfg = providers_cfg.get(provider_name, {})

        return ResolvedModel(
            provider=provider_name, model=model_name, config=prov_cfg)

    def _get_runtime(self) -> "ModelRuntime":
        """Return the shared ModelRuntime, building one lazily."""
        if self._runtime is None:
            from ..runtime.models import ModelRuntime
            self._runtime = ModelRuntime()
        return self._runtime
