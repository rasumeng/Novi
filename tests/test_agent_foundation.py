"""Phase 8A — Agent Foundation & Reliability Seam tests.

Proves the reliability seams Phase 8B/8C will build on:

State
  - AgentStateBase fields exist on both workflow states; specialized fields
    stay specialized; error records are bounded.

Budget (F4)
  - Every graph-initiated search is gated and recorded through the
    RetrievalCoordinator (the single budget authority). Duplicate searches
    are blocked; exhausted budgets block distinct queries; the graph-level
    attempt bound remains an independent safety net.

Cancellation (F5)
  - The runtime-injected ``should_stop`` probe terminates both graphs at
    node boundaries with ``completion_reason="stopped"``. A stopped loop is
    terminal for the coding verify node (no re-implement after cancel).

Honest plan/step semantics (F6)
  - Graph-backed execution maps to exactly ONE logical plan step: one
    STEP_STARTED/STEP_COMPLETED pair, remaining template steps CANCELLED,
    no phantom completions, Checkpoint.step stays honest via JobLifecycle.

Tool metadata (F1)
  - One category authority in tool_registry; no duplicate tables can drift
    back into existence.

Events
  - Additive phase/retry stream events are emitted and forwarded without
    breaking existing consumers.
"""

import ast
import inspect
from types import SimpleNamespace

import pytest

from cozmo.graphs import CodingGraph, ResearchGraph
from cozmo.graphs.state import (
    MAX_STATE_ERRORS,
    AgentStateBase,
    ErrorRecord,
    append_error,
    should_stop,
)
from cozmo.runtime.evidence import EvidenceBundle, RetrievalQuality
from cozmo.runtime.event_bus import EventBus
from cozmo.runtime.execution_context import ExecutionContext
from cozmo.runtime.retrieval_coordinator import RetrievalBudget, RetrievalCoordinator
from cozmo.runtime.runtime import CozmoRuntime


# ── shared stubs ──────────────────────────────────────────────────────────


class _StubModel:
    """Model stub returning a canned answer; records invocations."""

    def __init__(self, answer="asyncio event loop basics explained"):
        self.answer = answer
        self.calls = 0

    def invoke(self, msgs):
        self.calls += 1
        return type("R", (), {"content": self.answer})()


def _bundle(quality=RetrievalQuality.SUFFICIENT,
            text="python asyncio event loop basics guide",
            error=None):
    return EvidenceBundle(
        query="q", merged_text=text if not error else "",
        source_count=1, quality=quality, error=error,
    )


def _research_state(**kw):
    state = {
        "user_input": "python asyncio event loop basics",
        "analysis": None,
        "retrieval_plan": None,
        "grounding_text": "",
        "quality": "",
        "query": "python asyncio event loop basics",
        "search_attempts": 0,
        "max_search_attempts": 2,
        "system_prompt": "system",
        "plan_step_index": 0,
    }
    state.update(kw)
    return state


def _coding_state(**kw):
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


# ── state ─────────────────────────────────────────────────────────────────


def test_agent_state_base_fields_exist():
    base_keys = set(AgentStateBase.__annotations__)
    assert {"user_input", "system_prompt", "messages", "model",
            "attempt", "max_attempts", "errors",
            "completion_reason"} <= base_keys

    from cozmo.graphs.state import ResearchState, CodingState
    # Both specialize the base: inherited keys merge into child annotations.
    for state_cls in (ResearchState, CodingState):
        assert base_keys <= set(state_cls.__annotations__), (
            f"{state_cls.__name__} must carry the shared fundamentals")
    # Specialized fields remain specialized.
    assert {"evidence", "gaps", "search_attempts",
            "validation"} <= set(ResearchState.__annotations__)
    assert {"events", "stop_reason",
            "verify_note"} <= set(CodingState.__annotations__)


def test_error_records_bounded():
    state = {}
    for i in range(MAX_STATE_ERRORS + 5):
        append_error(state, source="t", stage=f"s{i}", kind="internal",
                     message=f"err {i}")
    errors = state["errors"]
    assert len(errors) == MAX_STATE_ERRORS
    assert all(isinstance(e, ErrorRecord) for e in errors)
    # Newest retained, oldest dropped.
    assert errors[-1].stage == f"s{MAX_STATE_ERRORS + 4}"


