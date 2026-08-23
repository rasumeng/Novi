"""Cutover parity — legacy runtime vs workflow_engine="langgraph".

Pins the structured-parity matrix produced by runtime_parity_harness across
the representative workload set. Any behavioral difference between engines
fails here; intentional differences must be reclassified explicitly in this
file, never by weakening assertions.
"""

from __future__ import annotations

import pytest

from runtime_parity_harness import compare, run_all, run_workload, _tc


def test_parity_matrix_zero_unexplained_differences():
    records = run_all()
    rows, diffs = compare(records)
    assert diffs == [], (
        "behavioral differences between legacy and langgraph engines:\n"
        + "\n".join(f"  {d}" for d in diffs)
    )
    assert len(rows) == 15, "spec workload coverage"


def test_tool_ordering_and_gate_identity_exact():
    for workload in ("tool_call", "multi_tool_loop"):
        a = run_workload(workload, engine="legacy",
                         **_cfg(workload))
        b = run_workload(workload, engine="langgraph", **_cfg(workload))
        assert a.tool_calls == b.tool_calls
        assert [c[0] for c in a.tool_calls] == [c[0] for c in b.tool_calls]
        assert a.tool_failures == b.tool_failures


def test_max_steps_wording_and_reason_identical():
    a = run_workload("max_steps_exhaustion", engine="legacy",
                     **_cfg("max_steps_exhaustion"))
    b = run_workload("max_steps_exhaustion", engine="langgraph",
                     **_cfg("max_steps_exhaustion"))
    assert a.stop_reason == b.stop_reason
    assert a.final_text == b.final_text
    assert a.tool_calls == b.tool_calls, "same executions before the bound"
    # Both engines must terminate via their bounded-budget vocabulary, not an
    # incidental crash — the legacy wording is the contract.
    if a.stop_reason == "max_steps":
        assert "ran out of steps" in a.final_text


def test_model_unavailable_lifecycle_identical():
    a = run_workload("model_unavailable", engine="legacy",
                     **_cfg("model_unavailable"))
    b = run_workload("model_unavailable", engine="langgraph",
                     **_cfg("model_unavailable"))
    assert a.stop_reason == b.stop_reason == "error"
    assert a.error == b.error
    assert a.final_text == "" and b.final_text == ""
    assert "not found" in (a.error or ""), "strict-selection refusal text"


def test_brain_observation_contract_identical():
    cfg = _cfg("chat")
    a = run_workload("chat", engine="legacy", **cfg)
    b = run_workload("chat", engine="langgraph", **cfg)
    assert a.brain_observations == b.brain_observations
    assert len(a.brain_observations) == 1
    user, assistant, conv_id = a.brain_observations[0]
    assert user == "hello" and assistant == a.final_text
    # conversation id may be None on unplanned runs — both engines agree
    assert conv_id == b.brain_observations[0][2]


def test_cancellation_identical():
    a = run_workload("cancelled", engine="legacy", **_cfg("cancelled"))
    b = run_workload("cancelled", engine="langgraph", **_cfg("cancelled"))
    assert a.cancelled and b.cancelled
    assert a.stop_reason == b.stop_reason == "stopped"
    assert a.final_text == b.final_text == ""
    assert a.brain_observations == b.brain_observations == []


def test_latency_same_magnitude():
    recs = {k: v for k, v in run_all().items()}
    for name in ("chat", "tool_call", "multi_tool_loop"):
        la = recs[(name, "legacy")].latency_ms
        lb = recs[(name, "langgraph")].latency_ms
        assert max(la, lb) < 2000, "interactive budget"
        ratio = max(la, lb) / max(min(la, lb), 0.001)
        assert ratio < 50, f"{name}: latency divergence {la} vs {lb} ms"


# ── helpers ──────────────────────────────────────────────────────────────────


def _cfg(name):
    from runtime_parity_harness import WORKLOADS

    return dict(WORKLOADS[name])
