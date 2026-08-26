"""Phase 7 Stage 3C — LangGraph research workflow tests.

Prove the research graph:
  - composes search→evaluate→synthesize→validate as explicit transitions
  - receives the model from Novi (state["model"]) and never resolves one
  - never reads/writes configuration
  - reuses sufficient pre-loop evidence instead of double-searching
  - respects the RetrievalCoordinator budget (single budget authority)
  - re-searches (bounded) on gaps / insufficient evidence / validation
  - produces a final answer
"""

import pytest

from novi.graphs import ResearchGraph
from novi.runtime.evidence import EvidenceBundle, RetrievalQuality
from novi.runtime.retrieval_coordinator import RetrievalBudget, RetrievalCoordinator


class _StubModel:
    """Model stub that returns a canned answer; records the messages it saw."""

    def __init__(self, answer="answer-with-key", name="stub"):
        self.answer = answer
        self.name = name
        self.seen = []

    def invoke(self, msgs):
        self.seen.append(msgs)
        return type("R", (), {"content": self.answer})()


def _bundle(quality: RetrievalQuality, text="grounding evidence text for key") -> EvidenceBundle:
    return EvidenceBundle(
        query="q",
        merged_text=text if quality is not RetrievalQuality.EMPTY else "",
        source_count=1 if quality is not RetrievalQuality.EMPTY else 0,
        quality=quality,
    )


def _state(**kw):
    state = {
        "user_input": "what is the key",
        "analysis": None,
        "retrieval_plan": None,
        "grounding_text": "",
        "quality": "",
        "query": "what is the key",
        "search_attempts": 0,
        "max_search_attempts": 2,
        "system_prompt": "system",
        "plan_step_index": 0,
    }
    state.update(kw)
    return state


def _code_references(src: str, names: list[str]) -> list[str]:
    """Find real code references (Name/Attribute nodes), ignoring docstrings
    and comments where forbidden words legitimately appear as prose."""
    import ast

    tree = ast.parse(src)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id in names:
                found.append(node.id)
        elif isinstance(node, ast.Attribute):
            parts = []
            n = node
            while isinstance(n, ast.Attribute):
                parts.append(n.attr)
                n = n.value
            if isinstance(n, ast.Name):
                parts.append(n.id)
            parts.reverse()
            dotted = ".".join(parts)
            if dotted in names:
                found.append(dotted)
    return found


# ── construction ─────────────────────────────────────────────────────────

def test_graph_constructs():
    g = ResearchGraph(model=_StubModel())
    assert g.max_search_attempts == 2
    assert g.run(_state())["answer"] == "answer-with-key"


def test_graph_rejects_zero_search_budget():
    with pytest.raises(ValueError):
        ResearchGraph(model=_StubModel(), max_search_attempts=0)


# ── model injection ──────────────────────────────────────────────────────

def test_graph_uses_state_model_over_construction_model():
    construction = _StubModel(answer="construction")
    runtime = _StubModel(answer="runtime", name="runtime")
    g = ResearchGraph(model=construction)

    result = g.run(_state(model=runtime))

    assert result["answer"] == "runtime"
    assert runtime.seen, "state-provided model must be invoked"


def test_graph_never_resolves_model():
    """The graph module must not import any model-selection authority."""
    import inspect
    import novi.graphs.research_graph as mod

    src = inspect.getsource(mod)
    forbidden = (
        "ModelService", "ModelSelector", "ModelRecommendationEngine",
        "recommend", "apply_selection", "create_provider",
        "configuration.resolver", "llm.workloads",
    )
    found = _code_references(src, list(forbidden))
    assert not found, f"graph references forbidden authority: {found}"


def test_graph_state_has_no_configuration_or_checkpoint():
    """Graph state carries only per-run workflow fields."""
    import inspect
    from novi.graphs import state as st

    src = inspect.getsource(st)
    forbidden = ("checkpointer", "checkpoint", "llm.workloads",
                 "apply_selection", "config")
    found = _code_references(src, list(forbidden))
    assert not found, f"graph state carries forbidden field: {found}"


# ── search / evaluate loop ───────────────────────────────────────────────

def test_sufficient_search_produces_answer():
    seen = []

    def search(query):
        seen.append(query)
        return _bundle(RetrievalQuality.SUFFICIENT)

    g = ResearchGraph(model=_StubModel(), search=search)
    result = g.run(_state())

    assert result["answer"] == "answer-with-key"
    assert len(seen) == 1
    assert result["quality"] == RetrievalQuality.SUFFICIENT.value
    assert result["validation"] == "sufficient"