def test_error_record_never_raises():
    state = {"errors": None}
    append_error(state, source="t", stage="s", kind="model", message="x")
    # A hostile state shape must not break execution.


def test_should_stop_tolerates_missing_and_failing_probe():
    assert should_stop({}) is False
    assert should_stop({"should_stop": lambda: True}) is True
    assert should_stop({"should_stop": lambda: False}) is False

    def _boom():
        raise RuntimeError("probe failure")

    assert should_stop({"should_stop": _boom}) is False


# ── budget accounting (F4) ────────────────────────────────────────────────


def test_graph_search_records_coordinator_usage():
    coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=3))
    searches = []

    def search(query):
        searches.append(query)
        return _bundle()

    g = ResearchGraph(model=_StubModel(), search=search)
    g.run(_research_state(coordinator=coord))

    assert len(searches) == 1
    assert coord.budget.searches_used == 1, (
        "graph-initiated search must be metered by the coordinator")


def test_duplicate_graph_search_is_gated():
    """Re-running an identical query double-pays for identical evidence:
    the coordinator gate must block it and force synthesize instead."""
    coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=5))
    calls = []

    def search(query):
        calls.append(query)
        return _bundle(quality=RetrievalQuality.WEAK)

    model = _StubModel(answer="unrelated answer text")
    g = ResearchGraph(model=model, search=search)
    result = g.run(_research_state(coordinator=coord))

    assert len(calls) == 1, "identical retry must be deduplicated"
    assert coord.budget.searches_used == 1
    assert result["completion_reason"] == "completed"


def test_budget_exhaustion_blocks_distinct_query():
    coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=1))
    calls = []

    def search(query):
        calls.append(query)
        return _bundle(quality=RetrievalQuality.WEAK)

    g = ResearchGraph(model=_StubModel(), search=search)
    result = g.run(_research_state(coordinator=coord))

    assert len(calls) == coord.budget.max_web_searches
    assert coord.budget.searches_used == len(calls)
    assert result["search_blocked"] is True
    assert result["completion_reason"] == "completed"


def test_failed_search_still_consumes_budget():
    """Accounting parity with the ToolExecutor path: failed searches count."""
    coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=2))

    def search(query):
        return _bundle(error="searxng down")

    g = ResearchGraph(model=_StubModel(), search=search)
    result = g.run(_research_state(coordinator=coord))

    assert coord.budget.searches_used == 1
    kinds = [e.kind for e in result.get("errors") or []]
    assert "search" in kinds


def test_attempt_bound_independent_of_budget():
    """Without a coordinator the graph attempt bound still caps recursion."""
    calls = []

    def search(query):
        calls.append(query)
        return _bundle(quality=RetrievalQuality.WEAK)

    g = ResearchGraph(model=_StubModel(), search=search)
    result = g.run(_research_state())  # no coordinator

    assert len(calls) == 2  # max_search_attempts default
    assert result["search_attempts"] == 2


def test_no_coordinator_bypass_possible_via_runtime():
    """The runtime always injects the run's coordinator into graph state."""
    from cozmo.runtime.runtime import CozmoRuntime

    captured = {}

    class _M:
        def resolve(self, workload):
            return ("ollama", "m1")

        def bind_model(self, name, tools, temperature=0.0):
            return _StubModel()

        def client_for_model(self, name, temperature=0.0):
            return _StubModel()

    rt = CozmoRuntime(model_service=_M(), research_graph=ResearchGraph(),
                      cfg={"runtime": {"temperature": 0.2}})
    original = rt._research_graph_state

    def spy(ctx, runnable, base_msgs, user_input):
        state = original(ctx, runnable, base_msgs, user_input)
        captured["coordinator"] = state.get("coordinator")
        captured["should_stop"] = callable(state.get("should_stop"))
        return state

    rt._research_graph_state = spy
    ctx = ExecutionContext(user_input="q")
    ctx.analysis = SimpleNamespace(
        intent=SimpleNamespace(value="research"),
        evidence=SimpleNamespace(signals=[], confidence=1.0, needs_memory=False),
        complexity=SimpleNamespace(score=1, plan_level=0, max_steps=3),
        capabilities=["research"], strategy=SimpleNamespace(value="research"),
        grounding=SimpleNamespace(needs_grounding=True, confidence=0.8,
                                  source="heuristic", reason="test"),
        retrieval_plan=None,
    )
    rt.retrieval_executor.execute_search = lambda query, trace=None: _bundle()
    for _ in rt.run_stream(context=ctx):
        pass
    assert captured["coordinator"] is not None
    assert captured["should_stop"] is True


