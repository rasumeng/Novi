"""Phase 7 Stage 1 — ModelRuntime boundary regression tests.

Prove the strict contract:
  selected model X → runtime → LangChain → model X
and never:
  selected model X → LangChain → model Y

Also prove that an empty selection (``""``) raises ModelUnavailableError BEFORE
any LangChain client is constructed, with no fallback/substitution, and that
ModelRuntime never performs recommendation.
"""

import pytest

from cozmo.models import ModelRegistry, ModelService, ModelUnavailableError
from cozmo.providers import create_provider
from cozmo.runtime.models import ModelRuntime, ResolvedModel
from cozmo.services.simple_llm import SimpleLLM


# ── helpers ─────────────────────────────────────────────────────────────

def _registry(*models: str) -> ModelRegistry:
    from cozmo.providers import ModelInfo
    reg = ModelRegistry()
    reg.update("ollama", [ModelInfo(name=m, provider="ollama") for m in models])
    return reg


def _config(model: str) -> dict:
    return {
        "llm": {"workloads": {"general": {"model": model}}},
        "providers": {"default": "ollama",
                      "ollama": {"url": "http://localhost:11434"}},
    }


class _RecorderProvider:
    """Fake provider that records the model identity it was given."""

    def __init__(self, model):
        self.model = model
        self.chat_model = object()
        self.bound = object()

    def get_chat_model(self, temperature=0.0):
        return self.chat_model

    def bind_tools(self, tools, temperature=0.0):
        return self.bound


# ── Test A — selected model identity is preserved ────────────────────────

def test_resolved_model_is_immutable_handoff():
    """ResolvedModel is frozen and carries only construction/use info."""
    import dataclasses

    assert dataclasses.is_dataclass(ResolvedModel)
    assert ResolvedModel.__dataclass_params__.frozen is True

    resolved = ResolvedModel(provider="ollama", model="qwen3:8b", config={"url": "x"})
    with pytest.raises(dataclasses.FrozenInstanceError):
        resolved.model = "other"

    assert resolved.model == "qwen3:8b"  # verbatim


def test_resolved_model_supports_tools_default_true():
    """supports_tools is a descriptive capability that defaults to True and is
    never used for selection/substitution."""
    assert ResolvedModel(provider="ollama", model="m").supports_tools is True
    assert ResolvedModel(
        provider="ollama", model="m", supports_tools=False
    ).supports_tools is False


def test_resolved_model_has_no_selection_state():
    """ResolvedModel must never carry recommendation/candidate/fallback state."""
    fields = {f.name for f in ResolvedModel.__dataclass_fields__.values()}
    assert fields == {"provider", "model", "config", "supports_tools"}
    assert not {"candidates", "recommendation", "fallback", "hardware", "vram"} & fields


def test_runtime_preserves_selected_model_identity():
    """The verbatim selected model string must reach the provider unchanged."""
    captured = {}

    def fake_factory(provider, model, cfg):
        captured.update(provider=provider, model=model, cfg=cfg)
        return _RecorderProvider(model)

    runtime = ModelRuntime(provider_factory=fake_factory)
    resolved = ResolvedModel(
        provider="ollama",
        model="qwen3:8b",
        config={"url": "http://localhost:11434"},
    )

    client = runtime.create_chat_model(resolved, temperature=0.3)

    assert captured["provider"] == "ollama"
    assert captured["model"] == "qwen3:8b"      # verbatim — no transformation
    assert client is not None


def test_modelservice_client_forwards_model_verbatim():
    """End-to-end: llm.workloads.general.model='qwen3:8b' arrives at the
    provider exactly as 'qwen3:8b'."""
    captured = {}
    runtime = ModelRuntime(provider_factory=lambda p, m, c: _RecorderProvider(m))
    ms = ModelService(_config("qwen3:8b"), _registry("qwen3:8b"), runtime=runtime)

    ms.client("general")

    assert captured == {}
    # ModelRuntime cache: provider received the verbatim identity
    assert len(runtime._providers) == 1
    key = next(iter(runtime._providers))
    assert key == "ollama:qwen3:8b"


