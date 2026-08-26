"""General runtime workflow as a LangGraph StateGraph (dual-path migration).

The explicit, checkable form of the legacy ReAct execution path:

    START → analyze → retrieve → reason ── tool calls? ──→ act ──→ reason
                                        │                  (bounded)
                                        ▼
                                     reflect → answer → END

Ownership boundaries (identical to the research/coding graphs — immutable):

* Model selection  — Novi. The graph RECEIVES the already-bound runnable;
  it never resolves, recommends, selects, substitutes, or falls back.
  ``ModelUnavailableError`` is never caught: it propagates exactly as it does
  on the legacy path.
* Retrieval        — the runtime's RetrievalExecutor / UnifiedRetriever /
  evidence pipeline ran BEFORE the graph; the retrieve node snapshots their
  output through the injected ``prepare_context`` collaborator. Zero
  retrieval logic lives in-graph and no second retrieval system exists.
* Tool execution   — ToolExecutor remains the sole gate: every call goes
  through the injected ``execute_tool`` collaborator, which wraps the
  runtime's ``ToolExecutor.execute`` (permission/risk/coordinator gating).
* Reflection       — Brain consolidation stays gated by Novi semantics via
  the injected ``reflect`` collaborator (default no-op preserves the current
  observe-per-turn behavior).
* Persistence      — none. LangGraph state is in-memory per run; Brain /
  ConversationStore / JobStore remain the only durable authorities. No
  LangGraph checkpointer is installed (no competing source of truth).
* Configuration    — never read or written here.

Errors are not swallowed to make the graph "robust": any exception raised in
a collaborator or model invocation propagates out of ``run()`` exactly as the
legacy loop lets it propagate into ``Runtime.run_stream``'s handler.
"""

from __future__ import annotations

import json
import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from .state import RuntimeState, append_error, emit_event, should_stop

log = logging.getLogger("novi.graphs.runtime")

# Mirrors the generic ReAct executor's (novi/runtime/react_attempt.py)
# terminal strings so downstream consumers see identical stop_reason and
# completion vocabulary on both paths.
_MAX_STEPS_MESSAGE = (
    "I ran out of steps before finishing. Here's where I got to — ask me to "
    "continue if you want me to keep going."
)
_EMPTY_MESSAGE = "(no response — the model returned empty output; try rephrasing)"


