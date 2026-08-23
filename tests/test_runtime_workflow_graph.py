"""LangGraph dual-path runtime migration — parity + regression tests.

Two layers:

1. Graph-level (pure fakes): the RuntimeWorkflowGraph must reproduce the
   legacy ReAct path's observable contract — event tuple vocabulary/order,
   exact-call dedup message, max-steps wording, ToolMessage accumulation,
   context snapshot semantics, cancellation, and ModelUnavailableError
   propagation (never swallowed, never substituted).
2. Runtime integration (hermetic CozmoRuntime): the opt-in langgraph engine
   replays graph events on run_stream, keeps the shared tail (trace finalize,
   Brain observation) intact, and stays byte-inert for the legacy default.

Architecture guards pin the locked boundary: graphs import no storage, never
instantiate ToolExecutor/ModelSelector, and own no persistence.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from cozmo.graphs import RuntimeWorkflowGraph
from cozmo.models import ModelUnavailableError


# ── fakes ────────────────────────────────────────────────────────────────────


class ScriptedModel:
    """Returns scripted AIMessages in order; records every invocation."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, msgs):
        self.calls.append(list(msgs))
        if not self.responses:
            raise AssertionError("model invoked more times than scripted")
        r = self.responses.pop(0)
        return r() if callable(r) else r


def _ai(content="", tool_calls=None):
    return AIMessage(content=content, tool_calls=tool_calls or [])


def _tc(name="search", args=None, call_id="call-1"):
    return {"name": name, "args": args or {}, "id": call_id}


def _state(**kw):
    s = {
        "user_input": "hello",
        "system_prompt": "You are Cozmo.",
        "seed_messages": [HumanMessage(content="earlier turn"),
                          AIMessage(content="earlier answer")],
        "messages": [],
        "attempt": 0,
        "events": [],
        "observations": [],
    }
    s.update(kw)
    return s


def _snapshot(grounding="ground", memory="mem ctx", project="proj ctx"):
    return {
        "grounding_text": grounding,
        "memory_context": memory,
        "project_context": project,
        "evidence_context": None,
        "quality": "sufficient",
    }


# ── 1. no-tool conversation ─────────────────────────────────────────────────


def test_plain_conversation_answers_without_tools():
    model = ScriptedModel([_ai("Hello there!")])
    g = RuntimeWorkflowGraph()
    out = g.run(_state(model=model))
    assert out["answer"] == "Hello there!"
    assert out["completion_reason"] == "completed"
    assert out["pending_tool_calls"] == []
    # exactly one model invocation; messages end with the AI reply
    assert len(model.calls) == 1
    kinds = [type(m).__name__ for m in model.calls[0]]
    assert kinds[0] == "SystemMessage"
    assert kinds[-1] == "HumanMessage"


def test_seed_messages_preserve_conversation_continuation():
    model = ScriptedModel([_ai("ok")])
    g = RuntimeWorkflowGraph()
    out = g.run(_state(model=model))
    first_call = model.calls[0]
    assert first_call[0].content == "You are Cozmo."
    assert isinstance(first_call[1], HumanMessage)
    assert first_call[1].content == "earlier turn"
    assert isinstance(first_call[-1], HumanMessage)
    assert first_call[-1].content == "hello"
    assert any(isinstance(m, AIMessage) and m.content == "earlier answer"
               for m in first_call), "history AIMessage present"


# ── 2. retrieved-context snapshot semantics ─────────────────────────────────


def test_retrieve_node_snapshots_runtime_context_verbatim():
    snap = _snapshot()
    seen = {}
    model = ScriptedModel([_ai("ans")])

    def prepare():
        seen.update(snap)
        return dict(snap)

    g = RuntimeWorkflowGraph()
    out = g.run(_state(model=model, prepare_context=prepare))
    assert out["grounding_text"] == "ground"
    assert out["memory_context"] == "mem ctx"
    assert out["project_context"] == "proj ctx"
    assert out["quality"] == "sufficient"
    assert seen == snap, "snapshot consulted exactly once, unmodified"


def test_evidence_context_flows_through_state():
    ev = SimpleNamespace(query="q", fallback=False)
    model = ScriptedModel([_ai("ans")])
    g = RuntimeWorkflowGraph()
    out = g.run(_state(model=model,
                       prepare_context=lambda: {**_snapshot(),
                                                "evidence_context": ev}))
    assert out["evidence_context"] is ev


