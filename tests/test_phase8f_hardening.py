"""Phase 8F — Runtime Hardening tests.

Partial-completion honesty
  - a graph run with a non-empty answer but a failing terminal reason
    (verification_failed / environment_error / permission_denied) maps to
    STEP_FAILED + PLAN_FAILED, never a phantom STEP_COMPLETED.

Cross-attempt tool deduplication (safe subset)
  - identical MUTATING calls (same tool + args) from a previous repair
    attempt are blocked by the loop's dedup gate; reads and commands stay
    repeatable because their inputs/state legitimately change.

Wall-clock visibility without arbitrary cutoffs
  - both graphs record elapsed_ms (+ search count) in bounded run metrics;
    termination stays owned by iteration/budget bounds, never a timer.

Error taxonomy
  - completion_reason values across all terminal paths stay within the
    documented closed set.
"""

import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from cozmo.graphs import CodingGraph, ResearchGraph
from cozmo.runtime.event_bus import EventBus
from cozmo.runtime.execution_context import ExecutionContext
from cozmo.runtime.runtime import CozmoRuntime


# ── helpers ───────────────────────────────────────────────────────────────


class _StubModel:
    def __init__(self, answer="done"):
        self.answer = answer

    def invoke(self, msgs):
        return type("R", (), {"content": self.answer})()


def _bundle(quality="sufficient", text="grounding evidence text for key"):
    from cozmo.runtime.evidence import EvidenceBundle, RetrievalQuality

    try:
        q = RetrievalQuality(quality)
    except ValueError:
        q = RetrievalQuality.EMPTY
    return EvidenceBundle(query="q", merged_text=text if q !=
                          RetrievalQuality.EMPTY else "", source_count=1,
                          quality=q)


def _research_state(**kw):
    state = {
        "user_input": "python asyncio event loop basics",
        "analysis": None, "retrieval_plan": None,
        "grounding_text": "", "quality": "",
        "query": "python asyncio event loop basics",
        "search_attempts": 0, "max_search_attempts": 2,
        "system_prompt": "system", "plan_step_index": 0,
    }
    state.update(kw)
    return state


_EDIT_EVENTS = [("tool_call", "write_file", {"path": "a.py"}, "c1"),
                ("tool_result", "write_file", "[ok]", "c1",
                 {"text": "+++ a.py", "added": 1, "removed": 0})]


def _coding_state(**kw):
    state = {
        "user_input": "add a logging helper",
        "analysis": None, "retrieval_plan": None,
        "system_prompt": "system", "plan_step_index": 0,
        "answer": "", "stop_reason": "", "attempt": 0, "max_attempts": 2,
    }
    state.update(kw)
    return state


def _make_plan(task_id="t8f", n=2):
    from cozmo.planner.models import Plan, PlanStep

    plan = Plan(id="p8f", task_id=task_id)
    plan.add_step(PlanStep(id="s0", plan_id=plan.id, description="work"))
    if n > 1:
        plan.add_step(PlanStep(id="s1", plan_id=plan.id, description="more"))
    return plan


def _make_execution_plan(plan, analysis):
    from cozmo.orchestrator.task_types import ExecutionPlan

    return ExecutionPlan(
        task_id=plan.task_id, tools=[], model_spec={"model": "m1"},
        plan=plan, context={"analysis": analysis}, max_steps=6,
        temperature=0.2)


class _M:
    def bind_model(self, name, tools, temperature=0.0):
        return _StubModel()

    def client_for_model(self, name, temperature=0.0):
        return _StubModel()


# ── partial-completion honesty ────────────────────────────────────────────


def _run_coding_with_plan(stop_reason):
    """Drive the runtime coding branch with a scripted loop whose terminal
    reason is `stop_reason` and whose final text is non-empty."""
    rt = CozmoRuntime(model_service=_M(), coding_graph=CodingGraph(),
                      cfg={"runtime": {"temperature": 0.2}})

    def fake_run_loop(state):
        return (list(_EDIT_EVENTS), f"partial output ({stop_reason})",
                stop_reason, stop_reason == "completed")

    original = rt._coding_graph_state

    def spy(ctx, runnable, base_msgs, user_input, step_budget):
        state = original(ctx, runnable, base_msgs, user_input, step_budget)
        state["run_loop"] = fake_run_loop
        # Keep verification out of this test: no edits gating interference.
        state["verify"] = None
        return state

    rt._coding_graph_state = spy

    analysis = SimpleNamespace(
        intent=SimpleNamespace(value="coding"),
        evidence=SimpleNamespace(signals=[], confidence=1.0, needs_memory=False),
        complexity=SimpleNamespace(score=1, plan_level=1, max_steps=4),
        capabilities=["coding"], strategy=SimpleNamespace(value="coding"),
        grounding=SimpleNamespace(needs_grounding=False, confidence=0.8,
                                  source="heuristic", reason="test"),
        retrieval_plan=None,
    )
    plan = _make_plan()
    ctx = ExecutionContext(user_input="fix it")
    ctx.execution_plan = _make_execution_plan(plan, analysis)
    ctx.analysis = analysis

    kinds = []
    for item in rt.run_stream(context=ctx):
        kinds.append(item[0])
    return kinds, plan


def test_verification_failure_with_output_is_step_failed_not_completed():
    kinds, plan = _run_coding_with_plan("verification_failed")
    assert kinds.count("step.failed") == 1
    assert kinds.count("step.completed") == 0
    assert kinds.count("plan.failed") == 1
    assert plan.steps[0].status.value == "failed"


def test_environment_error_with_output_is_step_failed_not_completed():
    kinds, plan = _run_coding_with_plan("environment_error")
    assert kinds.count("step.failed") == 1
    assert plan.steps[0].status.value == "failed"


