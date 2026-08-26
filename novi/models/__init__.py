"""Model layer — capability-based model selection.

Phase B: ModelService, ModelRegistry, ModelUnavailableError.
ModelInfo lives in novi.providers (shared with provider layer).
"""

from .registry import ModelRegistry
from .service import ModelService, ModelUnavailableError

__all__ = ["ModelRegistry", "ModelService", "ModelUnavailableError"]
