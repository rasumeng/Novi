"""ModelService — resolves configured models and coordinates providers.

Phase 2: reads strictly from ``llm.workloads`` (general/research/code). No
legacy role names, no role→workload shims — callers pass workload names only.
"""

from __future__ import annotations

import logging
from typing import Optional

from .registry import ModelRegistry as _ModelRegistry
from ..configuration.resolver import WORKLOADS
from ..providers import ModelInfo, PROVIDER_REGISTRY, create_provider, parse_model_spec

log = logging.getLogger("novi.models.service")


class ModelUnavailableError(Exception):
    """Raised when a configured model is not found or cannot satisfy a workload."""

    def __init__(self, workload: str, configured: str, available: list[str],
                 detail: str = ""):
        self.workload = workload
        self.configured = configured
        self.available = available
        if detail:
            msg = detail
        else:
            msg = (f"Model '{configured}' for workload '{workload}' not found. "
                   f"Available: {', '.join(available) if available else '(none)'}")
        super().__init__(msg)


class ModelService:
    """Coordinates providers and resolves workloads to (provider, model_name).

    Model *construction* is delegated to the ``ModelRuntime`` boundary
    (``novi/runtime/models``), which forwards the already-resolved selection
    to the existing provider layer. This class never constructs a LangChain
    model directly and never selects a model.
    """

    def __init__(self, config: dict, registry: _ModelRegistry, runtime=None):
        self._config = config
        self._registry = registry
        self._runtime = runtime

    def update_configuration(self, config: dict):
        """Swap the backing config view.

        Called by the composition root when the Configuration Framework
        emits a change, so workload resolution always reflects the current
        ``llm.workloads.*`` instead of a startup-time snapshot.
        """
        self._config = config

    # ── public API ──────────────────────────────────────────────────────

    def resolve(self, workload: str) -> tuple[str, str]:
        """Resolve workload to (provider_name, model_name).

        Reads ``llm.workloads.<workload>.model``. Raises ModelUnavailableError
        if the configured model is not in the registry.
        """
        provider_name, model_name = self._resolve_spec(workload)[:2]
        return provider_name, model_name

    def bind_model(self, model_name: str, tools: list,
                   temperature: float = 0.0):
        resolved = self._resolved_for_model(model_name)
        return self._get_runtime().bind_tools(resolved, tools, temperature)

    def client_for_model(self, model_name: str,
                         temperature: float = 0.0):
        resolved = self._resolved_for_model(model_name)
        return self._get_runtime().create_chat_model(resolved, temperature)

    def client(self, workload: str, temperature: float = 0.0):
        provider_name, model_name, prov_cfg = self._resolve_spec(workload)
        from ..runtime.models import ResolvedModel
        resolved = ResolvedModel(
            provider=provider_name, model=model_name, config=prov_cfg)
        return self._get_runtime().create_chat_model(resolved, temperature)

    def list_available(self) -> dict[str, list[ModelInfo]]:
        result: dict[str, list[ModelInfo]] = {}
        for m in self._registry.list_all():
            result.setdefault(m.provider, []).append(m)
        return result

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
        """Check every configured workload. Returns list of errors (non-raising)."""
        errors: list[ModelUnavailableError] = []
        workloads = self._get_workloads_config()

        for workload, spec in workloads.items():
            if not spec:
                continue
            provider_name, model_name, _ = self._parse_spec(spec)
            if model_name and not self._registry.validate(model_name):
                available = [m.name for m in self._registry.list_all()]
                errors.append(ModelUnavailableError(workload, model_name, available))
        return errors

    # ── internal ────────────────────────────────────────────────────────

    def _normalize_workload(self, workload: str) -> str:
        """Validate a workload name against the persisted selection surface."""
        if workload not in WORKLOADS:
            raise ValueError(
                f"Unknown workload '{workload}'. Valid workloads: {', '.join(WORKLOADS)}"
            )
        return workload

    def _get_workloads_config(self) -> dict:
        """Read workload→model assignments from ``llm.workloads``."""
        llm = self._config.get("llm", {})
        workloads = llm.get("workloads", {}) if isinstance(llm, dict) else {}
        out = {}
        for workload, spec in (workloads or {}).items():
            if isinstance(spec, dict):
                model = spec.get("model", "") or ""
            elif isinstance(spec, str):
                model = spec
            else:
                model = ""
            out[workload] = model
        return out

    def _parse_spec(self, spec) -> tuple[str, str, dict]:
        providers_cfg = self._config.get("providers", {})
        default_provider = providers_cfg.get("default", "ollama")
        return parse_model_spec(spec, providers_cfg, default_provider)

    def _resolve_spec(self, workload: str) -> tuple[str, str, dict]:
        workload = self._normalize_workload(workload)
        workloads = self._get_workloads_config()
        spec = workloads.get(workload, "")
        provider_name, model_name, prov_cfg = self._parse_spec(spec)

        if model_name and not self._registry.validate(model_name):
            available = [m.name for m in self._registry.list_all()]
            raise ModelUnavailableError(workload, model_name, available)

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