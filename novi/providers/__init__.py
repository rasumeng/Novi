"""LLM Provider abstraction — provider-agnostic model inference.

Each provider wraps a LangChain ChatModel.  The runtime never
constructs provider-specific classes directly.
"""

from .base import (
    LLMProvider,
    ModelInfo,
    OllamaProvider,
    OpenAIProvider,
    PROVIDER_REGISTRY,
    create_provider,
    parse_model_spec,
)

__all__ = [
    "LLMProvider",
    "ModelInfo",
    "OllamaProvider",
    "OpenAIProvider",
    "PROVIDER_REGISTRY",
    "create_provider",
    "parse_model_spec",
]
