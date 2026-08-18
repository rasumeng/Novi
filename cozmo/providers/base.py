"""LLM Provider Abstraction.

Plug-in architecture supporting Ollama, OpenAI, Anthropic, etc.
Each provider wraps a LangChain ChatModel so the runtime's tool-binding
pipeline works unchanged.

Moved from runtime/providers/llm.py — Phase A.
"""

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("cozmo.providers.llm")


@dataclass
class ModelInfo:
    """Minimal model metadata returned by providers."""
    name: str
    provider: str = ""
    tags: list[str] = field(default_factory=list)


class LLMProvider(ABC):
    """Abstract LLM provider. Subclasses wrap a LangChain BaseChatModel.

    Args:
        model_name: The model identifier (e.g. '<model-id>')
        cfg: Provider-specific config dict (url, api keys, etc.)
    """

    def __init__(self, model_name: str, cfg: dict | None = None):
        self.model_name = model_name
        self.cfg = cfg or {}

    @abstractmethod
    def get_chat_model(self, temperature: float = 0.0) -> Any:
        """Return a LangChain BaseChatModel for this provider at the given temperature."""

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        """Return list of available models from this provider."""

    def bind_tools(self, tools: list, temperature: float = 0.0):
        return self.get_chat_model(temperature).bind_tools(tools)

    def invoke_messages(self, messages: list, temperature: float = 0.0):
        return self.get_chat_model(temperature).invoke(messages)

    def stream_messages(self, messages: list, temperature: float = 0.0):
        yield from self.get_chat_model(temperature).stream(messages)

    def invoke(self, prompt: str, system_prompt: str | None = None,
               temperature: float = 0.0) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage
        try:
            messages = []
            if system_prompt:
                messages.append(SystemMessage(content=system_prompt))
            messages.append(HumanMessage(content=prompt))
            return self.get_chat_model(temperature).invoke(messages).content
        except Exception as e:
            return f"Error: model unavailable — {e}"

    def stream(self, prompt: str, system_prompt: str | None = None,
               temperature: float = 0.0):
        from langchain_core.messages import HumanMessage, SystemMessage
        try:
            messages = []
            if system_prompt:
                messages.append(SystemMessage(content=system_prompt))
            messages.append(HumanMessage(content=prompt))
            for chunk in self.get_chat_model(temperature).stream(messages):
                if chunk.content:
                    yield chunk.content
        except Exception as e:
            yield f"Error: model unavailable — {e}"


# ── Concrete providers ───────────────────────────────────────────────


class OllamaProvider(LLMProvider):
    """Ollama provider using langchain-ollama ChatOllama."""

    def __init__(self, model_name: str, cfg: dict | None = None):
        super().__init__(model_name, cfg)
        self.base_url = self.cfg.get("url", "http://localhost:11434")
        self._clients: dict[float, Any] = {}

    def get_chat_model(self, temperature: float = 0.0):
        from langchain_ollama import ChatOllama

        key = round(float(temperature), 2)
        if key not in self._clients:
            reasoning = self.cfg.get("reasoning", False)
            try:
                self._clients[key] = ChatOllama(
                    model=self.model_name,
                    base_url=self.base_url,
                    temperature=key,
                    reasoning=reasoning,
                )
            except Exception:
                self._clients[key] = ChatOllama(
                    model=self.model_name,
                    base_url=self.base_url,
                    temperature=key,
                )
        return self._clients[key]

    def list_models(self) -> list[ModelInfo]:
        import json
        import urllib.request
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read())
            return [ModelInfo(name=m["name"], provider="ollama") for m in data.get("models", [])]
        except Exception:
            return []


class OpenAIProvider(LLMProvider):
    """OpenAI / compatible provider using langchain-openai ChatOpenAI."""

    def __init__(self, model_name: str, cfg: dict | None = None):
        super().__init__(model_name, cfg)
        env_key = self.cfg.get("api_key_env", "OPENAI_API_KEY")
        self.api_key = os.getenv(env_key)
        if not self.api_key:
            log.warning("OPENAI_API_KEY not set — OpenAI calls will fail")
        self.base_url = self.cfg.get("url", None)

    def get_chat_model(self, temperature: float = 0.0):
        from langchain_openai import ChatOpenAI

        key = round(float(temperature), 2)
        if key not in self._clients:
            kwargs = dict(
                model=self.model_name,
                api_key=self.api_key,
                temperature=key,
            )
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._clients[key] = ChatOpenAI(**kwargs)
        return self._clients[key]

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name=self.model_name, provider="openai")]


# ── Registry ─────────────────────────────────────────────────────────

PROVIDER_REGISTRY: dict[str, type[LLMProvider]] = {
    "ollama": OllamaProvider,
    "openai": OpenAIProvider,
}


def create_provider(provider_name: str, model_name: str,
                    cfg: dict | None = None) -> LLMProvider:
    """Factory: return a provider instance by name."""
    cls = PROVIDER_REGISTRY.get(provider_name)
    if cls is None:
        known = ", ".join(sorted(PROVIDER_REGISTRY))
        raise ValueError(f"Unknown provider '{provider_name}'. Known: {known}")
    return cls(model_name, cfg or {})


def parse_model_spec(spec: str | dict, providers_cfg: dict,
                     default_provider: str = "ollama") -> tuple[str, str, dict]:
    """Parse a model config value into (provider_name, model_name, provider_cfg).

    Accepts:
      - "<model-id>"                   -> ("ollama", "<model-id>", {url: ...})
      - {provider="openai", model="<model-id>"} -> ("openai", "<model-id>", {api_key_env: ...})
    """
    if isinstance(spec, dict):
        provider = spec.get("provider", default_provider)
        name = spec.get("model", "")
    else:
        provider = default_provider
        name = spec
    prov_cfg = providers_cfg.get(provider, {})
    return provider, name, prov_cfg
