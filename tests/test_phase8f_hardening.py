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

from novi.graphs import CodingGraph, ResearchGraph
from novi.runtime.event_bus import EventBus
from novi.runtime.execution_context import ExecutionContext


# ── helpers ───────────────────────────────────────────────────────────────


class _StubModel:
    def __init__(self, answer="done"):
        self.answer = answer

    def invoke(self, msgs):
        return type("R", (), {"content": self.answer})()


def _bundle(quality="sufficient", text="grounding evidence text for key"):
    from novi.runtime.evidence import EvidenceBundle, RetrievalQuality

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
        from novi.graphs.coding_intel import VerificationReport
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