def test_modelservice_client_forwards_model_verbatim_factory():
    """The factory sees exactly the selected model."""

    class _RecorderRuntime:
        def __init__(self):
            self.seen = []

        def create_chat_model(self, resolved, temperature=0.0):
            self.seen.append((resolved.provider, resolved.model, resolved.config))
            return object()

        def bind_tools(self, resolved, tools, temperature=0.0):
            self.seen.append(("bind", resolved.provider, resolved.model))
            return object()

        def clear(self):
            pass

    rt = _RecorderRuntime()
    ms = ModelService(_config("qwen3:8b"), _registry("qwen3:8b"), runtime=rt)

    ms.client("general", temperature=0.2)
    ms.bind_model("qwen3:8b", ["calc"], temperature=0.0)

    assert rt.seen[0] == ("ollama", "qwen3:8b", {"url": "http://localhost:11434"})
    assert rt.seen[1] == ("bind", "ollama", "qwen3:8b")


def test_modelservice_client_for_model_verbatim():
    """client_for_model derives provider from the registry and keeps identity."""
    captured = {}
    runtime = ModelRuntime(provider_factory=lambda p, m, c: _RecorderProvider(m))
    ms = ModelService(_config(""), _registry("qwen3:8b"), runtime=runtime)

    client = ms.client_for_model("qwen3:8b")

    assert next(iter(runtime._providers)) == "ollama:qwen3:8b"
    assert client is not None


# ── Test B — empty selection fails BEFORE LangChain construction ─────────

def test_runtime_empty_selection_never_constructs(monkeypatch):
    """ModelRuntime must reject an empty model before touching any provider."""
    constructed = {"factory": 0}

    def fake_factory(provider, model, cfg):
        constructed["factory"] += 1
        raise AssertionError("provider factory must not run for empty selection")

    runtime = ModelRuntime(provider_factory=fake_factory)
    with pytest.raises(ModelUnavailableError):
        runtime.create_chat_model(ResolvedModel(provider="ollama", model="", config={}))

    assert constructed["factory"] == 0
    assert runtime._providers == {}


def test_empty_selection_never_constructs_langchain_model(monkeypatch):
    """llm.workloads.general.model='' → ModelUnavailableError, and neither
    ChatOllama nor ChatOpenAI is constructed."""
    langchain_constructed = {"ollama": 0, "openai": 0}

    class _ChatOllama:
        def __init__(self, **kwargs):
            langchain_constructed["ollama"] += 1

    class _ChatOpenAI:
        def __init__(self, **kwargs):
            langchain_constructed["openai"] += 1

    monkeypatch.setattr("langchain_ollama.ChatOllama", _ChatOllama)
    monkeypatch.setattr("langchain_openai.ChatOpenAI", _ChatOpenAI)

    ms = ModelService(_config(""), _registry("qwen3:8b"))

    with pytest.raises(ModelUnavailableError) as exc_info:
        ms.client("general")

    assert isinstance(exc_info.value, ModelUnavailableError)
    assert langchain_constructed == {"ollama": 0, "openai": 0}


def test_empty_selection_raises_no_fallback_no_substitution(monkeypatch):
    """An empty selection must NOT trigger discovery, fallback, or a
    substitute — it raises and stops."""
    langchain_constructed = {"ollama": 0, "openai": 0}

    class _ChatOllama:
        def __init__(self, **kwargs):
            langchain_constructed["ollama"] += 1

    class _ChatOpenAI:
        def __init__(self, **kwargs):
            langchain_constructed["openai"] += 1

    monkeypatch.setattr("langchain_ollama.ChatOllama", _ChatOllama)
    monkeypatch.setattr("langchain_openai.ChatOpenAI", _ChatOpenAI)

    ms = ModelService(_config(""), _registry("qwen3:8b", "llama3.2:3b"))

    with pytest.raises(ModelUnavailableError):
        ms.client("general")

    # no substitute was built even though other models exist in the registry
    assert langchain_constructed == {"ollama": 0, "openai": 0}


