"""SimpleLLM + provider-level model resolution tests.

Covers the Settings-to-execution contract:
- SimpleLLM re-resolves the workload model on every call, so a selection
  change is picked up immediately (no stale cached client).
- The Ollama provider forwards the ``reasoning`` setting to ChatOllama.
"""

import pytest

from cozmo.models import ModelUnavailableError
from cozmo.providers.base import OllamaProvider
from cozmo.services.simple_llm import SimpleLLM


class _FakeClient:
    def __init__(self):
        self.invoke_count = 0

    def invoke(self, prompt, **kwargs):
        self.invoke_count += 1
        return type("Result", (), {"content": "ok"})()


class _FakeModelService:
    def __init__(self, model="a"):
        self.model = model
        self.clients = []
        self.resolve_calls = 0

    def resolve(self, workload):
        self.resolve_calls += 1
        return "ollama", self.model

    def client(self, workload):
        c = _FakeClient()
        self.clients.append(c)
        return c


def test_simple_llm_reresolves_model_every_call():
    ms = _FakeModelService(model="a")
    llm = SimpleLLM(ms, workload="general")

    llm.invoke("first")
    llm.invoke("second")
    assert ms.resolve_calls == 2          # re-resolved per call
    assert len(ms.clients) == 1           # same model -> client cached

    # The user changes the selection in Settings; the next call must NOT keep
    # using the stale client bound to the previous model.
    ms.model = "b"
    llm.invoke("third")
    assert len(ms.clients) == 2           # model changed -> client rebuilt
    assert llm._model == "b"


def test_simple_llm_client_reuse_after_workload_switch_back():
    ms = _FakeModelService(model="x")
    llm = SimpleLLM(ms, workload="general")
    llm.invoke("a")
    ms.model = "y"
    llm.invoke("b")
    ms.model = "x"
    llm.invoke("c")
    assert len(ms.clients) == 3           # each distinct model -> fresh client


def test_simple_llm_without_model_service_raises_unavailable():
    llm = SimpleLLM(None)
    with pytest.raises(ModelUnavailableError):
        llm.invoke("anything")


def test_ollama_provider_forwards_reasoning_setting(monkeypatch):
    captured = {}

    class _ChatOllama:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("langchain_ollama.ChatOllama", _ChatOllama)

    prov = OllamaProvider("qwen3:8b", cfg={"url": "http://localhost:11434", "reasoning": True})
    prov.get_chat_model()
    assert captured["reasoning"] is True

    prov2 = OllamaProvider("qwen3:8b", cfg={"url": "http://localhost:11434", "reasoning": False})
    prov2.get_chat_model()
    assert captured["reasoning"] is False
