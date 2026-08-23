"""Phase 9B — react_attempt extraction parity & boundary tests.

The ReAct loop body moved verbatim from ``CozmoRuntime._run_agent_loop`` into
``cozmo.runtime.react_attempt.run_react_attempt``. These tests prove:

1. GOLDEN PARITY — the delegation wrapper and a direct executor wiring that
   replicates the coding ``run_loop`` collaborator produce identical event
   streams (including ``_LOOP_DONE`` payloads) across every terminal path:
   completion, tool rounds, duplicate-call dedup, max-steps, empty output,
   mid-stream model failure, pre-run cancellation, mid-round cancellation.
2. GOLDEN LITERALS — key legacy vocabulary is pinned against hardcoded
   expected tuples so "wrapper == executor" cannot mask joint drift.
3. EXECUTOR-DIRECT DEDUP — the Phase 8F seed_seen contract driven through
   ``run_react_attempt`` itself (mirrors the wrapper-level hardening tests).
4. CLOSURE INTEGRATION — the real ``run_loop`` collaborator + real executor
   blocks an identical mutating repeat across repair attempts end-to-end.
5. ARCHITECTURE GUARDS — the wrapper stays pure delegation, the executor
   never imports the graph layer, the coding state builder targets the
   executor (not the wrapper), RuntimeWorkflowGraph is untouched by the
   extraction, and the sentinel contract is preserved.
"""

from __future__ import annotations

import inspect
import json
import re
import threading
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessageChunk, SystemMessage

import cozmo.runtime.react_attempt as react_attempt
import cozmo.runtime.runtime as runtime_module
from cozmo.graphs import RuntimeWorkflowGraph
from cozmo.runtime.execution_context import ExecutionContext
from cozmo.runtime.retrieval import RecoveryAction
from cozmo.runtime.runtime import CozmoRuntime
from cozmo.runtime.trace import ExecutionTrace


_LOOP_DONE = react_attempt._LOOP_DONE

_MAX_STEPS_WORDING = (
    "I ran out of steps before finishing. Here's where I got to — ask me to "
    "continue if you want me to keep going.")
_EMPTY_WORDING = "(no response — the model returned empty output; try rephrasing)"
_DEDUP_WORDING = (
    "Error: you already made this exact {name} call "
    "and have its result above. Use it, or try a "
    "DIFFERENT call — do not repeat yourself.")


# ── scripted collaborators ───────────────────────────────────────────────────


def _tc(name, args=None, call_id="c1"):
    return {"name": name, "args": args or {}, "id": call_id}


class _M:
    """Model service placeholder; runnables are injected directly."""

    def bind_model(self, name, tools, temperature=0.0):
        return None

    def client_for_model(self, name, temperature=0.0):
        return None


class _RAISE:
    """Sentinel turn: blow up inside stream() like a failing provider."""