def test_reuses_sufficient_preloop_evidence():
    """Pre-loop SUFFICIENT grounding must not trigger a second search."""

    def search(query):
        raise AssertionError("must not re-search when pre-loop evidence is sufficient")

    g = ResearchGraph(model=_StubModel(), search=search)
    result = g.run(_state(
        grounding_text="pre-loop evidence",
        quality=RetrievalQuality.SUFFICIENT.value,
    ))

    assert result["answer"] == "answer-with-key"
    assert result["search_attempts"] == 0


def test_insufficient_evidence_triggers_search():
    """Empty pre-loop evidence → search node runs once → answer."""
    seen = []

    def search(query):
        seen.append(query)
        return _bundle(RetrievalQuality.SUFFICIENT)

    g = ResearchGraph(model=_StubModel(), search=search)
    result = g.run(_state(grounding_text="", quality=""))

    assert result["answer"] == "answer-with-key"
    assert len(seen) == 1
    assert result["search_attempts"] == 1


def test_weak_evidence_reroutes_to_search():
    """Weak first search → gaps detected → second search → answer."""
    calls = {"n": 0}

    def search(query):
        calls["n"] += 1
        if calls["n"] == 1:
            return _bundle(RetrievalQuality.WEAK, text="")
        return _bundle(RetrievalQuality.SUFFICIENT)

    g = ResearchGraph(model=_StubModel(), search=search)
    result = g.run(_state())

    assert calls["n"] == 2
    assert result["search_attempts"] == 2
    assert result["answer"] == "answer-with-key"


def test_search_loop_bounded_by_max_attempts():
    """Persistently weak results never loop unboundedly."""
    calls = {"n": 0}

    def search(query):
        calls["n"] += 1
        return _bundle(RetrievalQuality.WEAK, text="")

    g = ResearchGraph(model=_StubModel(), search=search, max_search_attempts=3)
    result = g.run(_state())

    assert calls["n"] == 3  # initial + 2 bounded re-searches, then synthesize
    assert result["search_attempts"] == 3


def test_search_respects_coordinator_budget():
    """Exhausted budget → search node must not fire."""
    coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=0))

    def search(query):
        raise AssertionError("search must not run with exhausted budget")

    g = ResearchGraph(model=_StubModel(), search=search)
    result = g.run(_state(coordinator=coord))

    assert result["search_attempts"] == 0


# ── validation ───────────────────────────────────────────────────────────

def test_validation_insufficient_routes_back_to_search():
    """Model answers without incorporating relevant evidence → re-search."""
    calls = {"n": 0}

    def search(query):
        calls["n"] += 1
        return _bundle(RetrievalQuality.SUFFICIENT)

    g = ResearchGraph(
        model=_StubModel(answer="unrelated answer entirely"),
        search=search,
    )
    result = g.run(_state())

    # evaluate: sufficient → synthesize; validate: answer missed key terms →
    # re-search once (bounded) → synthesize again.
    assert calls["n"] == 2
    assert result["validation"] == "insufficient"


def test_validation_insufficient_bounded():
    """Even with always-insufficient validation, re-search stays bounded."""
    calls = {"n": 0}

    def search(query):
        calls["n"] += 1
        return _bundle(RetrievalQuality.SUFFICIENT)

    g = ResearchGraph(
        model=_StubModel(answer="unrelated answer entirely"),
        search=search,
        max_search_attempts=2,
    )
    result = g.run(_state())

    assert calls["n"] == 2
    assert result["search_attempts"] == 2
    assert result["validation"] == "insufficient"


# ── runtime integration ──────────────────────────────────────────────────