# ── cancellation (F5) ─────────────────────────────────────────────────────


def test_research_cancel_before_run_executes_nothing():
    model = _StubModel()
    searches = []
    g = ResearchGraph(model=model, search=lambda q: searches.append(q))
    result = g.run(_research_state(should_stop=lambda: True))

    assert result["completion_reason"] == "stopped"
    assert model.calls == 0
    assert searches == []


def test_research_cancel_between_nodes_skips_remaining_work():
    """Probe flips to True after the first boundary check: understand runs,
    everything after bails without searching or invoking the model."""
    model = _StubModel()
    searches = []
    probes = {"n": 0}

    def probe():
        probes["n"] += 1
        return probes["n"] > 1

    g = ResearchGraph(model=model, search=lambda q: searches.append(q))
    result = g.run(_research_state(should_stop=probe))

    assert result["completion_reason"] == "stopped"
    assert searches == [], "no search may run after cancellation"
    assert model.calls == 0, "no synthesis may run after cancellation"


def test_research_cancel_during_retry_routing():
    """Validation wants a bounded re-search; a cancel fired mid-run means the
    routed search never executes."""
    model = _StubModel(answer="totally unrelated answer")
    calls = []
    stop = {"flag": False}

    def probe():
        return stop["flag"]

    def search(query):
        calls.append(query)
        stop["flag"] = True  # cancel fires between attempts
        return _bundle()  # relevant grounding...

    # ...but validation will judge the answer insufficient → route back to
    # search → search node sees cancelled and bails.
    g = ResearchGraph(model=model, search=search)
    result = g.run(_research_state(should_stop=probe))

    assert len(calls) == 1
    assert result["completion_reason"] == "stopped"


def test_coding_stopped_loop_is_terminal():
    """A stopped inner loop must NOT schedule a re-implement attempt."""
    implement_calls = []

    def run_loop(state):
        implement_calls.append(1)
        return [], "", "stopped", False

    g = CodingGraph(run_loop=run_loop)
    result = g.run(_coding_state())

    assert len(implement_calls) == 1, "cancel must end the verify→implement loop"
    assert result["verify_note"] == "done"
    assert result["completion_reason"] == "stopped"


def test_coding_cancel_before_retry_attempt():
    attempts = []

    def run_loop(state):
        attempts.append(state.get("attempt", 0))
        if len(attempts) == 1:
            return [], "", "max_steps", False  # schedules a retry
        return [], "done text", "completed", True

    g = CodingGraph(run_loop=run_loop)
    result = g.run(_coding_state(should_stop=lambda: True))

    assert len(attempts) == 0, "cancelled run never starts implementing"
    assert result["completion_reason"] == "stopped"


def test_normal_execution_unaffected_without_probe():
    searches = []

    def search(query):
        searches.append(query)
        return _bundle()

    model = _StubModel()
    g = ResearchGraph(model=model, search=search)
    result = g.run(_research_state())
    assert result["completion_reason"] == "completed"
    assert model.calls == 1

    cg = CodingGraph(run_loop=lambda s: ([], "ok", "completed", True))
    cresult = cg.run(_coding_state())
    assert cresult["completion_reason"] == "completed"


# ── honest plan/step semantics (F6) ───────────────────────────────────────


def _make_plan(task_id="t1", n=3):
    from cozmo.planner.models import Plan, PlanStep

    plan = Plan(id="p1", task_id=task_id)
    descriptions = ["Gather relevant information", "Synthesize findings",
                    "Deliver an answer"]
    for i in range(n):
        plan.add_step(PlanStep(id=f"s{i}", plan_id=plan.id,
                               description=descriptions[i % len(descriptions)]))
    return plan