# ── 3. tool round-trip: act/reason loop ──────────────────────────────────────


def test_single_tool_call_executes_and_loops_back_to_reason():
    executed = []

    def execute_tool(name, args, step_idx):
        executed.append((name, args, step_idx))
        return "42", "", True

    model = ScriptedModel([
        _ai("", tool_calls=[_tc("get_answer", {"q": "meaning"}, "call-a")]),
        _ai("The answer is 42."),
    ])
    g = RuntimeWorkflowGraph()
    out = g.run(_state(model=model, execute_tool=execute_tool))

    assert executed == [("get_answer", {"q": "meaning"}, 1)]
    assert out["answer"] == "The answer is 42."
    assert out["completion_reason"] == "completed"

    kinds = [type(m).__name__ for m in model.calls[1]]
    assert "ToolMessage" in kinds, "tool output fed back as ToolMessage"
    tm = [m for m in model.calls[1] if isinstance(m, ToolMessage)][0]
    assert tm.content == "42"
    assert tm.tool_call_id == "call-a"


def test_event_stream_matches_legacy_vocabulary_and_order():
    def execute_tool(name, args, step_idx):
        return "out", "diff-text", True

    model = ScriptedModel([
        _ai("", tool_calls=[_tc("search", {"q": "x"})]),
        _ai("done"),
    ])
    g = RuntimeWorkflowGraph()
    out = g.run(_state(model=model, execute_tool=execute_tool))

    kinds = [ev[0] for ev in out["events"]]
    assert kinds == ["thinking", "tool_call", "tool_result"]
    thinking, call_ev, result_ev = out["events"]
    assert thinking[1] == "Running: search"
    assert call_ev[1] == "search" and call_ev[2] == {"q": "x"}
    assert result_ev[1] == "search" and result_ev[2] == "out"
    assert result_ev[4] == "diff-text"


def test_multiple_rounds_bounded_by_max_steps():
    rounds = {"n": 0}

    def execute_tool(name, args, step_idx):
        rounds["n"] += 1
        return f"out{rounds['n']}", "", True

    def always_tool():
        return _ai("", tool_calls=[_tc(f"t{rounds['n'] + 1}", {}, f"c{rounds['n']}")])

    model = ScriptedModel([always_tool] * 5)
    g = RuntimeWorkflowGraph(max_steps=3)
    out = g.run(_state(model=model, execute_tool=execute_tool))

    assert rounds["n"] == 3, "exactly max_steps executions"
    assert out["completion_reason"] == "max_steps"
    assert out["answer"].startswith("I ran out of steps")
    token_events = [ev for ev in out["events"] if ev[0] == "token"]
    assert token_events and token_events[-1][1] == out["answer"]


def test_repeated_exact_call_gets_legacy_dedup_message():
    calls = []

    def execute_tool(name, args, step_idx):
        calls.append((name, dict(args)))
        return "fresh", "", True

    tc = _tc("lookup", {"k": "v"}, "c1")
    model = ScriptedModel([
        _ai("", tool_calls=[dict(tc)]),
        _ai("", tool_calls=[dict(tc)]),  # identical name+args again
        _ai("finished"),
    ])
    g = RuntimeWorkflowGraph(max_steps=5)
    out = g.run(_state(model=model, execute_tool=execute_tool))

    assert calls == [("lookup", {"k": "v"})], "duplicate not re-executed"
    dup_results = [ev for ev in out["events"]
                   if ev[0] == "tool_result" and "already made" in str(ev[2])]
    assert dup_results, "legacy dedup message emitted"


def test_observations_recorded_per_execution():
    def execute_tool(name, args, step_idx):
        return "res", "", True

    model = ScriptedModel([
        _ai("", tool_calls=[_tc("a", {}, "c1"), _tc("b", {}, "c2")]),
        _ai("ok"),
    ])
    g = RuntimeWorkflowGraph()
    out = g.run(_state(model=model, execute_tool=execute_tool))
    assert [(o["name"], o["output"]) for o in out["observations"]] == \
        [("a", "res"), ("b", "res")]
    msgs = model.calls[1]
    tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in tool_msgs] == ["c1", "c2"], \
        "one ToolMessage per call, ids preserved"