def test_runtime_research_intent_goes_through_graph():
    """When wired, research intent executes through the graph and the graph
    receives the runnable the runtime bound (state["model"])."""
    from types import SimpleNamespace

    from novi.runtime.execution_context import ExecutionContext
    from novi.runtime.runtime import NoviRuntime

    invoked = {"model": None}

    class _InvokeRunnable:
        def invoke(self, msgs):
            invoked["model"] = self
            return type("R", (), {"content": "graph answer"})

    class _ModelService:
        def resolve(self, workload):
            return ("ollama", "m1")

        def bind_model(self, model_name, tools, temperature=0.0):
            return _InvokeRunnable()

        def client_for_model(self, model_name, temperature=0.0):
            return _InvokeRunnable()

    analysis = SimpleNamespace(
        intent=SimpleNamespace(value="research"),
        evidence=SimpleNamespace(signals=[], confidence=1.0, needs_memory=False),
        complexity=SimpleNamespace(score=1, plan_level=0, max_steps=3),
        capabilities=["research"],
        strategy=SimpleNamespace(value="research"),
        grounding=SimpleNamespace(
            needs_grounding=True, confidence=0.8, source="heuristic", reason="test"),
        retrieval_plan=None,
    )

    graph = ResearchGraph()
    rt = NoviRuntime(model_service=_ModelService(), research_graph=graph,
                      cfg={"runtime": {"temperature": 0.2}})

    # Stub the retrieval pipeline so the test never hits the network.
    def fake_search(query, trace=None):
        return _bundle(RetrievalQuality.SUFFICIENT)

    rt.retrieval_executor.execute_search = fake_search

    ctx = ExecutionContext(user_input="what is the key")
    ctx.analysis = analysis
    ctx.allowed_tools = ["web_search", "web_fetch", "calculator", "search_knowledge"]

    tokens = []
    for kind, *rest in rt.run_stream(context=ctx):
        if kind == "token":
            tokens.append(rest[0])

    assert invoked["model"] is not None, "graph must receive the bound runnable"
    assert "".join(tokens) == "graph answer"

# ── live re-synthesis contract ───────────────────────────────────────────────


class _RChunk:
    def __init__(self, content="", reasoning=""):
        self.content = content
        self.additional_kwargs = {"reasoning_content": reasoning} if reasoning else {}

    def __add__(self, other):
        merged = (self.additional_kwargs.get("reasoning_content", "")
                  + other.additional_kwargs.get("reasoning_content", ""))
        return _RChunk(self.content + other.content, merged)


class _StreamSynth:
    """Model whose synthesis passes stream scripted chunk lists."""

    def __init__(self, passes):
        self.passes = [list(p) for p in passes]

    def stream(self, msgs):
        if not self.passes:
            raise AssertionError("streamed more passes than scripted")
        return iter(self.passes.pop(0))


def test_resynthesis_emits_answer_reset_before_second_pass():
    """validate(insufficient) -> re-search -> synthesize must tell the UI to
    CLEAR the first streamed answer, or the second pass doubles up on screen
    appended to the first."""
    calls = {"n": 0}

    def search(query):
        calls["n"] += 1
        return _bundle(RetrievalQuality.SUFFICIENT)

    g = ResearchGraph(
        model=_StreamSynth([
            [_RChunk("unrelated answer entirely")],
            [_RChunk("better second answer")],
        ]),
        search=search,
    )
    live: list = []
    state = _state(model=g._model if hasattr(g, "_model") else None)
    # state model wins over construction model; hand the streaming fake in.
    state["model"] = _StreamSynth([
        [_RChunk("unrelated answer entirely")],
        [_RChunk("better second answer")],
    ])
    state["emit"] = live.append
    out = g.run(state)

    kinds = [i[0] for i in live]
    assert kinds.count("token") >= 2, "both passes stream tokens"
    assert kinds.count("answer_reset") == 1
    first_token = kinds.index("token")
    reset_idx = kinds.index("answer_reset")
    assert first_token < reset_idx < kinds.index(
        "token", first_token + 1), "reset sits between pass 1 and pass 2 tokens"
    assert calls["n"] == 2
    assert out["answer"] == "better second answer"


def test_synthesis_failure_after_partial_stream_resets_answer():
    """A synthesis pass that dies mid-stream must clear the partial answer so
    the error bubble replaces garbage instead of stacking on it."""
    class _DyingStream:
        def stream(self, msgs):
            yield _RChunk("partial ans")
            raise RuntimeError("ollama exploded")

    def search(query):
        return _bundle(RetrievalQuality.SUFFICIENT)

    g = ResearchGraph(search=search)
    live: list = []
    state = _state(model=_DyingStream())
    state["emit"] = live.append
    out = g.run(state)

    kinds = [i[0] for i in live]
    assert "token" in kinds
    assert kinds.count("answer_reset") == 1
    assert kinds.index("token") < kinds.index("answer_reset")
    assert out["answer"] == ""