def test_permission_denied_with_output_is_step_failed_not_completed():
    kinds, plan = _run_coding_with_plan("permission_denied")
    assert kinds.count("step.failed") == 1
    assert plan.steps[0].status.value == "failed"


def test_genuine_completion_still_completes():
    kinds, plan = _run_coding_with_plan("completed")
    assert kinds.count("step.completed") == 1
    assert kinds.count("step.failed") == 0
    assert plan.steps[0].status.value == "completed"
    # Subsumed template step cancelled, never phantom-completed.
    assert plan.steps[1].status.value == "cancelled"


# ── cross-attempt mutating-call dedup ─────────────────────────────────────


class _ToolCallModel:
    """Streams one AIMessage carrying the given native tool call."""

    def __init__(self, name, args):
        self.name = name
        self.args = args

    def stream(self, msgs):
        yield AIMessage(content="", tool_calls=[
            {"name": self.name, "args": self.args, "id": "x1"}])


def _bare_runtime():
    rt = CozmoRuntime(model_service=_M(), cfg={"runtime": {"temperature": 0.2}})
    rt.retrieval_executor.execute_search = lambda q, trace=None: _bundle()
    return rt


def _ctx_with_trace():
    from cozmo.runtime.trace import ExecutionTrace

    ctx = ExecutionContext(user_input="edit")
    ctx.trace = ExecutionTrace()
    return ctx


def test_seed_seen_blocks_identical_mutating_repeat():
    rt = _bare_runtime()
    ctx = _ctx_with_trace()
    sig = 'write_file:' + json.dumps({"path": "a.py", "content": "x"},
                                     sort_keys=True)
    consumed = []
    for chunk in rt._run_agent_loop(
            ctx, _ToolCallModel("write_file", {"path": "a.py", "content": "x"}),
            "coding", 1, [], seed_seen={sig}):
        consumed.append(chunk)
    results = [c for c in consumed if c[0] == "tool_result"]
    assert results and "already made this exact" in results[0][2]


def test_fresh_loop_without_seed_executes_mutating_call():
    rt = _bare_runtime()
    ctx = _ctx_with_trace()
    consumed = []
    for chunk in rt._run_agent_loop(
            ctx, _ToolCallModel("write_file", {"path": "a.py", "content": "x"}),
            "coding", 1, []):
        consumed.append(chunk)
    results = [c for c in consumed if c[0] == "tool_result"]
    assert results and "already made" not in results[0][2]


def test_run_loop_accumulates_signatures_across_attempts():
    """The runtime coding collaborator feeds prior attempt signatures into
    the next attempt's loop (the safe-subset contract).

    Phase 9B: the closure targets the generic single-attempt executor
    (``run_react_attempt``) directly, so the spy follows that seam. The
    captured kwargs contract is otherwise asserted verbatim."""
    rt = _bare_runtime()
    base_msgs = []
    captured_seeds = []

    def fake_executor(**kwargs):
        captured_seeds.append(set(kwargs.get("seed_seen") or ()))
        yield ("tool_call", "write_file", {"path": "b.py", "content": "y"},
               "c2", "workspace")
        yield ("_LOOP_DONE", "out", "completed", True)

    import cozmo.runtime.runtime as runtime_module
    original = runtime_module.run_react_attempt
    runtime_module.run_react_attempt = fake_executor
    try:
        fake_ctx = SimpleNamespace(analysis=None, retrieval_plan=None,
                                   resume_from=0)
        builder = CozmoRuntime._coding_graph_state(rt, fake_ctx, None,
                                                   base_msgs, "fix", 3)
        list(builder["run_loop"]({}))
        list(builder["run_loop"]({}))
    finally:
        runtime_module.run_react_attempt = original

    assert any("write_file:" in s for s in captured_seeds[-1]), (
        "second attempt must be seeded with attempt-1 signatures")


# ── wall-clock visibility ─────────────────────────────────────────────────


def test_research_graph_records_elapsed_and_searches():
    g = ResearchGraph(model=_StubModel(answer="python asyncio event loop basics"),
                      search=lambda q: _bundle(
                          text="python asyncio event loop basics guide"))
    result = g.run(_research_state())
    metrics = result.get("metrics") or {}
    assert metrics.get("elapsed_ms") >= 0
    assert metrics.get("searches") == 1


def test_coding_graph_records_elapsed():
    g = CodingGraph(run_loop=lambda s: ([], "ok", "completed", True))
    result = g.run(_coding_state())
    metrics = result.get("metrics") or {}
    assert metrics.get("elapsed_ms") >= 0


# ── error taxonomy ────────────────────────────────────────────────────────


def test_completion_taxonomy_is_closed():
    allowed = {"completed", "empty", "max_steps", "error", "stopped",
               "environment_error", "permission_denied", "verification_failed"}

    def verify_fail(state):
        from cozmo.graphs.coding_intel import VerificationReport
        return [VerificationReport(kind="test", exit_code=1,
                                   stdout_tail="fail", stderr_tail="",
                                   duration_ms=1.0, passed=False,
                                   command="pytest",
                                   classification="environment")]

    g = CodingGraph(run_loop=lambda s: (list(_EDIT_EVENTS), "edited",
                                        "completed", True),
                    verify=verify_fail, max_attempts=1)
    r = g.run(_coding_state())
    assert r["completion_reason"] in allowed

    g2 = CodingGraph(run_loop=lambda s: ([], "", "stopped", False))
    r2 = g2.run(_coding_state())
    assert r2["completion_reason"] in allowed

    g3 = ResearchGraph(model=_StubModel(), search=lambda q: _bundle())
    r3 = g3.run(_research_state())
    assert r3["completion_reason"] in allowed