def _make_execution_plan(plan, analysis):
    from cozmo.orchestrator.task_types import ExecutionPlan

    return ExecutionPlan(
        task_id=plan.task_id,
        tools=["calculator"],
        model_spec={"model": "m1"},
        plan=plan,
        context={"analysis": analysis},
        max_steps=6,
        temperature=0.2,
    )


_RESEARCH_ANALYSIS = dict(
    intent=SimpleNamespace(value="research"),
    evidence=SimpleNamespace(signals=[], confidence=1.0, needs_memory=False),
    complexity=SimpleNamespace(score=2, plan_level=1, max_steps=6),
    capabilities=["research"],
    strategy=SimpleNamespace(value="research"),
    grounding=SimpleNamespace(needs_grounding=True, confidence=0.9,
                              source="heuristic", reason="test"),
    retrieval_plan=None,
)


def _run_research_with_plan(rt, ctx):
    kinds = []
    for item in rt.run_stream(context=ctx):
        kinds.append(item[0])
    return kinds


def test_research_plan_single_honest_step():
    bus = EventBus()
    bus_events = []
    bus.on_any(lambda ev: bus_events.append((ev.type, ev.data)))

    class _M:
        def bind_model(self, name, tools, temperature=0.0):
            return _StubModel()

        def client_for_model(self, name, temperature=0.0):
            return _StubModel()

    rt = CozmoRuntime(model_service=_M(), research_graph=ResearchGraph(),
                      event_bus=bus, cfg={"runtime": {"temperature": 0.2}})
    rt.retrieval_executor.execute_search = lambda query, trace=None: _bundle()

    plan = _make_plan()
    ep = _make_execution_plan(plan, SimpleNamespace(**_RESEARCH_ANALYSIS))
    ctx = ExecutionContext(user_input="python asyncio event loop basics")
    ctx.execution_plan = ep
    ctx.analysis = ep.context["analysis"]

    kinds = _run_research_with_plan(rt, ctx)

    # Exactly one honest logical step — no phantom completions.
    assert kinds.count("step.started") == 1
    assert kinds.count("step.completed") == 1
    assert kinds.count("step.failed") == 0
    assert kinds.count("plan.completed") == 1

    statuses = [s.status.value for s in plan.steps]
    assert statuses[0] == "completed"
    assert statuses[1:] == ["cancelled", "cancelled"], (
        "subsumed template steps must be CANCELLED, never phantom-COMPLETED")

    started = [d for t, d in bus_events if t == "step.started"]
    completed = [d for t, d in bus_events if t == "step.completed"]
    assert len(completed) == 1
    assert completed[0]["index"] == started[0]["index"]

    plan_completed = [d for t, d in bus_events if t == "plan.completed"]
    assert plan_completed and plan_completed[0]["step_count"] == 1


def test_coding_plan_single_honest_step():
    from langchain_core.messages import AIMessage

    plan = _make_plan()
    analysis = SimpleNamespace(
        intent=SimpleNamespace(value="coding"),
        evidence=SimpleNamespace(signals=[], confidence=1.0, needs_memory=False),
        complexity=SimpleNamespace(score=2, plan_level=1, max_steps=6),
        capabilities=["coding"],
        strategy=SimpleNamespace(value="coding"),
        grounding=SimpleNamespace(needs_grounding=False, confidence=0.8,
                                  source="heuristic", reason="test"),
        retrieval_plan=None,
    )

    class _StreamRunnable:
        def stream(self, msgs):
            yield AIMessage(content="patched")

        def invoke(self, msgs):
            return AIMessage(content="patched")

    class _M:
        def bind_model(self, name, tools, temperature=0.0):
            return _StreamRunnable()

        def client_for_model(self, name, temperature=0.0):
            return _StreamRunnable()

    rt = CozmoRuntime(model_service=_M(), coding_graph=CodingGraph(),
                      cfg={"runtime": {"temperature": 0.2}})
    plan_ref = ep = _make_execution_plan(plan, analysis)
    ctx = ExecutionContext(user_input="add a logging helper")
    ctx.execution_plan = ep
    ctx.analysis = analysis

    kinds = [item[0] for item in rt.run_stream(context=ctx)]

    assert kinds.count("step.started") == 1
    assert kinds.count("step.completed") == 1
    statuses = [s.status.value for s in plan.steps]
    assert statuses[0] == "completed"
    assert statuses[1:] == ["cancelled", "cancelled"]


