"""SimpleLLM — lightweight ``invoke(prompt) -> str`` over the primary model.

Used for intent classification, evidence grounding, knowledge summarization,
and history compaction — all advisory, low-frequency LLM calls sharing one
simple API. Resolves the primary model lazily and propagates
``ModelUnavailableError`` verbatim.
"""

from __future__ import annotations

import logging

from ..models import ModelUnavailableError

log = logging.getLogger("novi.simple_llm")


class SimpleLLM:
    """Thin ``invoke()`` wrapper over the primary model."""

    def __init__(self, model_service):
        self._client = None
        self._model = ""
        self._provider = ""
        self._ms = model_service

    def invoke(self, prompt: str, **kwargs) -> str:
        from contextlib import nullcontext
        coordinator = getattr(self._ms, 'inference', None)
        with coordinator.acquire_foreground() if coordinator else nullcontext():
            return self._invoke(prompt, **kwargs)

    def _invoke(self, prompt: str, **kwargs) -> str:
        # Re-resolve primary model every call so Settings change applies live.
        if self._ms is not None:
            provider, model_name = self._ms.resolve_primary()
            if (provider, model_name) != (self._provider, self._model):
                self._client = None
                self._model = model_name
                self._provider = provider
        if self._client is None:
            if self._ms is None:
                raise ModelUnavailableError(None, [])
            self._client = self._ms.client()
        result = self._client.invoke(prompt, **kwargs)
        return result.content if hasattr(result, "content") else str(result)
