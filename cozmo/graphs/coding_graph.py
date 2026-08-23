"""Coding workflow as a LangGraph StateGraph (Phase 7 Stage 3D; 8C upgrade).

Composes Cozmo's existing ReAct agent loop into an explicit workflow:

    START → understand → plan → implement → verify
                                    ▲          │ passed / skipped / terminal
                                    │          ▼ failure (bounded)
                                 analyze ←─────┘
                                    │
                                 implement   (repair attempt, feedback-injected)

Phase 8C makes verification REAL and makes failures useful:

  * verify   — runs the injected ``verify`` collaborator ONLY when the
    implement attempt actually edited files. The collaborator routes every
    command through ToolExecutor.execute() — this graph never executes a
    subprocess itself; permission/risk/deny/confirmation semantics stay
    exactly where they belong.
  * analyze  — classifies each bounded VerificationReport:
    implementation failure → build repair feedback and retry (bounded);
    environment failure (missing interpreter/test runner) or permission
    denial → terminate honestly WITHOUT touching project code;
    success → finalize.
  * repair   — the next implement attempt receives ``repair_context``
    (bounded, factual failure output) so it never repeats blind.

Ownership boundaries (immutable):
  * Model selection  — Cozmo (this graph RECEIVES an already-constructed
    LangChain chat model; it never resolves, recommends, selects, substitutes,
    or falls back).
  * Tool execution   — ToolExecutor (the graph orchestrates; implement nodes
    delegate to the runtime's ReAct loop and verify nodes delegate to the
    injected verifier — every call passes the permission/risk gate).
  * Persistence      — Brain / Job / Checkpoint (LangGraph state is in-memory;
    NO LangGraph checkpointer).
  * Configuration    — the graph never reads or writes configuration.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from . import coding_intel as ci
from .state import CodingState, append_error, should_stop

log = logging.getLogger("cozmo.graphs.coding")


_DEFAULT_SYSTEM_PROMPT = (
    "You are Cozmo, a coding assistant. Modify the codebase to satisfy the "
    "user's request. Read relevant files before editing, make precise edits, "
    "and verify your work when possible. Do not invent file contents — read "
    "first."
)


class CodingGraph:
    """LangGraph coding workflow bound to injected collaborators.

    Args:
        model: Already-constructed LangChain chat model (Runnable). Used only
            as a standalone fallback when no ``run_loop`` is injected (tests /
            direct use). Cozmo resolved and built it upstream — never
            constructed here.
        run_loop: Callable ``(state: dict) -> (events, final, reason, ok)``
            wrapping one implement attempt (the runtime's ReAct agent loop).
        verify: Callable ``(state: dict) -> list[VerificationReport]``. The
            runtime injects a verifier that routes commands through
            ToolExecutor; standalone fallbacks may inject their own. None ⇒
            verification is skipped honestly.
        max_attempts: Bounded verify→implement re-loop budget (default 2).
            Never unbounded.
        understand / plan: Optional node callables.
    """

    def __init__(
        self,
        *,
        model=None,
        run_loop: Callable[[dict], tuple] | None = None,
        verify: Callable[[dict], list] | None = None,
        max_attempts: int = 2,
        understand: Callable[[dict], dict] | None = None,
        plan: Callable[[dict], dict] | None = None,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._model = model
        self._run_loop = run_loop
        self._verify = verify
        self._max_attempts = max_attempts
        self._understand = understand
        self._plan = plan
        self.max_attempts = max_attempts
        self._graph = self._build()

    # ── workflow definition ─────────────────────────────────────────────

    def _build(self):
        g = StateGraph(CodingState)
        g.add_node("understand", self._node_understand)
        g.add_node("plan", self._node_plan)
        g.add_node("implement", self._node_implement)
        g.add_node("verify", self._node_verify)
        g.add_node("analyze", self._node_analyze)
        g.add_edge(START, "understand")
        g.add_edge("understand", "plan")
        g.add_edge("plan", "implement")
        g.add_edge("implement", "verify")
        g.add_conditional_edges(
            "verify",
            _route_after_verify,
            {"analyze": "analyze", "implement": "implement", END: END},
        )
        g.add_conditional_edges(
            "analyze",
            _route_after_analyze,
            {"implement": "implement", END: END},
        )
        return g.compile()

    def run(self, state: dict) -> dict:
        """Execute the workflow for one per-run state; returns final state.

        The graph is authoritative for its own re-implement budget: it forces
        ``max_attempts`` into state so a caller-supplied stale value cannot
        make the workflow unbounded.

        Cancellation (Phase 8A): when the runtime's stop signal has already
        fired, no node executes at all — the run terminates deterministically
        with ``completion_reason="stopped"``.
        """
        s = dict(state)
        s["max_attempts"] = self._max_attempts
        s.setdefault("stream_events", [])
        s.setdefault("verification_reports", [])
        s.setdefault("metrics", {"attempts": 0})
        if should_stop(s):
            s["completion_reason"] = "stopped"
            s.setdefault("stop_reason", "stopped")
            return s
        t0 = time.perf_counter()
        result = self._graph.invoke(s)
        try:
            metrics = result.get("metrics")
            if isinstance(metrics, dict):
                metrics["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
                result["metrics"] = metrics
        except Exception:
            pass
        if not result.get("completion_reason"):
            # Mirror the inner loop's terminal reason so every workflow
            # exposes one uniform completion field.
            reason = result.get("stop_reason") or (
                "completed" if (result.get("answer") or "").strip() else "empty")
            result["completion_reason"] = reason
        return result

    # ── nodes ───────────────────────────────────────────────────────────

    def _node_understand(self, state: dict) -> dict:
        """Analysis already computed upstream by the orchestrator. Injected
        override (e.g. for standalone use) replaces it."""
        if should_stop(state):
            state["completion_reason"] = "stopped"
            return state
        if self._understand is not None:
            return self._understand(state)
        return state

    def _node_plan(self, state: dict) -> dict:
        """Retrieval/execution plan already computed upstream. Injected
        override (e.g. for standalone use) replaces it."""
        if should_stop(state):
            state["completion_reason"] = "stopped"
            return state
        if self._plan is not None:
            return self._plan(state)
        return state

    def _node_implement(self, state: dict) -> dict:
        """Implement node — delegates to the runtime's ReAct agent loop via
        the injected ``run_loop`` callable, or falls back to a plain model
        invoke.

        The loop's stream events are captured into ``state["events"]`` so the
        runtime can replay them; ``answer`` / ``stop_reason`` carry the loop's
        terminal outcome. Tool execution is the loop's job (ToolExecutor
        gates every call) — this node only orchestrates.

        A cancelled run never starts (or restarts) an implementation attempt.
        """
        if should_stop(state):
            state["completion_reason"] = "stopped"
            state["stop_reason"] = "stopped"
            return state
        loop = state.get("run_loop") or self._run_loop
        if loop is not None:
            events, final, reason, _ok = loop(state)
            state["events"] = events
            state["answer"] = final or ""
            state["stop_reason"] = reason or ("completed" if final else "empty")
            if state["stop_reason"] == "error":
                append_error(state, source="graph.implement", stage="implement",
                             kind="model",
                             message=final[:500] if final else "agent loop error")
        else:
            events, final = self._standalone_invoke(state)
            state["events"] = events
            state["answer"] = final
            state["stop_reason"] = "completed" if final.strip() else "empty"
        state["attempt"] = state.get("attempt", 0) + 1
        metrics = state.setdefault("metrics", {})
        metrics["attempts"] = state["attempt"]

        # 8C.8: accumulate bounded edit metrics from THIS attempt's events so
        # later stages know whether anything was actually changed.
        attempt_edits = ci.scan_edits(events)
        state["edits_this_attempt"] = attempt_edits
        merged = ci.merge_metrics(metrics, attempt_edits)
        merged["attempts"] = state["attempt"]
        state["metrics"] = merged

        # Repair attempts consume the pending context once.
        state.setdefault("repair_context", "")
        return state

    def _standalone_invoke(self, state: dict) -> tuple[list, str]:
        """Standalone fallback: invoke the injected model with the runtime's
        system prompt + user input (+ any repair feedback). Used only when no
        ``run_loop`` is wired (tests / direct use)."""
        model = state.get("model") or self._model
        if model is None:
            return [], ""
        system_prompt = state.get("system_prompt") or _DEFAULT_SYSTEM_PROMPT
        msgs: list[Any] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=state.get("user_input") or ""),
        ]
        feedback = state.get("repair_context") or ""
        if feedback:
            msgs.append(SystemMessage(content=feedback))
        state["messages"] = msgs
        try:
            result = model.invoke(msgs)
            final = str(getattr(result, "content", "") or "")
        except Exception as e:
            log.warning("coding graph: implementation failed: %s", e)
            final = ""
        return [], final

    def _node_verify(self, state: dict) -> dict:
        """Verify node (8C.1/8C.2) — REAL verification through the boundary.

        Skipped honestly when there is no verifier or when the attempt made
        no edits (explaining code ≠ changing code). Every actual command is
        executed by the injected verifier via ToolExecutor.execute(); this
        node only inspects the returned bounded VerificationReports.

        A stopped run never starts verification (user cancellation wins).
        """
        if should_stop(state):
            state["completion_reason"] = "stopped"
            state["stop_reason"] = "stopped"
            return state

        verifier = state.get("verify") or self._verify
        reports: list[Any] = []
        skipped_reason = ""
        if verifier is None:
            skipped_reason = "no_verifier"
        elif not ci.had_edits(state.get("events") or []):
            skipped_reason = "no_edits"

        if skipped_reason:
            state["verification_reports"] = []
            state["verification_skipped"] = skipped_reason
            state["verification_status"] = ci.VS_SKIPPED
            # Preserve legacy retry semantics for incomplete loops even
            # without verification: empty/max_steps outcomes stay retryable.
            self._legacy_retry_gate(state)
            return state

        state.setdefault("stream_events", []).append({"phase": "verifying"})
        try:
            reports = list(verifier(state) or [])
        except Exception as e:
            log.warning("coding graph: verifier failed: %s", e)
            append_error(state, source="graph.verify", stage="verify",
                         kind="internal", message=str(e))
            reports = []

        # Phase 8 remediation (audit E): a verifier that executed ZERO
        # commands (e.g. no verification command configured) has NOT verified
        # anything. Reporting passed=True here would fabricate a green check.
        # The run terminates honestly without repair — there is no failure
        # evidence to repair against.
        if not reports:
            state["verification_reports"] = []
            state["verification_passed"] = False
            state["verification_status"] = ci.VS_UNAVAILABLE
            state["verification_classification"] = ""
            state["verify_note"] = "done"
            append_error(state, source="graph.verify", stage="verify",
                         kind="internal",
                         message="verification unavailable: no commands "
                                 "executed (none configured)")
            state.setdefault("stream_events", []).append({
                "phase": "verification_unavailable",
            })
            return state

        state["verification_reports"] = reports[:ci.MAX_REPORTS]
        state["verification_status"] = (ci.VS_VERIFIED if all(
            r.passed for r in state["verification_reports"])
            else ci.VS_FAILED)
        metrics = state.setdefault("metrics", {})
        metrics["verifications"] = int(metrics.get("verifications") or 0) + len(reports)
        failed_n = sum(1 for r in reports if not getattr(r, "passed", False))
        metrics["verification_failures"] = \
            int(metrics.get("verification_failures") or 0) + failed_n

        all_passed, blocking = ci.overall(state["verification_reports"])
        state["verification_passed"] = all_passed
        state["verification_classification"] = "" if all_passed else blocking
        if not all_passed:
            failed = [r for r in state["verification_reports"]
                      if not r.passed][:1]
            state.setdefault("stream_events", []).append({
                "phase": "verification_failed",
                "classification": blocking,
                "command": failed[0].command[:200] if failed else "",
                "exit_code": failed[0].exit_code if failed else None,
            })
        return state

    def _legacy_retry_gate(self, state: dict) -> None:
        """8A semantics retained: without verification results, an empty or
        step-starved loop still schedules ONE honest re-implement."""
        answer = state.get("answer") or ""
        reason = state.get("stop_reason") or ""
        if reason == "stopped":
            state["verify_note"] = "done"
            state["completion_reason"] = "stopped"
        elif not answer.strip() or reason == "max_steps":
            state["verify_note"] = "retry"
            if state.get("attempt", 0) < state.get("max_attempts", 2):
                state.setdefault("stream_events", []).append({
                    "phase": "retry",
                    "attempt": state.get("attempt", 0) + 1,
                    "reason": reason or "empty",
                })
        else:
            state["verify_note"] = "done"

    def _node_analyze(self, state: dict) -> dict:
        """Analyze node (8C.5/8C.6/8C.7) — classify failures, build bounded
        repair feedback, schedule the bounded repair attempt.

        Implementation failures → repair (feedback injected). Environment /
        permission failures → terminate honestly; repairing project files
        cannot fix a missing interpreter or an explicitly denied command.
        """
        if should_stop(state):
            state["completion_reason"] = "stopped"
            state["stop_reason"] = "stopped"
            return state
        reports = state.get("verification_reports") or []
        all_passed, classification = ci.overall(reports)

        if all_passed:
            state["verify_note"] = "done"
            return state

        if classification == "permission_denied":
            state["verify_note"] = "done"
            state["completion_reason"] = "permission_denied"
            state["stop_reason"] = "permission_denied"
            append_error(state, source="graph.verify", stage="analyze",
                         kind="permission",
                         message="verification command denied by permissions")
            return state

        if classification == "timeout":
            # Phase 8 remediation (audit F): a timeout is NOT evidence of a
            # code defect (slow suite / deadlock / infrastructure are equally
            # plausible). Blind repair on timeout would burn attempts against
            # the wrong hypothesis. Terminate honestly; the report tails stay
            # in state for inspection.
            state["verify_note"] = "done"
            state["completion_reason"] = "verification_timeout"
            state["stop_reason"] = "verification_timeout"
            append_error(state, source="graph.verify", stage="analyze",
                         kind="timeout",
                         message=_first_timeout_detail(reports))
            return state

        if classification == "environment":
            state["verify_note"] = "done"
            state["completion_reason"] = "environment_error"
            state["stop_reason"] = "environment_error"
            append_error(state, source="graph.verify", stage="analyze",
                         kind="environment",
                         message=_first_env_detail(reports))
            return state

        # Implementation failure: bounded repair with REAL failure feedback.
        feedback = ci.build_repair_feedback(reports)
        prior = state.get("repair_context") or ""
        state["repair_context"] = feedback if not prior else \
            f"{prior}\n\n{feedback}"[:ci.MAX_FEEDBACK_CHARS]
        state["verify_note"] = "retry"
        if state.get("attempt", 0) < state.get("max_attempts", 2):
            state.setdefault("stream_events", []).append({
                "phase": "retrying",
                "attempt": state.get("attempt", 0) + 1,
                "reason": "verification_failed",
            })
        else:
            state["completion_reason"] = "verification_failed"
            state["stop_reason"] = "verification_failed"
        return state


def _first_env_detail(reports: list) -> str:
    for r in reports:
        if r.classification == "environment":
            detail = r.stderr_tail or r.stdout_tail or r.command
            return (detail or "verification environment failure")[:300]
    return "verification environment failure"


def _first_timeout_detail(reports: list) -> str:
    for r in reports:
        if r.classification == "timeout":
            detail = r.stdout_tail or r.stderr_tail or r.command
            return (detail or "verification command timed out")[:300]
    return "verification command timed out"


def _route_after_verify(state: dict) -> str:
    """Explicit transition after verification.

      stopped                        → END
      skipped (no verifier/no edits) → legacy gate decided; END unless the
                                       8A retry marker fired
      all passed                     → END (finalize)
      any verification failure       → analyze (it classifies: repair is for
                                       implementation failures ONLY;
                                       environment / permission terminate)
    A cancelled run never routes anywhere but END.
    """
    if state.get("completion_reason") == "stopped":
        return END
    if state.get("verification_skipped"):
        if (state.get("verify_note") == "retry"
                and state.get("attempt", 0) < state.get("max_attempts", 2)):
            return "implement"
        return END
    reports = state.get("verification_reports") or []
    if not reports:
        # Zero executed commands (audit E): verification unavailable is a
        # terminal honest outcome — analyze has no failure to classify.
        return END
    all_passed, _cls = ci.overall(reports)
    if all_passed:
        return END
    return "analyze"


def _route_after_analyze(state: dict) -> str:
    """Explicit transition: implementation failure with budget left →
    repair attempt; otherwise END (reason already recorded)."""
    if state.get("completion_reason") == "stopped":
        return END
    if state.get("verify_note") == "retry":
        if state.get("attempt", 0) < state.get("max_attempts", 2):
            return "implement"
    return END