class _ScriptedModel:
    """Streams turns: str → two AIMessageChunk token halves;
    list[dict] → one chunk carrying native tool_calls.
    Optional ``event`` is set right after the first turn finishes streaming
    (production-style mid-round cancellation probe). ``_RAISE`` as a turn
    makes stream() raise before yielding anything."""

    def __init__(self, turns, event: threading.Event | None = None):
        self.turns = list(turns)
        self.event = event
        self.round = 0

    def stream(self, msgs):
        self.round += 1
        if not self.turns:
            return
        turn = self.turns.pop(0)
        if isinstance(turn, _RAISE):
            raise RuntimeError("model exploded")
        if isinstance(turn, str):
            mid = max(1, len(turn) // 2)
            for piece in (turn[:mid], turn[mid:]):
                if piece:
                    yield AIMessageChunk(content=piece)
        else:
            yield AIMessageChunk(content="", tool_calls=list(turn))
        if self.event is not None and self.round == 1:
            self.event.set()


class _FakeToolExecutor:
    """Deterministic ToolExecutor stand-in with the exact surface the loop
    touches: extract_calls / tool_category / compute_diff / tools_for_mode /
    execute."""

    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    def extract_calls(self, ai):
        return [
            {"name": c["name"], "args": c.get("args", {}),
             "id": c.get("id") or c["name"]}
            for c in (getattr(ai, "tool_calls", None) or [])
        ]

    def tool_category(self, name):
        return "workspace"

    def compute_diff(self, name, args):
        return {"tool": name}

    def tools_for_mode(self, allowed_tools=None, **kw):
        return []

    def execute(self, name, args, coordinator=None, perm_mode=None,
                permission_callback=None, step_idx=None, trace=None):
        self.calls.append((name, json.loads(json.dumps(args))))
        out = self.results.get(name, f"{name}-ok")
        # Mirror the real pipeline's Stage 10 trace recording so parity
        # covers the happy-path ToolCallTrace contract too.
        if trace is not None and getattr(self, "tracer", None) is not None:
            self.tracer.record_tool(
                step_idx=step_idx or 0, name=name, args=args, result=out,
                latency_ms=1.0, success=not out.startswith("Error"),
                error=out if out.startswith("Error") else None, trace=trace)
        return SimpleNamespace(output=out, success=not out.startswith("Error"),
                               diff=self.compute_diff(name, args),
                               latency_ms=1.0, structured=None, error=None)


class _FakeRetrieval:
    """No-op recovery authority; escalation paths must stay dormant here."""

    def recommend_when_model_answered(self, ctx):
        return SimpleNamespace(action=RecoveryAction.NONE)

    def recommend_after_tool(self, ctx, name, out):
        return SimpleNamespace(action=RecoveryAction.NONE)

    def commit_recovery(self, ctx, decision, tag):  # pragma: no cover
        raise AssertionError("recovery must not trigger in parity fixtures")


# ── fixture driver ───────────────────────────────────────────────────────────


def _make_rt(tool_executor, retrieval):
    rt = CozmoRuntime(model_service=_M(),
                      cfg={"runtime": {"temperature": 0.2}})
    rt._skills = {}                      # deterministic: no host skill bleed
    tool_executor.tracer = rt.tracer     # Stage-10 trace recording parity
    rt.tool_executor = tool_executor
    rt.retrieval_executor = retrieval
    return rt


def _make_ctx():
    ctx = ExecutionContext(user_input="parity")
    ctx.trace = ExecutionTrace()
    ctx.activated_skills = []
    ctx.allowed_tools = ["read_file"]
    ctx.retrieval_coordinator = None
    return ctx


def _wire(rt, ctx, runnable, budget, seed=None):
    """Exact kwarg expansion the production coding collaborator uses."""
    return dict(
        ctx=ctx,
        runnable=runnable,
        tool_executor=rt.tool_executor,
        tracer=rt.tracer,
        retrieval_executor=rt.retrieval_executor,
        capability_registry=rt._capability_registry,
        scan_skills=rt._scan_skills,
        skill_block=rt._skill_block,
        bind_runnable=rt._bind_runnable,
        stop_probe=rt._stop_probe(),
        event_bus=rt.event_bus,
        debug_trace=rt.debug_trace,
        step_budget=budget,
        base_msgs=[SystemMessage(content="sys")],
        step=None,
        step_index_base=0,
        seed_seen=set(seed or ()),
    )


def _drive(path: str, cfg: dict):
    """Run ONE scenario through 'wrapper' or 'executor' on fresh, identically
    constructed fixtures. Returns (events, trace_projection, fx)."""
    fx = _FakeToolExecutor(results=cfg.get("tool_results"))
    fr = _FakeRetrieval()
    rt = _make_rt(fx, fr)
    if cfg.get("stop"):
        rt.stop_event = threading.Event()
        rt.stop_event.set()
    elif cfg.get("stop_after_stream"):
        rt.stop_event = threading.Event()
    model = _ScriptedModel(list(cfg["turns"]), event=rt.stop_event
                           if cfg.get("stop_after_stream") else None)
    ctx = _make_ctx()

    if path == "wrapper":
        gen = rt._run_agent_loop(ctx, model, "coding",
                                 cfg.get("budget", 3),
                                 [SystemMessage(content="sys")],
                                 seed_seen=set(cfg.get("seed") or ()))
    else:
        gen = react_attempt.run_react_attempt(
            **_wire(rt, ctx, model, cfg.get("budget", 3),
                    seed=cfg.get("seed")))
    events = list(gen)

    steps = [{
        "tokens_generated": s.tokens_generated,
        "tools": [(t.name, dict(t.args), t.result_preview, t.success,
                   t.error, t.fallback_used) for t in s.tool_calls],
    } for s in ctx.trace.steps]
    return events, steps, fx


SCENARIOS = {
    "plain_answer": dict(turns=["Hello there"]),
    "single_tool_round": dict(
        turns=[[_tc("read_file", {"path": "a.py"}, "c1")], "Found it"],
        tool_results={"read_file": "file body"}),
    "duplicate_call": dict(
        turns=[[_tc("read_file", {"path": "a.py"}, "c1")],
               [_tc("read_file", {"path": "a.py"}, "c2")],
               "done"],
        tool_results={"read_file": "file body"}),
    "multi_call_round": dict(
        turns=[[_tc("read_file", {"path": "a.py"}, "c1"),
                _tc("glob", {"pattern": "*.py"}, "c2")],
               "both read"],
        tool_results={"read_file": "body", "glob": "a.py\nb.py"}),
    "max_steps": dict(
        turns=[[_tc(f"t{i}", {}, f"c{i}")] for i in range(10)],
        budget=2, tool_results={"t0": "o"}),
    "empty_output": dict(turns=[""]),
    "stream_error": dict(
        turns=[[_tc("read_file", {"path": "a.py"}, "c1")], _RAISE()],
        tool_results={"read_file": "ok"}),
    "cancelled_before_run": dict(turns=["never reached"], stop=True),
    "cancelled_mid_round": dict(
        turns=[[_tc("read_file", {"path": "a.py"}, "c1")], "later"],
        tool_results={"read_file": "ok"}, stop_after_stream=True),
}


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
@pytest.mark.parametrize("path", ["wrapper", "executor"])
def test_golden_parity_wrapper_equals_executor_direct(scenario, path):
    events, trace_proj, _fx = _drive(path, SCENARIOS[scenario])
    # Reference run through the OTHER path must be byte-identical.
    other = "executor" if path == "wrapper" else "wrapper"
    ref_events, ref_trace, _fx2 = _drive(other, SCENARIOS[scenario])
    assert events == ref_events, f"{scenario}: event streams diverged"
    assert trace_proj == ref_trace, f"{scenario}: traces diverged"


def test_golden_parity_seed_seen_symmetry():
    sig = ("write_file:" +
           json.dumps({"path": "a.py", "content": "x"}, sort_keys=True))
    cfg = dict(turns=[[_tc("write_file", {"path": "a.py", "content": "x"},
                           "x1")]],
               seed={sig}, tool_results={"write_file": "[written]"})
    a_events, _, a_fx = _drive("wrapper", cfg)
    b_events, _, b_fx = _drive("executor", cfg)
    assert a_events == b_events
    assert a_fx.calls == [] and b_fx.calls == []


# ── golden literals ──────────────────────────────────────────────────────────


def test_literal_plain_answer_vocabulary():
    events, _, _ = _drive("executor", SCENARIOS["plain_answer"])
    assert events == [
        ("token", "Hello"),
        ("token", " there"),
        (_LOOP_DONE, "Hello there", "completed", True),
    ]


def test_literal_tool_round_sequence_with_category_and_diff():
    events, trace_proj, _ = _drive("executor",
                                   SCENARIOS["single_tool_round"])
    assert events == [
        ("thinking", "Running: read_file",
         'read_file({"path": "a.py"})', None),
        ("tool_call", "read_file", {"path": "a.py"}, "call-0-read_file",
         "workspace"),
        ("tool_result", "read_file", "file body", "call-0-read_file",
         {"tool": "read_file"}),
        ("thinking", "Thinking...",
         "Processing tool results and forming response", None),
        ("token", "Foun"),
        ("token", "d it"),
        (_LOOP_DONE, "Found it", "completed", True),
    ]
    # Trace records the executed call under the round's step index.
    assert trace_proj[0]["tools"][0][:4] == (
        "read_file", {"path": "a.py"}, "file body", True)


def test_literal_duplicate_call_gets_legacy_message_and_skips_execution():
    fx = _FakeToolExecutor(results={"read_file": "file body"})
    rt = _make_rt(fx, _FakeRetrieval())
    ctx = _make_ctx()
    model = _ScriptedModel(SCENARIOS["duplicate_call"]["turns"])
    events = list(react_attempt.run_react_attempt(**_wire(rt, ctx, model, 3)))
    dup_results = [e for e in events if e[0] == "tool_result"]
    assert dup_results[0][2] == "file body"
    assert dup_results[1][2] == _DEDUP_WORDING.format(name="read_file")
    # Dedup path records a failed trace entry WITHOUT executing the tool.
    assert fx.calls.count(("read_file", {"path": "a.py"})) == 1
    dedup_trace = [t for s in ctx.trace.steps for t in s.tool_calls]
    assert any(not t.success and "already made this exact" in t.result_preview
               for t in dedup_trace)


def test_literal_max_steps_wording_and_reason():
    events, _, _ = _drive("executor", SCENARIOS["max_steps"])
    assert events[-1] == (_LOOP_DONE, _MAX_STEPS_WORDING, "max_steps", False)
    assert ("token", _MAX_STEPS_WORDING) in events
    assert len([e for e in events if e[0] == "tool_result"]) == 2


def test_literal_empty_output_fallback():
    """Legacy quirk preserved verbatim: a fully empty model output is
    replaced by the fallback wording, which then reads as 'completed'."""
    events, _, _ = _drive("executor", SCENARIOS["empty_output"])
    assert events == [
        ("token", _EMPTY_WORDING),
        (_LOOP_DONE, _EMPTY_WORDING, "completed", True),
    ]


def test_literal_error_path_converts_exception_to_sentinel():
    events, _, _ = _drive("executor", SCENARIOS["stream_error"])
    assert events[-1] == (_LOOP_DONE, "I hit an error: model exploded",
                          "error", False)
    assert ("token", "I hit an error: model exploded") in events
    assert any(e[0] == "tool_result" for e in events[:-2])


def test_literal_cancelled_before_run_payload_exact():
    events, _, fx = _drive("executor", SCENARIOS["cancelled_before_run"])
    assert events == [(_LOOP_DONE, "", "stopped", False)]
    assert fx.calls == []


def test_literal_cancelled_mid_round_before_execution():
    fx = _FakeToolExecutor(results={"read_file": "ok"})
    rt = _make_rt(fx, _FakeRetrieval())
    rt.stop_event = threading.Event()
    ctx = _make_ctx()
    model = _ScriptedModel([[_tc("read_file", {"path": "a.py"}, "c1")]],
                           event=rt.stop_event)
    events = list(react_attempt.run_react_attempt(**_wire(rt, ctx, model, 3)))
    # Tool-call chunk streamed (no token text), pre-call checkpoint stops.
    assert events == [
        ("thinking", "Running: read_file",
         'read_file({"path": "a.py"})', None),
        (_LOOP_DONE, "", "stopped", False),
    ]
    assert fx.calls == []


# ── event-bus parity ─────────────────────────────────────────────────────────


def test_event_bus_emissions_identical_both_paths():
    class _Bus:
        def __init__(self):
            self.seen = []

        def emit(self, *a, **k):
            self.seen.append((a, k))

    seen_by_path = {}
    events_by_path = {}
    for path in ("wrapper", "executor"):
        bus = _Bus()
        fx = _FakeToolExecutor(results={"read_file": "b"})
        rt = _make_rt(fx, _FakeRetrieval())
        rt.event_bus = bus
        ctx = _make_ctx()
        model = _ScriptedModel(SCENARIOS["single_tool_round"]["turns"])
        if path == "wrapper":
            gen = rt._run_agent_loop(ctx, model, "coding", 3,
                                     [SystemMessage(content="sys")])
        else:
            gen = react_attempt.run_react_attempt(**_wire(rt, ctx, model, 3))
        events_by_path[path] = list(gen)
        seen_by_path[path] = bus.seen
    assert seen_by_path["wrapper"] == seen_by_path["executor"]
    assert len(seen_by_path["wrapper"]) == 2          # tool_called+tool_result
    assert events_by_path["wrapper"] == events_by_path["executor"]
    kinds = [a[0] for a, _k in seen_by_path["wrapper"]]
    assert kinds == ["tool_called", "tool_result"]


# ── executor-direct Phase 8F dedup (seed_seen) ───────────────────────────────


def test_executor_direct_seed_blocks_identical_mutating_repeat():
    fx = _FakeToolExecutor(results={"write_file": "[written]"})
    rt = _make_rt(fx, _FakeRetrieval())
    ctx = _make_ctx()
    sig = ("write_file:" +
           json.dumps({"path": "a.py", "content": "x"}, sort_keys=True))
    events = list(react_attempt.run_react_attempt(
        **_wire(rt, ctx,
                _ScriptedModel([
                    [_tc("write_file", {"path": "a.py", "content": "x"},
                         "x1")]]),
                3, seed={sig})))
    results = [e for e in events if e[0] == "tool_result"]
    assert results and "already made this exact" in results[0][2]
    assert fx.calls == []


def test_executor_direct_fresh_loop_executes_mutating_call():
    fx = _FakeToolExecutor(results={"write_file": "[written]"})
    rt = _make_rt(fx, _FakeRetrieval())
    ctx = _make_ctx()
    events = list(react_attempt.run_react_attempt(
        **_wire(rt, ctx,
                _ScriptedModel([
                    [_tc("write_file", {"path": "a.py", "content": "x"},
                         "x1")]]),
                3)))
    results = [e for e in events if e[0] == "tool_result"]
    assert results and "already made" not in results[0][2]
    assert fx.calls == [("write_file", {"path": "a.py", "content": "x"})]


def test_closure_accumulates_signatures_across_attempts_stubbed():
    """Seam-following twin of the Phase 8F hardening test: the coding
    collaborator passes the accumulated signature set into the NEXT
    attempt's executor call."""
    rt = CozmoRuntime(model_service=_M(),
                      cfg={"runtime": {"temperature": 0.2}})
    rt._skills = {}
    captured_seeds = []

    def fake_executor(**kwargs):
        captured_seeds.append(set(kwargs.get("seed_seen") or ()))
        yield ("tool_call", "write_file", {"path": "b.py", "content": "y"},
               "c2", "workspace")
        yield (_LOOP_DONE, "out", "completed", True)

    original = runtime_module.run_react_attempt
    runtime_module.run_react_attempt = fake_executor
    try:
        fake_ctx = SimpleNamespace(analysis=None, retrieval_plan=None,
                                   resume_from=0)
        builder = CozmoRuntime._coding_graph_state(rt, fake_ctx, None,
                                                   [], "fix", 3)
        list(builder["run_loop"]({}))
        list(builder["run_loop"]({}))
    finally:
        runtime_module.run_react_attempt = original

    assert any("write_file:" in s for s in captured_seeds[-1]), (
        "second attempt must be seeded with attempt-1 signatures")


def test_closure_real_executor_blocks_repeat_across_attempts():
    """End-to-end: real closure + real executor + real dedup gate. Attempt 2
    repeating attempt 1's byte-identical write is blocked and never reaches
    the executor twice."""
    fx = _FakeToolExecutor(results={"write_file": "[written]"})
    rt = _make_rt(fx, _FakeRetrieval())
    # Real ExecutionContext: the executor writes StepTrace entries.
    fake_ctx = _make_ctx()
    model = _ScriptedModel([
        [_tc("write_file", {"path": "a.py", "content": "x"}, "z1")],
        [_tc("write_file", {"path": "a.py", "content": "x"}, "z1")],
    ])
    builder = CozmoRuntime._coding_graph_state(
        rt, fake_ctx, model, [SystemMessage(content="sys")], "fix it", 1)

    # Attempt 1: mutating call executes normally.
    ev1, _final1, _reason1, _ok1 = builder["run_loop"]({})
    first_results = [e for e in ev1 if e[0] == "tool_result"]
    assert first_results and "already made" not in first_results[0][2]
    assert fx.calls == [("write_file", {"path": "a.py", "content": "x"})]

    # Attempt 2 (same builder ⇒ accumulated prior_sigs): identical write is
    # served the legacy dedup message instead of executing again.
    ev2, _final2, _reason2, _ok2 = builder["run_loop"]({})
    second_results = [e for e in ev2 if e[0] == "tool_result"]
    assert second_results, "attempt 2 must still produce a tool_result event"
    assert "already made this exact" in second_results[0][2]
    assert fx.calls == [("write_file", {"path": "a.py", "content": "x"})]


# ── architecture guards ──────────────────────────────────────────────────────


def test_executor_never_imports_graph_layer():
    src = inspect.getsource(react_attempt)
    assert not re.search(r"(from|import)\s+\S*graphs", src), (
        "the generic executor must not depend on the graph layer")


def test_wrapper_is_pure_delegation():
    src = inspect.getsource(CozmoRuntime._run_agent_loop)
    assert "yield from run_react_attempt(" in src
    for forbidden in ("extract_calls", "ToolMessage(", "for outer_step",
                      "seen_calls", ".stream("):
        assert forbidden not in src, (
            f"wrapper must not own loop mechanics ({forbidden})")


def test_coding_state_targets_executor_not_wrapper():
    src = "\n".join(
        ln for ln in
        inspect.getsource(CozmoRuntime._coding_graph_state).splitlines()
        if not ln.lstrip().startswith("#"))
    assert "run_react_attempt(" in src
    assert "_run_agent_loop" not in src, (
        "CodingGraph run_loop must bypass the legacy entry point entirely")


def test_wrapper_signature_unchanged_for_remaining_callers():
    params = [p.name for p in
              inspect.signature(CozmoRuntime._run_agent_loop)
              .parameters.values()]
    assert params == ["self", "ctx", "runnable", "intent_str", "step_budget",
                      "base_msgs", "step", "step_index_base", "seed_seen"]


def test_sentinel_contract_preserved():
    assert react_attempt._LOOP_DONE == "__plan_step_done__"
    assert runtime_module._LOOP_DONE is react_attempt._LOOP_DONE


def test_binding_seam_invocation_lives_only_in_executor():
    wrap_src = inspect.getsource(CozmoRuntime._run_agent_loop)
    exec_src = inspect.getsource(react_attempt)
    # Wrapper PASSES the seam along but never INVOKES it.
    assert "bind_runnable=self._bind_runnable," in wrap_src
    assert "self._bind_runnable(" not in wrap_src
    assert "bind_model" not in wrap_src and "client_for_model" not in wrap_src
    # The executor is the sole rebinding call site (recovery paths).
    assert re.search(r"\bbind_runnable\(ctx,", exec_src)


def test_runtime_workflow_graph_untouched_by_extraction():
    g_src = inspect.getsource(RuntimeWorkflowGraph)
    assert "run_react_attempt" not in g_src
    assert "_run_agent_loop" not in g_src
