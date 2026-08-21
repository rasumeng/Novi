"""Coding workflow as a LangGraph StateGraph (Phase 7 Stage 3D).

Composes Cozmo's existing ReAct agent loop into an explicit workflow instead
of the hand-rolled inline loop:

    START → understand → plan → implement → verify
                                                ├── retry → implement (bounded)
                                                ▼
                                               END

The implement node delegates to the runtime's existing ``_run_agent_loop`` via
an injected ``run_loop`` callable, capturing its stream events so the runtime
can replay them exactly — tool execution stays exclusively inside the
ToolExecutor permission/risk boundaries.

Ownership boundaries (immutable):
  * Model selection  — Cozmo (this graph RECEIVES an already-constructed
    LangChain chat model; it never resolves, recommends, selects, substitutes,
    or falls back).
  * Tool execution   — ToolExecutor (the graph orchestrates; implement nodes
    delegate to the runtime's ReAct loop, which already routes every tool call
    through the permission/risk gate).
  * Persistence      — Brain / Job / Checkpoint (LangGraph state is in-memory;
    NO LangGraph checkpointer).
  * Configuration    — the graph never reads or writes configuration.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from .state import CodingState

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
            ``events`` is the list of stream tuples the runtime replays;
            ``final`` the loop's terminal text; ``reason`` its stop reason
            ("completed" / "max_steps" / "empty" / "error"). Defaults to None
            → plain ``model.invoke`` fallback.
        max_attempts: Bounded verify→implement re-loop budget (default 2).
            Never unbounded.
        understand / plan: Optional node callables. Defaults pass the
            upstream-computed analysis/retrieval_plan through unchanged.
    """

    def __init__(
        self,
        *,
        model=None,
        run_loop: Callable[[dict], tuple] | None = None,
        max_attempts: int = 2,
        understand: Callable[[dict], dict] | None = None,
        plan: Callable[[dict], dict] | None = None,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._model = model
        self._run_loop = run_loop
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
        g.add_edge(START, "understand")
        g.add_edge("understand", "plan")
        g.add_edge("plan", "implement")
        g.add_edge("implement", "verify")
        g.add_conditional_edges(
            "verify",
            _route_after_verify,
            {"implement": "implement", END: END},
        )
        return g.compile()

    def run(self, state: dict) -> dict:
        """Execute the workflow for one per-run state; returns final state.

        The graph is authoritative for its own re-implement budget: it forces
        ``max_attempts`` into state so a caller-supplied stale value cannot
        make the workflow unbounded.
        """
        s = dict(state)
        s["max_attempts"] = self._max_attempts
        return self._graph.invoke(s)

    # ── nodes ───────────────────────────────────────────────────────────

    def _node_understand(self, state: dict) -> dict:
        """Analysis already computed upstream by the orchestrator. Injected
        override (e.g. for standalone use) replaces it."""
        if self._understand is not None:
            return self._understand(state)
        return state

    def _node_plan(self, state: dict) -> dict:
        """Retrieval/execution plan already computed upstream. Injected
        override (e.g. for standalone use) replaces it."""
        if self._plan is not None:
            return self._plan(state)
        return state

    def _node_implement(self, state: dict) -> dict:
        """Implement node — delegates to the runtime's ReAct agent loop via the
        injected ``run_loop`` callable, or falls back to a plain model invoke.

        The loop's stream events are captured into ``state["events"]`` so the
        runtime can replay them; ``answer`` / ``stop_reason`` carry the loop's
        terminal outcome. Tool execution is the loop's job (ToolExecutor
        gates every call) — this node only orchestrates.
        """
        loop = state.get("run_loop") or self._run_loop
        if loop is not None:
            events, final, reason, _ok = loop(state)
            state["events"] = events
            state["answer"] = final or ""
            state["stop_reason"] = reason or ("completed" if final else "empty")
        else:
            events, final = self._standalone_invoke(state)
            state["events"] = events
            state["answer"] = final
            state["stop_reason"] = "completed" if final.strip() else "empty"
        state["attempt"] = state.get("attempt", 0) + 1
        return state

    def _standalone_invoke(self, state: dict) -> tuple[list, str]:
        """Standalone fallback: invoke the injected model with the runtime's
        system prompt + user input. Used only when no ``run_loop`` is wired
        (tests / direct use)."""
        model = state.get("model") or self._model
        if model is None:
            return [], ""
        system_prompt = state.get("system_prompt") or _DEFAULT_SYSTEM_PROMPT
        msgs: list[Any] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=state.get("user_input") or ""),
        ]
        state["messages"] = msgs
        try:
            result = model.invoke(msgs)
            final = str(getattr(result, "content", "") or "")
        except Exception as e:
            log.warning("coding graph: implementation failed: %s", e)
            final = ""
        return [], final

    def _node_verify(self, state: dict) -> dict:
        """Verify node — explicit transition. An empty answer or a loop that
        ran out of steps marks the run for a bounded re-implement attempt."""
        answer = state.get("answer") or ""
        reason = state.get("stop_reason") or ""
        if not answer.strip() or reason == "max_steps":
            state["verify_note"] = "retry"
        else:
            state["verify_note"] = "done"
        return state


def _route_after_verify(state: dict) -> str:
    """Explicit transition: incomplete implement → re-implement (bounded);
    otherwise END."""
    if state.get("verify_note") == "retry":
        if state.get("attempt", 0) < state.get("max_attempts", 2):
            return "implement"
    return END