def test_research_failure_emits_honest_step_and_plan_failed():
    plan = _make_plan()
    analysis = SimpleNamespace(**_RESEARCH_ANALYSIS)

    class _EmptyModel:
        def invoke(self, msgs):
            return type("R", (), {"content": ""})()

    class _M:
        def bind_model(self, name, tools, temperature=0.0):
            return _EmptyModel()

        def client_for_model(self, name, temperature=0.0):
            return _EmptyModel()

    rt = CozmoRuntime(model_service=_M(), research_graph=ResearchGraph(),
                      cfg={"runtime": {"temperature": 0.2}})
    rt.retrieval_executor.execute_search = lambda query, trace=None: _bundle()
    ctx = ExecutionContext(user_input="python asyncio event loop basics")
    ctx.execution_plan = _make_execution_plan(plan, analysis)
    ctx.analysis = analysis

    kinds = [item[0] for item in rt.run_stream(context=ctx)]

    assert kinds.count("step.failed") == 1
    assert kinds.count("plan.failed") == 1
    assert kinds.count("plan.completed") == 0
    assert plan.steps[0].status.value == "failed"


def test_job_lifecycle_checkpoint_stays_honest():
    """One graph execution → one checkpoint whose step reflects reality."""
    from cozmo.services.job_lifecycle import JobLifecycle

    class FakeManager:
        def __init__(self):
            self.checkpoints = []

        def set_event_sink(self, sink):
            pass

        def submit(self, **kw):
            raise AssertionError("job pre-registered by test")

        def start(self, job_id):
            return True

        def checkpoint(self, job_id, cp):
            self.checkpoints.append(cp)

        def complete(self, job_id, result=""):
            pass

        def fail(self, job_id, error=""):
            pass

    mgr = FakeManager()
    bus = EventBus()
    lifecycle = JobLifecycle(mgr)
    lifecycle.register("t1", "j1")
    lifecycle.subscribe(bus)

    plan = _make_plan()

    class _M:
        def bind_model(self, name, tools, temperature=0.0):
            return _StubModel()

        def client_for_model(self, name, temperature=0.0):
            return _StubModel()

    rt = CozmoRuntime(model_service=_M(), research_graph=ResearchGraph(),
                      event_bus=bus, cfg={"runtime": {"temperature": 0.2}})
    rt.retrieval_executor.execute_search = lambda query, trace=None: _bundle()
    analysis = SimpleNamespace(**_RESEARCH_ANALYSIS)
    ctx = ExecutionContext(user_input="python asyncio event loop basics")
    ctx.execution_plan = _make_execution_plan(plan, analysis)
    ctx.analysis = analysis

    for _ in rt.run_stream(context=ctx):
        pass

    assert len(mgr.checkpoints) == 1, "one honest step → one checkpoint"
    cp = mgr.checkpoints[0]
    assert cp.step == 1, "Checkpoint.step must equal real completed steps"
    assert cp.completed_steps == [plan.steps[0].id]


# ── tool category single source (F1) ──────────────────────────────────────


def test_tool_category_single_source():
    from cozmo.runtime.tool_executor import ToolExecutor
    from cozmo.runtime.tool_registry import TOOL_CATEGORIES, tool_category

    assert ToolExecutor.tool_category("read_file") == "workspace"
    assert ToolExecutor.tool_category("execute_python") == "python"
    assert ToolExecutor.tool_category("web_fetch") == "web"
    assert ToolExecutor.tool_category("nonexistent") == "other"
    # Executor delegates to the registry constant — identical by identity.
    for name in ("read_file", "bash", "git_diff", "telegram_send"):
        assert ToolExecutor.tool_category(name) == TOOL_CATEGORIES[name]
    assert tool_category("web_search") == TOOL_CATEGORIES["web_search"]


def test_duplicate_category_tables_cannot_return():
    """Source-scan the runtime and executor: no local _TOOL_CATEGORIES table
    may exist anywhere except tool_registry."""
    import cozmo.runtime.runtime as rt_mod
    import cozmo.runtime.tool_executor as te_mod

    for mod in (rt_mod, te_mod):
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assert target.id != "_TOOL_CATEGORIES", (
                            f"{mod.__name__} redefines _TOOL_CATEGORIES")