# ── 4. failure / degradation / cancellation ─────────────────────────────────


def test_model_unavailable_error_propagates_untouched():
    class Unavailable:
        def invoke(self, msgs):
            raise ModelUnavailableError("general", None, [])

    g = RuntimeWorkflowGraph()
    with pytest.raises(ModelUnavailableError):
        g.run(_state(model=Unavailable()))


def test_arbitrary_model_errors_propagate_no_silent_robustness():
    class Boom:
        def invoke(self, msgs):
            raise RuntimeError("model exploded")

    g = RuntimeWorkflowGraph()
    with pytest.raises(RuntimeError, match="model exploded"):
        g.run(_state(model=Boom()))


def test_tool_executor_failure_semantics_pass_through_as_output():
    def failing_tool(name, args, step_idx):
        return "Error: permission denied", "", False

    model = ScriptedModel([
        _ai("", tool_calls=[_tc("write_file", {})]),
        _ai("I could not write the file."),
    ])
    g = RuntimeWorkflowGraph()
    out = g.run(_state(model=model, execute_tool=failing_tool))
    assert out["answer"] == "I could not write the file.", \
        "tool errors are observations, never exceptions"


def test_cancelled_run_executes_nothing():
    model = ScriptedModel([])
    tools = []
    g = RuntimeWorkflowGraph()
    out = g.run(_state(model=model, should_stop=lambda: True,
                       execute_tool=lambda *a: tools.append(a)))
    assert out["completion_reason"] == "stopped"
    assert model.calls == [] and tools == []


def test_reflect_called_once_on_success_only():
    reflects = []
    g = RuntimeWorkflowGraph()

    ok = g.run(_state(model=ScriptedModel([_ai("done")]),
                      reflect=lambda: reflects.append("r")))
    assert reflects == ["r"]
    assert ok["completion_reason"] == "completed"

    capped = RuntimeWorkflowGraph(max_steps=3).run(_state(
        model=ScriptedModel([lambda: _ai("", tool_calls=[_tc("x", {})])] * 5),
        execute_tool=lambda n, a, i: ("o", "", True),
        reflect=lambda: reflects.append("r"),
    ))
    assert capped["completion_reason"] == "max_steps"
    assert reflects == ["r"], "no consolidation after failed/capped runs"


def test_processing_is_deterministic_across_runs():
    def make():
        model = ScriptedModel([
            _ai("", tool_calls=[_tc("s", {"q": 1})]),
            _ai("final"),
        ])
        g = RuntimeWorkflowGraph()
        return g.run(_state(model=model,
                            execute_tool=lambda n, a, i: ("o", "d", True)))

    a, b = make(), make()
    assert [e[:4] for e in a["events"]] == [e[:4] for e in b["events"]]
    assert a["answer"] == b["answer"] == "final"
    assert a["completion_reason"] == b["completion_reason"]


def test_empty_model_output_falls_back_with_legacy_wording():
    model = ScriptedModel([_ai("")])
    g = RuntimeWorkflowGraph()
    out = g.run(_state(model=model))
    assert "(no response — the model returned empty output" in out["answer"]


# ── 5. runtime integration (opt-in engine, hermetic) ─────────────────────────


def _runtime_ctx():
    from cozmo.runtime.execution_context import ExecutionContext

    ctx = ExecutionContext(user_input="hello")
    ctx.analysis = SimpleNamespace(
        intent=SimpleNamespace(value="conversation"),
        evidence=SimpleNamespace(signals=[], confidence=1.0, needs_memory=False),
        complexity=SimpleNamespace(score=1, plan_level=0, max_steps=4),
        capabilities=["conversation"],
        strategy=SimpleNamespace(value="respond"),
        grounding=SimpleNamespace(needs_grounding=False, confidence=0.0,
                                  source="heuristic", reason="test"),
        retrieval_plan=None,
    )
    return ctx


class _MS:
    def resolve(self, workload):
        return ("ollama", "parity-model")

    def bind_model(self, name, tools, temperature=0.0):
        return _bound

    def client_for_model(self, name, temperature=0.0):
        return _bound


