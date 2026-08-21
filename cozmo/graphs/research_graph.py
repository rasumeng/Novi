"""Research workflow as a LangGraph StateGraph (Phase 7 Stage 3C).

Composes Cozmo's existing retrieval/evidence building blocks into an explicit
workflow instead of the hand-rolled inline search loop:

    START → understand → plan → search → evaluate
                                        ├── gaps/insufficient → search
                                        ▼
                                     synthesize → validate
                                                  ├── insufficient → search
                                                  ▼
                                                 END

Ownership boundaries (immutable):
  * Model selection  — Cozmo (this graph RECEIVES an already-constructed
    LangChain chat model; it never resolves, recommends, selects, substitutes,
    or falls back).
  * Retrieval budget — RetrievalCoordinator (the graph reacts to budget/quality
    state; it never creates a second budget authority).
  * Tool execution   — ToolExecutor (the graph orchestrates; search nodes reuse
    the retrieval pipeline, not raw shell/tool execution).
  * Persistence      — Brain / Job / Checkpoint (LangGraph state is in-memory;
    NO LangGraph checkpointer).
  * Configuration    — the graph never reads or writes configuration.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from ..runtime.evidence import RetrievalQuality
from ..runtime.retrieval import RetrievalExecutor

from .state import ResearchState

log = logging.getLogger("cozmo.graphs.research")


_DEFAULT_SYSTEM_PROMPT = (
    "You are Cozmo, a helpful assistant. Answer the user's question using the "
    "retrieved evidence below when it is relevant. If the evidence does not "
    "answer the question, say so clearly rather than inventing facts."
)

_MISSING_EVIDENCE_HINT = (
    "No retrieved evidence is available for this question. Answer from your "
    "own knowledge, and clearly note that current/web information could not "
    "be verified."
)


class ResearchGraph:
    """LangGraph research workflow bound to injected collaborators.

    Args:
        model: Already-constructed LangChain chat model (Runnable). Cozmo
            resolved and built it upstream — never constructed here.
        search: Callable ``(query: str) -> EvidenceBundle``. Defaults to a
            thin wrapper over ``RetrievalExecutor.execute_search`` (reuses the
            existing EvidenceCollector / SearXNG pipeline).
        coordinator: Optional ``RetrievalCoordinator`` whose budget the search
            node must respect (single retrieval-budget authority).
        max_search_attempts: Bounded re-search budget (default 2). Never
            unbounded.
        understand / plan: Optional node callables. Defaults pass the
            upstream-computed analysis/retrieval_plan through unchanged.
    """

    def __init__(
        self,
        *,
        model=None,
        search: Callable[[str], Any] | None = None,
        coordinator=None,
        max_search_attempts: int = 2,
        understand: Callable[[dict], dict] | None = None,
        plan: Callable[[dict], dict] | None = None,
    ):
        if max_search_attempts < 1:
            raise ValueError("max_search_attempts must be >= 1")
        self._model = model
        self._search = search
        self._coordinator = coordinator
        self._max_search_attempts = max_search_attempts
        self._understand = understand
        self._plan = plan
        self.max_search_attempts = max_search_attempts
        self._graph = self._build()

    # ── workflow definition ─────────────────────────────────────────────

    def _build(self):
        g = StateGraph(ResearchState)
        g.add_node("understand", self._node_understand)
        g.add_node("plan", self._node_plan)
        g.add_node("search", self._node_search)
        g.add_node("evaluate", self._node_evaluate)
        g.add_node("synthesize", self._node_synthesize)
        g.add_node("validate", self._node_validate)

        g.add_edge(START, "understand")
        g.add_edge("understand", "plan")
        g.add_edge("plan", "search")
        g.add_edge("search", "evaluate")
        g.add_conditional_edges(
            "evaluate",
            _route_after_evaluate,
            {"search": "search", "synthesize": "synthesize"},
        )
        g.add_edge("synthesize", "validate")
        g.add_conditional_edges(
            "validate",
            _route_after_validate,
            {"search": "search", END: END},
        )
        return g.compile()

    def run(self, state: dict) -> dict:
        """Execute the workflow for one per-run state; returns final state.

        The graph is authoritative for its own re-search budget: it forces
        ``max_search_attempts`` into state so a caller-supplied stale value
        cannot make the workflow unbounded.
        """
        s = dict(state)
        s["max_search_attempts"] = self._max_search_attempts
        return self._graph.invoke(s)

    # ── nodes ───────────────────────────────────────────────────────────

    def _node_understand(self, state: dict) -> dict:
        """Analysis already computed upstream by the orchestrator. Injected
        override (e.g. for standalone use) replaces it."""
        if self._understand is not None:
            return self._understand(state)
        return state

    def _node_plan(self, state: dict) -> dict:
        """Retrieval plan already computed upstream by RetrievalPolicy. Injected
        override (e.g. for standalone use) replaces it."""
        if self._plan is not None:
            return self._plan(state)
        return state

    def _node_search(self, state: dict) -> dict:
        """Search node — bounded by the coordinator budget. Reuses the existing
        retrieval pipeline; produces an EvidenceBundle + quality grade.

        Prefers per-run collaborators from ``state`` (search callable +
        coordinator) so the runtime's own retrieval executor and budget are
        used; the construction-time defaults are a standalone fallback.

        When the runtime's pre-loop retrieval already produced SUFFICIENT
        evidence (``search_attempts == 0``), the node reuses it instead of
        re-searching — the graph never double-pays the web budget.
        """
        search = state.get("search") or self._search
        coord = state.get("coordinator") or self._coordinator
        if state.get("search_attempts", 0) == 0:
            if state.get("quality") == RetrievalQuality.SUFFICIENT.value:
                if state.get("grounding_text"):
                    log.debug("research graph: reusing sufficient pre-loop evidence")
                    state["search_blocked"] = True
                    return state
        if search is None or (coord is not None and not coord.budget.search_remaining):
            log.debug("research graph: search unavailable/budget exhausted; forcing synthesize")
            state["search_blocked"] = True
            return state
        state["search_blocked"] = False

        query = state.get("query") or state.get("user_input") or ""
        if not query:
            return state
        bundle = search(query)
        state["evidence"] = bundle
        state["grounding_text"] = (bundle.merged_text or "") if bundle else ""
        state["quality"] = (
            bundle.quality.value if bundle and bundle.quality else ""
        )
        state["query"] = query
        state["search_attempts"] = state.get("search_attempts", 0) + 1
        if coord is not None and bundle and bundle.merged_text:
            coord.seed_cache(query, bundle.merged_text)
        return state

    def _node_evaluate(self, state: dict) -> dict:
        """Evaluate evidence quality + detect gaps (explicit transition)."""
        quality = _quality(state)
        grounding = state.get("grounding_text") or ""
        terms = RetrievalExecutor.extract_key_terms(state.get("user_input") or "")
        relevance = (
            RetrievalExecutor.compute_relevance(grounding, terms)
            if terms and grounding
            else 1.0
        )
        gaps: list[str] = []
        if quality is RetrievalQuality.SUFFICIENT:
            if terms and relevance < 0.5:
                gaps = terms
        elif terms:
            gaps = terms
        state["gaps"] = gaps
        return state

    def _node_synthesize(self, state: dict) -> dict:
        """Explicit synthesis node — moves the inline answer step out of the
        ReAct loop. Invokes the injected model with the runtime's system prompt
        + retrieved evidence.

        ``state["model"]`` (the runnable Cozmo bound for this execution) wins
        over the graph's construction-time model, so the graph always consumes
        the model Cozmo selected and built for the current run.
        """
        model = state.get("model") or self._model
        if model is None:
            state["answer"] = ""
            state["validation"] = "empty"
            return state

        system_prompt = state.get("system_prompt") or _DEFAULT_SYSTEM_PROMPT
        grounding = state.get("grounding_text") or ""
        if not grounding:
            system_prompt = f"{system_prompt}\n\n{_MISSING_EVIDENCE_HINT}"

        msgs: list[Any] = [SystemMessage(content=system_prompt)]
        msgs.append(HumanMessage(content=state.get("user_input") or ""))
        state["messages"] = msgs

        try:
            result = model.invoke(msgs)
            state["answer"] = str(getattr(result, "content", "") or "")
        except Exception as e:
            log.warning("research graph: synthesis failed: %s", e)
            state["answer"] = ""
        return state

    def _node_validate(self, state: dict) -> dict:
        """Validation node — uses evidence/retrieval quality semantics. If the
        retrieved evidence was relevant but the answer does not incorporate it,
        the run routes back to search (bounded)."""
        answer = state.get("answer") or ""
        if not answer.strip():
            state["validation"] = "empty"
            return state
        terms = RetrievalExecutor.extract_key_terms(state.get("user_input") or "")
        grounding = state.get("grounding_text") or ""
        quality = _quality(state)
        if terms and grounding and quality is RetrievalQuality.SUFFICIENT:
            ground_rel = RetrievalExecutor.compute_relevance(grounding, terms)
            ans_rel = RetrievalExecutor.compute_relevance(answer, terms)
            if ground_rel >= 0.3 and ans_rel < ground_rel * 0.5:
                state["validation"] = "insufficient"
                return state
        state["validation"] = "sufficient"
        return state


def _quality(state: dict) -> RetrievalQuality:
    try:
        return RetrievalQuality(state.get("quality") or "empty")
    except ValueError:
        return RetrievalQuality.EMPTY


def _route_after_evaluate(state: dict) -> str:
    """Explicit transition: insufficient/gaps → search; sufficient → synthesize.

    A blocked search node (no search callable / coordinator budget exhausted)
    forces synthesize so the workflow can never spin on an unavailable search.
    """
    quality = _quality(state)
    if quality is RetrievalQuality.SUFFICIENT and not state.get("gaps"):
        return "synthesize"
    if state.get("search_blocked"):
        return "synthesize"
    if state.get("search_attempts", 0) < state.get("max_search_attempts", 2):
        return "search"
    return "synthesize"


def _route_after_validate(state: dict) -> str:
    """Explicit transition: insufficient evidence coverage → re-search (bounded);
    otherwise END."""
    if state.get("search_blocked"):
        return END
    if (
        state.get("validation") == "insufficient"
        and state.get("search_attempts", 0) < state.get("max_search_attempts", 2)
    ):
        return "search"
    return END