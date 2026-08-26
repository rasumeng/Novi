"""ModelRegistry — internal cache of discovered models.

Phase A: pure data cache, no business logic.
Phase B: consumed by ModelService.
"""

from __future__ import annotations

from typing import Optional

from ..providers import ModelInfo


class ModelRegistry:
    """Cache of discovered models across all providers.

    Responsibilities:
      - store discovered models per provider
      - query by model name
      - validate that a configured model exists

    No business logic, no heuristic matching, no fallback selection.
    """

    def __init__(self):
        self._models: dict[str, ModelInfo] = {}

    def update(self, provider: str, models: list[ModelInfo]):
        for m in models:
            m.provider = provider
            self._models[m.name] = m

    def find(self, model_name: str) -> Optional[ModelInfo]:
        return self._models.get(model_name)

    def validate(self, model_name: str) -> bool:
        return model_name in self._models

    def list_all(self) -> list[ModelInfo]:
        return list(self._models.values())

    def clear(self):
        self._models.clear()

    def __len__(self) -> int:
        return len(self._models)