def test_runtime_module_has_no_recommendation_imports():
    """ModelRuntime must not perform or reference recommendation."""
    import inspect

    src = inspect.getsource(ModelRuntime)
    for forbidden in ("recommend", "ModelRecommendationEngine", "apply_selection"):
        assert forbidden not in src


# ── Test C — provider delegation ─────────────────────────────────────────

def test_runtime_delegates_to_provider_layer():
    """ModelRuntime routes through the provider's get_chat_model/bind_tools —
    it does not construct provider-specific LangChain models itself."""
    runtime = ModelRuntime(provider_factory=lambda p, m, c: _RecorderProvider(m))
    resolved = ResolvedModel(provider="ollama", model="m", config={})

    assert runtime.create_chat_model(resolved) is not None
    assert runtime.bind_tools(resolved, ["t"]) is not None


def test_runtime_default_factory_is_provider_boundary():
    """The default construction path is the canonical providers.create_provider."""
    from cozmo.providers import create_provider as canonical

    assert ModelRuntime()._provider_factory is canonical


def test_modelservice_constructs_only_through_provider_layer(monkeypatch):
    """Full chain: SimpleLLM → ModelService → ModelRuntime → create_provider
    → OllamaProvider → ChatOllama. Nothing constructs a model elsewhere."""
    captured = {}

    class _ChatOllama:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.model = kwargs.get("model")

        def invoke(self, prompt, **kwargs):
            return type("Result", (), {"content": f"ok:{self.model}"})()

    monkeypatch.setattr("langchain_ollama.ChatOllama", _ChatOllama)

    ms = ModelService(_config("qwen3:8b"), _registry("qwen3:8b"))
    llm = SimpleLLM(ms, workload="general")

    assert llm.invoke("hello") == "ok:qwen3:8b"
    assert captured["model"] == "qwen3:8b"


# ── Test D — SimpleLLM / general workload through the new boundary ───────

def test_simple_llm_general_through_model_runtime(monkeypatch):
    """SimpleLLM on the general workload resolves the configured model and
    constructs the LangChain client through ModelRuntime unchanged."""
    captured = {}

    class _ChatOllama:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def invoke(self, prompt, **kwargs):
            return type("Result", (), {"content": "hello back"})()

    monkeypatch.setattr("langchain_ollama.ChatOllama", _ChatOllama)

    ms = ModelService(_config("qwen3:8b"), _registry("qwen3:8b"))
    llm = SimpleLLM(ms, workload="general")

    assert llm.invoke("hi") == "hello back"
    assert captured["model"] == "qwen3:8b"
    assert captured["temperature"] == 0.0


def test_simple_llm_general_empty_selection_raises(monkeypatch):
    """Unset general workload now raises ModelUnavailableError at the runtime
    boundary instead of constructing a model with an empty name."""
    langchain_constructed = {"ollama": 0, "openai": 0}

    class _ChatOllama:
        def __init__(self, **kwargs):
            langchain_constructed["ollama"] += 1

    class _ChatOpenAI:
        def __init__(self, **kwargs):
            langchain_constructed["openai"] += 1

    monkeypatch.setattr("langchain_ollama.ChatOllama", _ChatOllama)
    monkeypatch.setattr("langchain_openai.ChatOpenAI", _ChatOpenAI)

    ms = ModelService(_config(""), _registry())
    llm = SimpleLLM(ms, workload="general")

    with pytest.raises(ModelUnavailableError):
        llm.invoke("anything")

    assert langchain_constructed == {"ollama": 0, "openai": 0}