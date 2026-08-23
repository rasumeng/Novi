"""LangGraph cutover parity harness — legacy vs workflow_engine="langgraph".

Drives both engines over identical hermetic fixtures and records structured
invariants (spec matrix). Deterministic stubs make even generated text
comparable; where streaming granularity legitimately differs (legacy streams
token deltas, the graph replays the final token), the harness compares the
concatenated token text plus event-kind semantics instead of raw tuples.

Run with -s to print the parity report table.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from cozmo.models import ModelUnavailableError
from cozmo.runtime.execution_context import ExecutionContext
from cozmo.runtime.evidence import EvidenceBundle
from cozmo.runtime.runtime import CozmoRuntime
from cozmo.graphs import RuntimeWorkflowGraph


# ── scripted collaborators ───────────────────────────────────────────────────


class ScriptedRunnable:
    """stream()/invoke() scripted like the legacy loop's usage."""

    def __init__(self, turns):
        # turns: list of either str (final answer) or list[tool_call dicts]
        self.turns = list(turns)
        self.calls = []

    def _next(self):
        if not self.turns:
            return "fallback-final"
        t = self.turns.pop(0)
        if isinstance(t, str):
            return AIMessage(content=t)
        return AIMessage(content="", tool_calls=t)

    def stream(self, msgs):
        self.calls.append(list(msgs))
        msg = self._next()
        content = str(msg.content or "")
        # legacy loop consumes token deltas: yield two halves when non-empty
        mid = max(1, len(content) // 2)
        for piece in (content[:mid], content[mid:]):
            if piece:
                yield SimpleNamespace(content=piece, additional_kwargs={})

    def invoke(self, msgs):
        self.calls.append(list(msgs))
        return self._next()


class FakeModelService:
    def __init__(self, runnable):
        self._runnable = runnable
        self.resolved = []

    def resolve(self, workload):
        self.resolved.append(workload)
        return ("ollama", "parity-model")

    def bind_model(self, name, tools=None, temperature=0.0):
        return self._runnable

    def client_for_model(self, name, temperature=0.0):
        return self._runnable


class UnavailableService:
    def resolve(self, workload):
        raise ModelUnavailableError(workload, None, [])


class FakeBrain:
    def __init__(self):
        self.observations = []

    def observe(self, turn):
        self.observations.append((turn.user, turn.assistant,
                                  turn.conversation_id))


class FakeToolExecutor:
    def __init__(self, results=None, fail=False):
        self.results = results or {}
        self.fail = fail
        self.calls = []          # ordered (name, args)
        self.failures = 0

    def execute(self, name, args, coordinator=None, step_idx=None, trace=None):
        self.calls.append((name, dict(args)))
        if self.fail:
            raise RuntimeError("tool gate exploded")
        out = self.results.get(name, f"{name}-ok")
        success = not out.startswith("Error")
        self.failures += 0 if success else 1
        return SimpleNamespace(output=out, diff="", success=success)

    def tool_category(self, name):
        return "test"

    def compute_diff(self, name, args):
        return ""


# ── fixture context ──────────────────────────────────────────────────────────


def make_ctx(**overrides):
    ctx = ExecutionContext(user_input=overrides.get("user_input", "hello"))
    ctx.analysis = SimpleNamespace(
        intent=SimpleNamespace(value=overrides.get("intent", "conversation")),
        evidence=SimpleNamespace(signals=[], confidence=1.0, needs_memory=False),
        complexity=SimpleNamespace(score=1, plan_level=0,
                                   max_steps=overrides.get("max_steps", 6)),
        capabilities=[overrides.get("intent", "conversation")],
        strategy=SimpleNamespace(value="respond"),
        grounding=SimpleNamespace(needs_grounding=False, confidence=0.0,
                                  source="heuristic", reason="parity"),
        retrieval_plan=None,
    )
    for key in ("grounding_text", "memory_context", "project_context"):
        ctx.__dict__[key] = overrides.get(key, "")
    ctx.grounding_quality = overrides.get("quality", "")
    ctx.evidence_context = overrides.get("evidence_context")
    return ctx


# ── driver ───────────────────────────────────────────────────────────────────


@dataclass
class ParityRecord:
    workload: str
    engine: str
    model_name: str = ""
    conversation_id: str | None = None
    tool_calls: list = field(default_factory=list)
    tool_failures: int = 0
    brain_observations: list = field(default_factory=list)
    history_len: int = 0
    stop_reason: str = ""
    final_text: str = ""
    event_kinds: list = field(default_factory=list)
    token_text: str = ""
    error: str | None = None
    cancelled: bool = False
    latency_ms: float = 0.0


def run_workload(workload: str, *, engine: str, turns=None, tools=None,
                 tool_results=None, tool_fail=False, ctx_over=None,
                 stop_before=False, history_seed=None, unavailable=False):
    runnable = ScriptedRunnable(turns or [])
    service = UnavailableService() if unavailable else FakeModelService(runnable)
    brain = FakeBrain()
    fx_tools = FakeToolExecutor(tool_results, fail=tool_fail)

    rt = CozmoRuntime(
        model_service=service,
        brain=brain,
        runtime_graph=RuntimeWorkflowGraph(max_steps=6),
        workflow_engine=engine,
    )
    rt.tool_executor = fx_tools
    # Retrieval is a shared upstream stage (identical code both engines);
    # fixtures preset its OUTPUT so engines are compared on consumption.
    rt.retrieval_executor.execute = lambda ctx, user_input: iter(())
    if history_seed:
        rt.history.extend(history_seed)

    ctx = make_ctx(**(ctx_over or {}))
    if stop_before:
        rt.stop_event = SimpleNamespace(is_set=lambda: True)

    rec = ParityRecord(workload=workload, engine=engine)
    t0 = time.perf_counter()
    try:
        for ev in rt.run_stream(context=ctx):
            kind = ev[0]
            rec.event_kinds.append(kind)
            if kind == "token":
                rec.token_text += ev[1]
            elif kind == "status":
                rec.error = rec.error or ""
            elif kind == "error":
                rec.error = ev[1]
    except Exception as e:  # engine-identical handler already yielded; safety net
        rec.error = f"{type(e).__name__}: {e}"
    rec.latency_ms = round((time.perf_counter() - t0) * 1000.0, 3)

    rec.model_name = ctx.model_name
    rec.conversation_id = ctx.conversation_id
    rec.tool_calls = list(fx_tools.calls)
    rec.tool_failures = fx_tools.failures
    rec.brain_observations = list(brain.observations)
    rec.history_len = len(rt.history)
    rec.stop_reason = getattr(ctx.trace, "stop_reason", "") or ""
    rec.final_text = rec.token_text
    rec.cancelled = rec.stop_reason == "stopped"
    return rec


def _tc(name, args=None, call_id="c"):
    return {"name": name, "args": args or {}, "id": call_id}


WORKLOADS = {
    "chat": dict(turns=["Hello! How can I help?"]),
    "memory_retrieval": dict(
        turns=["From memory: you prefer python."],
        ctx_over={"memory_context": "prefers python", "intent": "conversation"}),
    "knowledge_retrieval": dict(
        turns=["Per the knowledge base: uv is the build tool."],
        ctx_over={"grounding_text": "kb: build uses uv [kn-x]",
                  "quality": "sufficient", "intent": "conversation"}),
    "project_task": dict(
        turns=["Project uses pytest."],
        ctx_over={"project_context": "src layout, pytest", "intent": "coding",
                  "max_steps": 4}),
    "research_web": dict(
        turns=["Latest release notes say v2 shipped."],
        ctx_over={"grounding_text":
                  "**Evidence Summary**\n[S1] Release — https://r.example/x",
                  "quality": "sufficient", "intent": "research"}),
    "evidence_processing": dict(
        turns=["Structured evidence consumed."],
        ctx_over={
            "grounding_text":
                "**Evidence Summary** (confidence: 0.80)\nVerified facts:\n"
                "- v2 shipped today [S1]\nSources:\n"
                "[S1] Rel — https://rel.example/a (web)",
            "quality": "sufficient", "intent": "research"},
    ),
    "tool_call": dict(
        turns=[[_tc("lookup", {"k": "v"}, "c1")], "Found: lookup-ok"],
        tool_results={"lookup": "lookup-ok"}),
    "multi_tool_loop": dict(
        turns=[
            [_tc("step_one", {}, "c1")],
            [_tc("step_two", {"x": 1}, "c2")],
            "Chain complete.",
        ],
        tool_results={"step_one": "one-ok", "step_two": "two-ok"}),
    "continuation": dict(
        turns=["Continuing from before."],
        history_seed=[("prior q", "prior a")]),
    "failure_tool_gate": dict(
        turns=[[_tc("boom", {}, "c1")], "Recovered gracefully."],
        tool_fail=True),
    "model_unavailable": dict(unavailable=True),
    "cancelled": dict(stop_before=True, turns=["never reached"]),
    "insufficient_retrieval": dict(
        turns=["I don't have that on file."],
        ctx_over={"grounding_text": "", "quality": "empty"}),
    "graph_expansion": dict(
        turns=["Neighbor context integrated."],
        ctx_over={"grounding_text":
                  "seed claim\norigin=wikilink neighbor [kn-nbr]",
                  "quality": "empty"}),
    "max_steps_exhaustion": dict(
        # always-tool model vs the shared 6-round budget: both engines must
        # terminate with the same wording and stop_reason.
        turns=[[_tc(f"t{i}", {}, f"c{i}")] for i in range(10)],
        tool_results={"t0": "o"}),
}


def run_all():
    records = {}
    for name, cfg in WORKLOADS.items():
        for engine in ("legacy", "langgraph"):
            records[(name, engine)] = run_workload(name, engine=engine, **cfg)
    return records


# ── comparison ───────────────────────────────────────────────────────────────

# Streaming granularity: legacy emits N token deltas via .stream(); the graph
# replays one final token. Intentional (documented dual-path design); compare
# concatenated token text + non-token event vocabulary.
TOKEN_GRANULARITY = "intentional"


def compare(records):
    rows, diffs = [], []
    for name in WORKLOADS:
        a = records[(name, "legacy")]
        b = records[(name, "langgraph")]
        checks = {
            "model_name": (a.model_name, b.model_name),
            "conversation_id": (a.conversation_id, b.conversation_id),
            "tool_calls": (a.tool_calls, b.tool_calls),
            "tool_failures": (a.tool_failures, b.tool_failures),
            "brain_observations": (a.brain_observations, b.brain_observations),
            "history_len": (a.history_len, b.history_len),
            "stop_reason": (a.stop_reason, b.stop_reason),
            "final_text": (a.final_text, b.final_text),
            "error": (a.error, b.error),
            "cancelled": (a.cancelled, b.cancelled),
            "non_token_events": (
                [e for e in a.event_kinds if e != "token"],
                [e for e in b.event_kinds if e != "token"]),
        }
        row = {"workload": name}
        for k, (va, vb) in checks.items():
            ok = va == vb
            row[k] = "=" if ok else "X"
            if not ok:
                diffs.append((name, k, va, vb))
        # intentional difference bookkeeping
        ta = a.event_kinds.count("token")
        tb = b.event_kinds.count("token")
        row["token_chunks"] = f"legacy={ta} graph={tb} ({TOKEN_GRANULARITY})"
        rows.append(row)
    return rows, diffs


def print_report(rows, records):
    keys = ["workload", "model_name", "conversation_id", "tool_calls",
            "tool_failures", "brain_observations", "history_len",
            "stop_reason", "final_text", "error", "cancelled",
            "non_token_events", "token_chunks"]
    widths = {k: max(len(k), *(len(str(r.get(k, ""))) for r in rows)) for k in keys}
    print("PARITY MATRIX (legacy vs langgraph)")
    print(" | ".join(k.ljust(widths[k]) for k in keys))
    print("-" * sum(w + 3 for w in widths.values()))
    for r in rows:
        print(" | ".join(str(r[k]).ljust(widths[k]) for k in keys))
    lat = [(w, records[(w, "legacy")].latency_ms, records[(w, "langgraph")].latency_ms)
           for w in WORKLOADS]
    print("\nLATENCY ms (legacy | langgraph): "
          + ", ".join(f"{w}: {a}|{b}" for w, a, b in lat))


if __name__ == "__main__":
    recs = run_all()
    rows, diffs = compare(recs)
    print_report(rows, recs)
    print(f"\ndifferences: {len(diffs)}")
    for d in diffs:
        print("DIFF:", d)
