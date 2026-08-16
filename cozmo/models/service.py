"""ModelService — resolves configured models and coordinates providers.

Phase 2: reads strictly from ``llm.workloads`` (general/research/code). No
legacy role names, no role→workload shims — callers pass workload names only.
"""

from __future__ import annotations

import logging
from typing import Optional

from .registry import ModelRegistry as _ModelRegistry
from ..configuration.resolver import WORKLOADS
from ..providers import LLMProvider, ModelInfo, PROVIDER_REGISTRY, create_provider, parse_model_spec

log = logging.getLogger("cozmo.models.service")


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
    """Coordinates providers and resolves workloads to (provider, model_name)."""

    def __init__(self, config: dict, registry: _ModelRegistry):
        self._config = config
        self._registry = registry
        self._provider_cache: dict[str, LLMProvider] = {}

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
        provider = self._get_provider_for_model(model_name)
        return provider.bind_tools(tools, temperature)

    def client_for_model(self, model_name: str,
                         temperature: float = 0.0):
        provider = self._get_provider_for_model(model_name)
        return provider.get_chat_model(temperature)

    def client(self, workload: str, temperature: float = 0.0):
        provider_name, model_name = self.resolve(workload)
        provider = self._get_provider_for_model(model_name)
        return provider.get_chat_model(temperature)

    def list_available(self) -> dict[str, list[ModelInfo]]:
        result: dict[str, list[ModelInfo]] = {}
        for m in self._registry.list_all():
            result.setdefault(m.provider, []).append(m)
        return result

    def refresh(self):
        """Force re-discovery from all configured providers."""
        self._registry.clear()
        self._provider_cache.clear()

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

    def _get_provider_for_model(self, model_name: str) -> LLMProvider:
        cache_key = f"model:{model_name}"
        if cache_key in self._provider_cache:
            return self._provider_cache[cache_key]

        info = self._registry.find(model_name)
        if info:
            provider_name = info.provider
        else:
            provider_name = self._config.get("providers", {}).get("default", "ollama")

        providers_cfg = self._config.get("providers", {})
        prov_cfg = providers_cfg.get(provider_name, {})

        provider = create_provider(provider_name, model_name, prov_cfg)
        self._provider_cache[cache_key] = provider
        return provider