class RuntimeWorkflowGraph:
    """General analyze→retrieve→reason→act→reflect→answer workflow.

    Args:
        max_steps: hard bound on reason→act rounds. The graph is authoritative
            for its own budget: it forces this value into state so a stale
            caller-supplied value cannot make the loop unbounded.
    """

    def __init__(self, *, max_steps: int = 8):
        if max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        self.max_steps = max_steps
        self.max_attempts = max_steps  # uniform naming for parity checks
        self._graph = self._build()

    # ── workflow definition ─────────────────────────────────────────────

    def _build(self):
        g = StateGraph(RuntimeState)
        g.add_node("analyze", self._node_analyze)
        g.add_node("retrieve", self._node_retrieve)
        g.add_node("reason", self._node_reason)
        g.add_node("act", self._node_act)
        g.add_node("reflect", self._node_reflect)
        g.add_node("answer", self._node_answer)

        g.add_edge(START, "analyze")
        g.add_edge("analyze", "retrieve")
        g.add_edge("retrieve", "reason")
        g.add_conditional_edges(
            "reason",
            _route_after_reason,
            {"act": "act", "answer": "answer"},
        )
        g.add_conditional_edges(
            "act",
            _route_after_act,
            {"reason": "reason", "answer": "answer"},
        )
        g.add_conditional_edges(
            "answer",
            _route_after_answer,
            {"reflect": "reflect", END: END},
        )
        g.add_edge("reflect", END)
        return g.compile()

    def run(self, state: dict) -> dict:
        """Execute one workflow run; returns the final state."""
        s = dict(state)
        s["max_steps"] = self.max_steps
        s.setdefault("events", [])
        s.setdefault("tool_events", [])
        s.setdefault("observations", [])
        s.setdefault("messages", [])
        s.setdefault("attempt", 0)
        if should_stop(s):
            s["completion_reason"] = "stopped"
            return s
        result = self._graph.invoke(s)
        if not result.get("completion_reason"):
            answer = (result.get("answer") or "").strip()
            result["completion_reason"] = "completed" if answer else "empty"
        return result

    # ── nodes ───────────────────────────────────────────────────────────

    def _node_analyze(self, state: dict) -> dict:
        """Intent/evidence/complexity analysis via the existing Orchestrator.

        The runtime usually computed analysis upstream (executor.execute);
        the node then passes it through unchanged. When an ``analyze``
        collaborator is injected (standalone use), it runs there.
        """
        emit_event(state, ("phase", {"phase": "understanding"}))
        if should_stop(state):
            state["completion_reason"] = "stopped"
            return state
        analyze = state.get("analyze")
        if analyze is not None and state.get("analysis") is None:
            try:
                state["analysis"] = analyze(state.get("user_input") or "")
            except Exception as e:
                append_error(state, source="graph.analyze", stage="analyze",
                             kind="internal", message=str(e))
                raise
        return state

    def _node_retrieve(self, state: dict) -> dict:
        """Snapshot the context Novi's retrieval pipeline already produced.

        Retrieval itself is NOT re-run: RetrievalExecutor/UnifiedRetriever/
        EvidenceProcessor executed upstream under the runtime's budget
        authority. The node makes that context explicit workflow state so
        later nodes (and parity harnesses) can observe it.
        """
        emit_event(state, ("phase", {"phase": "retrieving"}))
        if should_stop(state):
            state["completion_reason"] = "stopped"
            return state
        prepare = state.get("prepare_context")
        if prepare is None:
            return state
        snapshot = prepare() or {}
        for key in ("grounding_text", "memory_context", "project_context",
                    "evidence_context", "quality"):
            if key in snapshot:
                state[key] = snapshot[key]
        return state

    def _node_reason(self, state: dict) -> dict:
        """One reasoning round against the ALREADY-bound runnable.

        Streams the model response: reasoning_content chunks become live
        ``("reasoning", text)`` items and content pieces become live
        ``("token", piece)`` items so the WebUI sees the answer as it is
        generated. The accumulated AIMessage feeds tool-call parsing exactly
        like the buffered path.

        Exceptions — including ModelUnavailableError — propagate untouched.
        """
        emit_event(state, ("phase", {"phase": "reasoning"}))
        if should_stop(state):
            state["completion_reason"] = "stopped"
            return state
        model = state.get("model")
        msgs = list(state.get("messages") or [])
        if not msgs:
            system_prompt = state.get("system_prompt") or ""
            msgs.append(SystemMessage(content=system_prompt))
            msgs.extend(_seed_messages(state))
            msgs.append(HumanMessage(content=state.get("user_input") or ""))
        acc = None
        content_buf = ""
        stream = getattr(model, "stream", None)
        if callable(stream):
            for chunk in model.stream(msgs):
                # Mid-generation cancellation: the legacy buffered invoke could
                # only be stopped between rounds; streaming lets a stop request
                # interrupt the model itself, matching the generic ReAct executor.
                if should_stop(state):
                    state["completion_reason"] = "stopped"
                    return state
                acc = chunk if acc is None else acc + chunk
                reasoning_content = chunk.additional_kwargs.get(
                    "reasoning_content", "") if hasattr(chunk, "additional_kwargs") else ""
                if reasoning_content:
                    emit_event(state, ("reasoning", reasoning_content))
                piece = chunk.content or ""
                if piece:
                    content_buf += piece
                    emit_event(state, ("token", piece))
            ai = acc if acc is not None else AIMessage(content="")
        else:
            # Test doubles / minimal runnables without .stream keep the
            # buffered semantics; production LangChain runnables always
            # stream.
            ai = model.invoke(msgs)
        ai = ai if isinstance(ai, AIMessage) else AIMessage(content=str(getattr(ai, "content", "") or ""))
        msgs.append(ai)
        state["messages"] = msgs
        calls = list(getattr(ai, "tool_calls", None) or [])
        state["pending_tool_calls"] = calls
        if not calls:
            state["answer"] = str(ai.content or "")
        return state

    def _node_act(self, state: dict) -> dict:
        """Execute pending tool calls through ToolExecutor (the sole gate).

        Replicates the legacy loop's observable contract: thinking/tool_call/
        tool_result event tuples, exact-call dedup message, ToolMessage
        accumulation, bounded rounds with the same max-steps wording.
        """
        emit_event(state, ("phase", {"phase": "acting"}))
        if should_stop(state):
            state["completion_reason"] = "stopped"
            return state
        execute_tool = state.get("execute_tool")
        events = state.setdefault("events", [])
        seen = set(state.get("seen_calls") or [])

        attempt = int(state.get("attempt") or 0)
        max_steps = int(state.get("max_steps") or self.max_steps)

        if attempt >= max_steps:
            final = _MAX_STEPS_MESSAGE
            state["answer"] = final
            state["completion_reason"] = "max_steps"
            events.append(("token", final))
            emit_event(state, ("token", final))
            return state

        calls = list(state.get("pending_tool_calls") or [])
        state["pending_tool_calls"] = []
        idx = attempt + 1

        if calls:
            names = ", ".join(c.get("name", "") for c in calls)
            detail = "; ".join(
                f"{c.get('name', '')}({json.dumps(c.get('args', {}), sort_keys=True, default=str)[:200]})"
                for c in calls
            )
            chunk = ("thinking", f"Running: {names}", detail, None)
            events.append(chunk)
            emit_event(state, chunk)

        msgs = list(state.get("messages") or [])
        observations = state.setdefault("observations", [])
        for c in calls:
            name = c.get("name", "")
            args = c.get("args", {}) or {}
            call_id = c.get("id") or f"call-{idx}-{name}"
            args_sig = json.dumps(args, sort_keys=True, default=str)
            sig = f"{name}:{args_sig}"
            chunk = ("tool_call", name, args, call_id, None)
            events.append(chunk)
            emit_event(state, chunk)

            if sig in seen:
                out = (
                    f"Error: you already made this exact {name} call "
                    f"and have its result above. Use it, or try a "
                    f"DIFFERENT call — do not repeat yourself."
                )
                diff = ""
            else:
                seen.add(sig)
                output, diff, _success = execute_tool(name, args, idx)
                out = output
            chunk = ("tool_result", name, out, call_id, diff)
            events.append(chunk)
            emit_event(state, chunk)
            msgs.append(ToolMessage(content=out, tool_call_id=call_id))
            observations.append({"name": name, "args": args, "output": out})

        state["messages"] = msgs
        state["seen_calls"] = sorted(seen)
        state["attempt"] = idx
        return state

    def _node_reflect(self, state: dict) -> dict:
        """Optional Brain consolidation hook — Novi-gated, default no-op."""
        reflect = state.get("reflect")
        if reflect is not None:
            reflect()
        return state

    def _node_answer(self, state: dict) -> dict:
        """Terminal text selection with legacy-empty fallback semantics."""
        emit_event(state, ("phase", {"phase": "answering"}))
        if should_stop(state):
            state["completion_reason"] = "stopped"
            return state
        final = (state.get("answer") or "").strip()
        if not final:
            final = _EMPTY_MESSAGE
            state.setdefault("events", []).append(("token", final))
            emit_event(state, ("token", final))
        state["answer"] = final
        return state


def _seed_messages(state: dict) -> list:
    """History/human seed messages supplied by the runtime (base_msgs[1:])."""
    return [m for m in (state.get("seed_messages") or [])]


def _route_after_reason(state: dict) -> str:
    if state.get("completion_reason") == "stopped":
        return "answer"
    return "act" if state.get("pending_tool_calls") else "answer"


def _route_after_act(state: dict) -> str:
    """Bounded loop: a terminal reason set inside Act (max steps / stop)
    exits to Answer instead of re-entering Reason — the legacy loop stops
    identically rather than invoking the model again."""
    if state.get("completion_reason"):
        return "answer"
    return "reason"


def _route_after_answer(state: dict) -> str:
    """Reflect only when the run produced a real answer — failures, empties,
    cancellations, and max-steps runs skip consolidation entirely (parity
    with the legacy path, which never reflects inside the request flow)."""
    if state.get("completion_reason"):
        return END
    return "reflect"