def test_toolinfo_derives_category_from_registry():
    from cozmo.runtime.tool_registry import ToolInfo, TOOL_CATEGORIES

    info = ToolInfo(name="edit_file", description="d", fn=lambda: "")
    assert info.category == TOOL_CATEGORIES["edit_file"]
    unknown = ToolInfo(name="brand_new_tool", description="d", fn=lambda: "")
    assert unknown.category == "other"


# ── phase / retry stream events ───────────────────────────────────────────


def test_research_emits_phase_events():
    events = []

    def search(query):
        events.append(("phase", {"phase": "searching"}))
        return _bundle()

    g = ResearchGraph(model=_StubModel(), search=search)
    result = g.run(_research_state())
    emitted = result["stream_events"]
    phases = [e["phase"] for e in emitted]
    assert "searching" in phases
    assert "synthesizing" in phases


def test_research_retry_event_on_genuine_second_search():
    """Standalone path (no coordinator): a second executed search emits a
    retry marker. Runtime-backed retries arrive with 8B's query variation."""
    calls = []

    def search(query):
        calls.append(query)
        return _bundle(quality=RetrievalQuality.WEAK)

    g = ResearchGraph(model=_StubModel(), search=search)
    result = g.run(_research_state())  # no coordinator → gate inactive
    retries = [e for e in result["stream_events"] if e["phase"] == "retry"]
    assert len(retries) == 1
    assert retries[0]["attempt"] == 2


def test_coding_retry_event_on_bounded_reimplement():
    attempts = []

    def run_loop(state):
        attempts.append(state.get("attempt", 0))
        if len(attempts) == 1:
            return [], "", "max_steps", False
        return [], "finished properly", "completed", True

    g = CodingGraph(run_loop=run_loop)
    result = g.run(_coding_state())
    retries = [e for e in result["stream_events"] if e["phase"] == "retry"]
    assert len(retries) == 1
    assert retries[0]["reason"] == "max_steps"
    assert result["answer"] == "finished properly"


def test_webui_forwards_phase_events_additively():
    """The WebSocket forwarder passes phase/retry payloads verbatim and still
    routes unknown kinds through the generic branch."""
    import cozmo.webui_server as ws

    forwarded = []

    class Dummy:
        def _emit(self, payload):
            forwarded.append(payload)

    ws.Session._forward_item(Dummy(), ("phase", {"phase": "searching"}))
    ws.Session._forward_item(Dummy(), ("retry", {"phase": "retry",
                                                 "attempt": 2}))
    ws.Session._forward_item(Dummy(), ("token", "hello"))

    assert forwarded[0] == {"type": "phase", "phase": "searching"}
    assert forwarded[1] == {"type": "retry", "phase": "retry", "attempt": 2}
    assert forwarded[2] == {"type": "token", "text": "hello",
                            "detail": None, "query": None}


# ── architecture: graph import boundary stays closed ──────────────────────


def test_graphs_import_boundary_extended():
    """Guard 5 extension: graphs never touch configuration, jobs, services,
    persistence — or LangGraph checkpointing."""
    import cozmo.graphs.coding_graph as cg
    import cozmo.graphs.research_graph as rg
    import cozmo.graphs.research_intel as rint
    import cozmo.graphs.state as st

    forbidden_prefixes = ("..configuration", "..jobs", "..services",
                          "langgraph.checkpoint", "cozmo.jobs")
    for mod in (cg, rg, rint, st):
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not any(node.module.startswith(p)
                               for p in forbidden_prefixes), (
                    f"{mod.__name__} imports {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(alias.name.startswith(p)
                                   for p in forbidden_prefixes), (
                        f"{mod.__name__} imports {alias.name}")


def test_graphs_never_construct_models_or_execute_tools():
    """AST scan of graph sources for direct-execution / construction verbs
    that would violate Guard 5 even without an import."""
    import cozmo.graphs.coding_graph as cg
    import cozmo.graphs.research_graph as rg

    forbidden_calls = {"create_provider", "bind_tools", "create_chat_model",
                       "apply_selection", "resolve"}
    for mod in (cg, rg):
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                assert name not in forbidden_calls, (
                    f"{mod.__name__} calls {name}")
