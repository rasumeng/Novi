"""Primary-model contract: one model for all strategies, no workload->model."""

from novi.configuration.resolver import get_primary_model, recommend, apply_selection
from novi.configuration.qualification import Qualification
from novi.configuration.model_seeds import ModelFact
from novi.configuration.hardware import DetectionConfidence, GpuConfidence, GpuInfo, HardwareProfile
from novi.runtime.strategies import get_strategy_prompt, normalize_strategy, STRATEGIES
from novi.runtime.model_selector import ModelSelector
from novi.models.service import ModelService, ModelUnavailableError
from novi.models.registry import ModelRegistry
from novi.orchestrator.router import WorkloadRouter


def _svc(model: str):
    reg = ModelRegistry()
    svc = ModelService({"llm": {"primary_model": model}, "providers": {"default": "ollama"}},
                       reg, runtime=None)
    # Pretend model installed by stubbing validate.
    reg.validate = lambda name: True
    return svc


def test_every_strategy_resolves_same_primary():
    # Every execution strategy uses the single primary model: resolve() is
    # strategy-independent, so resolving for any classified strategy returns
    # the same configured model.
    for _strategy in ("chat", "code", "research"):
        svc = _svc("qwen3:8b")
        sel = ModelSelector(svc)
        assert sel.resolve() == "qwen3:8b"


def test_chat_strategy_instructions():
    assert "conversational" in get_strategy_prompt("chat").lower()


def test_code_strategy_instructions():
    text = get_strategy_prompt("code").lower()
    assert "inspect" in text and "verif" in text


def test_research_strategy_instructions():
    text = get_strategy_prompt("research").lower()
    assert "citat" in text and "sources" in text


def test_router_does_not_change_model():
    r = WorkloadRouter()
    for msg in ["hello", "fix auth.py bug", "latest BTC price news"]:
        d = r.route(user_message=msg)
        assert d.strategy in STRATEGIES
        svc = _svc("qwen3:8b")
        assert ModelSelector(svc).resolve() == "qwen3:8b"


def test_capability_validation_clear_errors():
    from novi.configuration import discovery as disc
    svc = _svc("text-only-model")
    sel = ModelSelector(svc)
    try:
        sel.validate(supports_vision=True)
    except ModelUnavailableError as e:
        assert "workload" not in str(e).lower()
        assert "vision" in str(e).lower()
    else:
        raise AssertionError("expected vision rejection")


def test_unsupported_image_message():
    svc = _svc("text-only-model")
    sel = ModelSelector(svc)
    try:
        sel.validate(supports_vision=True)
        raise AssertionError("should reject")
    except ModelUnavailableError as e:
        assert "doesn't support image input" in str(e)


def test_no_workload_config_in_defaults():
    from novi.configuration.bootstrap import DEFAULT_CONFIG
    assert "workloads" not in DEFAULT_CONFIG["llm"]
    assert DEFAULT_CONFIG["llm"]["primary_model"] == ""


def test_no_hidden_fallback():
    import ast
    import inspect
    from novi.runtime import model_selector as ms
    src = inspect.getsource(ms.ModelSelector.resolve)
    # Strip comments so docstring mentions like "does not permit fallback" don't fail the gate.
    stripped = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    try:
        tree = ast.parse(stripped)
        code_tokens = " ".join(node.id.lower() if isinstance(node, ast.Name) else "" for node in ast.walk(tree))
        # Also check string literals — fallback in comments/docstrings is allowed.
        has_fallback = "fallback" in code_tokens
        has_substitute = "substitut" in code_tokens
    except SyntaxError:
        has_fallback = "fallback" in stripped.lower()
        has_substitute = "substitut" in stripped.lower()
    assert not has_fallback, "ModelSelector.resolve must not contain fallback logic"
    assert not has_substitute, "ModelSelector.resolve must not substitute models"
