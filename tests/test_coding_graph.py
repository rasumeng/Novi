"""Phase 7 Stage 3D — LangGraph coding workflow tests.

Prove the coding graph:
  - composes understand→plan→implement→verify as explicit transitions
  - receives the model from Cozmo (state["model"]) and never resolves one
  - delegates implement attempts to the injected run_loop (the runtime's
    ReAct loop) and preserves its stream events for replay
  - re-implements (bounded) on empty / max_steps outcomes
  - never reads/writes configuration, never uses LangGraph persistence
  - produces a final answer and replays streamed tokens
"""

import pytest

from cozmo.graphs import CodingGraph


class _StubModel:
    """Model stub that returns a canned answer; records the messages it saw."""

    def __init__(self, answer="implemented-change", name="stub"):
        self.answer = answer
        self.name = name
        self.seen = []

    def invoke(self, msgs):
        self.seen.append(msgs)
        return type("R", (), {"content": self.answer})()


def _state(**kw):
    state = {
        "user_input": "add a logging helper",
        "analysis": None,
        "retrieval_plan": None,
        "system_prompt": "system",
        "plan_step_index": 0,
        "answer": "",
        "stop_reason": "",
        "attempt": 0,
        "max_attempts": 2,
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
    g = CodingGraph(model=_StubModel())
    assert g.max_attempts == 2
    assert g.run(_state())["answer"] == "implemented-change"


def test_graph_rejects_zero_attempt_budget():
    with pytest.raises(ValueError):
        CodingGraph(model=_StubModel(), max_attempts=0)


# ── model injection ──────────────────────────────────────────────────────

def test_graph_uses_state_model_over_construction_model():
    construction = _StubModel(answer="construction")
    runtime = _StubModel(answer="runtime", name="runtime")
    g = CodingGraph(model=construction)

    result = g.run(_state(model=runtime))

    assert result["answer"] == "runtime"
    assert runtime.seen, "state-provided model must be invoked"


def test_graph_never_resolves_model():
    """The graph module must not import any model-selection authority."""
    import inspect
    import cozmo.graphs.coding_graph as mod

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
    from cozmo.graphs import state as st

    src = inspect.getsource(st)
    forbidden = ("checkpointer", "checkpoint", "llm.workloads",
                 "apply_selection", "config")
    found = _code_references(src, list(forbidden))
    assert not found, f"graph state carries forbidden field: {found}"


# ── implement / verify loop ──────────────────────────────────────────────

def test_run_loop_delegation_preserves_events():
    """Implement delegates to the injected run_loop and preserves its stream
    events for the runtime to replay."""
    captured = {"calls": 0}

    def run_loop(state):
        captured["calls"] += 1
        return ([("token", "edited"), ("tool_call", "write_file", {})],
                "edited", "completed", True)

    g = CodingGraph(run_loop=run_loop)
    result = g.run(_state())

    assert captured["calls"] == 1
    assert result["answer"] == "edited"
    assert result["stop_reason"] == "completed"
    assert result["attempt"] == 1
    assert result["events"] == [("token", "edited"), ("tool_call", "write_file", {})]


def test_verify_retries_on_empty_answer_bounded():
    """Empty implement outcome → re-implement, bounded by max_attempts."""
    calls = {"n": 0}

    def run_loop(state):
        calls["n"] += 1
        return ([], "", "empty", False)

    g = CodingGraph(run_loop=run_loop, max_attempts=3)
    result = g.run(_state())

    assert calls["n"] == 3  # initial + 2 bounded re-implements, then END
    assert result["attempt"] == 3
    assert result["verify_note"] == "retry"


def test_verify_retries_on_max_steps_then_succeeds():
    """A max_steps cut-short attempt is retried; a later completed attempt
    ends the workflow."""
    calls = {"n": 0}

    def run_loop(state):
        calls["n"] += 1
        if calls["n"] == 1:
            return ([("token", "partial")], "partial", "max_steps", False)
        return ([("token", "done")], "done", "completed", True)

    g = CodingGraph(run_loop=run_loop, max_attempts=2)
    result = g.run(_state())

    assert calls["n"] == 2
    assert result["answer"] == "done"
    assert result["verify_note"] == "done"
    assert result["attempt"] == 2


def test_completed_implement_no_retry():
    calls = {"n": 0}

    def run_loop(state):
        calls["n"] += 1
        return ([("token", "fine")], "fine", "completed", True)

    g = CodingGraph(run_loop=run_loop)
    result = g.run(_state())

    assert calls["n"] == 1
    assert result["verify_note"] == "done"
    assert result["attempt"] == 1


# ── runtime integration ──────────────────────────────────────────────────

def test_runtime_coding_intent_goes_through_graph():
    """When wired, coding intent executes through the graph; the graph
    receives the runnable the runtime bound (state["model"]) and the runtime
    replays the loop's streamed tokens."""
    from types import SimpleNamespace

    from langchain_core.messages import AIMessage

    from cozmo.runtime.execution_context import ExecutionContext
    from cozmo.runtime.runtime import CozmoRuntime

    streamed = {"tokens": []}

    class _StreamRunnable:
        def stream(self, msgs):
            streamed["tokens"].append("patched")
            yield AIMessage(content="patched")

        def invoke(self, msgs):
            return AIMessage(content="patched")

    class _ModelService:
        def resolve(self, workload):
            return ("ollama", "m1")

        def bind_model(self, model_name, tools, temperature=0.0):
            return _StreamRunnable()

        def client_for_model(self, model_name, temperature=0.0):
            return _StreamRunnable()

    analysis = SimpleNamespace(
        intent=SimpleNamespace(value="coding"),
        evidence=SimpleNamespace(signals=[], confidence=1.0, needs_memory=False),
        complexity=SimpleNamespace(score=1, plan_level=0, max_steps=3),
        capabilities=["coding"],
        strategy=SimpleNamespace(value="coding"),
        grounding=SimpleNamespace(
            needs_grounding=False, confidence=0.8, source="heuristic", reason="test"),
        retrieval_plan=None,
    )

    graph = CodingGraph()
    rt = CozmoRuntime(model_service=_ModelService(), coding_graph=graph,
                      cfg={"runtime": {"temperature": 0.2}})

    ctx = ExecutionContext(user_input="add a logging helper")
    ctx.analysis = analysis
    ctx.allowed_tools = ["read_file", "write_file", "edit_file", "glob",
                         "grep", "bash", "run_command", "list_directory"]

    tokens = []
    for kind, *rest in rt.run_stream(context=ctx):
        if kind == "token":
            tokens.append(rest[0])

    assert streamed["tokens"], "graph must receive the bound runnable via run_loop"
    assert "".join(tokens) == "patched"