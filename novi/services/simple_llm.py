"""SimpleLLM — lightweight ``invoke(prompt) -> str`` over a workload model.

Replaces the legacy ``_RouterLLM`` wrapper. Used for intent classification,
evidence grounding, knowledge summarization, and history compaction — all
advisory, low-frequency LLM calls that share one simple API.

Resolves the workload's configured model lazily (general by default) and
propagates ``ModelUnavailableError`` verbatim — no role shim, no masking.
"""

from __future__ import annotations

import logging

from ..models import ModelUnavailableError

log = logging.getLogger("novi.simple_llm")


class SimpleLLM:
    """Thin ``invoke()`` wrapper over a workload's configured model."""

    def __init__(self, model_service, workload: str = "general"):
        self._client = None
        self._model = ""
        self._ms = model_service
        self._workload = workload

    @property
    def workload(self) -> str:
        return self._workload

    def invoke(self, prompt: str, **kwargs) -> str:
        # Re-resolve the workload's model on every call so a Settings change is
        # picked up immediately — the cached client is rebuilt when the
        # configured model changes. Auxiliary calls (intent, grounding, summary)
        # never silently keep using a stale pre-change model.
        if self._ms is not None:
            _, model_name = self._ms.resolve(self._workload)
            if model_name != self._model:
                self._client = None
                self._model = model_name
        if self._client is None:
            if self._ms is None:
                raise ModelUnavailableError(self._workload, None, [])
            self._client = self._ms.client(self._workload)
        result = self._client.invoke(prompt, **kwargs)
        return result.content if hasattr(result, "content") else str(result)