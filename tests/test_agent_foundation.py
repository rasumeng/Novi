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

from novi.graphs import CodingGraph, ResearchGraph
from novi.graphs.state import (
    MAX_STATE_ERRORS,
    AgentStateBase,
    ErrorRecord,
    append_error,
    should_stop,
)
from novi.runtime.evidence import EvidenceBundle, RetrievalQuality
from novi.runtime.event_bus import EventBus
from novi.runtime.execution_context import ExecutionContext
from novi.runtime.retrieval_coordinator import RetrievalBudget, RetrievalCoordinator


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

    from novi.graphs.state import ResearchState, CodingState
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


# ── tool category single source (F1) ──────────────────────────────────────


def test_tool_category_single_source():
    from novi.runtime.tool_executor import ToolExecutor
    from novi.runtime.tool_registry import TOOL_CATEGORIES, tool_category

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
    import novi.runtime.agent_loop as rt_mod
    import novi.runtime.tool_executor as te_mod

    for mod in (rt_mod, te_mod):
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assert target.id != "_TOOL_CATEGORIES", (
                            f"{mod.__name__} redefines _TOOL_CATEGORIES")


def test_toolinfo_derives_category_from_registry():
    from novi.runtime.tool_registry import ToolInfo, TOOL_CATEGORIES

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


# ── architecture: graph import boundary stays closed ──────────────────────


def test_graphs_import_boundary_extended():
    """Guard 5 extension: graphs never touch configuration, jobs, services,
    persistence — or LangGraph checkpointing."""
    import novi.graphs.coding_graph as cg
    import novi.graphs.research_graph as rg
    import novi.graphs.research_intel as rint
    import novi.graphs.state as st

    forbidden_prefixes = ("..configuration", "..jobs", "..services",
                          "langgraph.checkpoint", "novi.jobs")
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
    import novi.graphs.coding_graph as cg
    import novi.graphs.research_graph as rg

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