_bound = ScriptedModel([_ai("graph says hi")])


def test_langgraph_engine_streams_answer_and_finalizes_trace():
    from cozmo.runtime.runtime import CozmoRuntime

    rt = CozmoRuntime(model_service=_MS(),
                      runtime_graph=RuntimeWorkflowGraph(),
                      workflow_engine="langgraph")
    ctx = _runtime_ctx()
    events = list(rt.run_stream(context=ctx))
    tokens = "".join(e[1] for e in events if e[0] == "token")
    assert tokens == "graph says hi"
    assert ctx.trace.stop_reason == "completed"
    assert ctx.grounding_text == ""  # snapshot ran; nothing fabricated
    assert ctx.evidence_context is None


def test_langgraph_engine_replays_tool_events_in_order(monkeypatch):
    from cozmo.runtime.runtime import CozmoRuntime

    bound = ScriptedModel([
        _ai("", tool_calls=[_tc("echo", {"v": 1}, "c9")]),
        _ai("handled"),
    ])

    class MS:
        def resolve(self, workload):
            return ("ollama", "m")

        def bind_model(self, name, tools, temperature=0.0):
            return bound

        def client_for_model(self, name, temperature=0.0):
            return bound

    rt = CozmoRuntime(model_service=MS(),
                      runtime_graph=RuntimeWorkflowGraph(max_steps=4),
                      workflow_engine="langgraph")
    ctx = _runtime_ctx()
    executed = []

    def fake_execute(name, args, coordinator=None, step_idx=None, trace=None):
        executed.append((name, args))
        return SimpleNamespace(output="echo-out", diff="", success=True)

    rt.tool_executor.execute = fake_execute
    events = list(rt.run_stream(context=_runtime_ctx()))
    kinds = [e[0] for e in events]
    assert "tool_call" in kinds and "tool_result" in kinds
    result_ev = [e for e in events if e[0] == "tool_result"][0]
    assert result_ev[2] == "echo-out"
    assert executed == [("echo", {"v": 1})], "ToolExecutor remained the gate"
    assert "".join(e[1] for e in events if e[0] == "token") == "handled"


def test_legacy_default_ignores_graph_entirely():
    """workflow_engine='legacy' (default): graph present but never used."""
    from cozmo.runtime.runtime import CozmoRuntime

    unused = RuntimeWorkflowGraph()
    monkey_calls = []
    orig_run = unused.run

    def spy_run(state):
        monkey_calls.append(state)
        return orig_run(state)

    unused.run = spy_run
    rt = CozmoRuntime(model_service=_MS(), runtime_graph=unused)
    assert rt._workflow_engine == "legacy"
    list(rt.run_stream(context=_runtime_ctx()))
    assert monkey_calls == []


# ── 6. architecture guards ──────────────────────────────────────────────────


def test_graph_package_import_purity():
    root = Path(__file__).resolve().parent.parent
    forbidden = (
        "lancedb", "sqlite3", "LanceStore", "VectorStore",
        "RelationshipStore", "ConversationStore", "MarkdownStore",
        "KnowledgeIndex", "ModelSelector", "ToolExecutor(",
        "SqliteSaver", "MemorySaver",
    )
    graphs_dir = root / "cozmo" / "graphs"
    for pyfile in graphs_dir.rglob("*.py"):
        source = pyfile.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        # Strip docstrings properly (ast-aware) so boundary docs mentioning a
        # forbidden concept don't false-positive; only real code references count.
        code_only = source
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc and doc in code_only:
                    code_only = code_only.replace(doc, "")
        for token in forbidden:
            assert not any(token in mod for mod in imported), \
                f"{pyfile.name}: forbidden graph dependency '{token}'"
            assert token not in code_only, \
                f"{pyfile.name}: references '{token}' — boundary violation"


def test_workflow_graph_has_no_langgraph_checkpointer():
    """Checkpoint/resumability decision pinned: Cozmo JobStore/ConversationStore
    remain the only durable authorities — no LangGraph checkpointer installed."""
    src = (Path(__file__).resolve().parent.parent /
           "cozmo" / "graphs" / "runtime_graph.py").read_text(encoding="utf-8")
    assert "compile(checkpointer" not in src
    assert "MemorySaver" not in src
