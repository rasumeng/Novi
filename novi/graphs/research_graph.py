"""Research workflow as a LangGraph StateGraph (Phase 7 Stage 3C; 8B upgrade).

Composes Novi's existing retrieval/evidence building blocks into an explicit
workflow instead of the hand-rolled inline search loop:

    START → understand → plan → decompose → search → evaluate
                                                ├── gaps/insufficient → search
                                                ▼
                                  synthesize → validate
                                                ├── insufficient → search
                                                ▼
                                               END

Phase 8B turns the loop into genuine iterative research:
  * decompose   — LLM-assisted, bounded sub-questions (deterministic JSON
    contract, bounded retries, graceful fallback to the original question).
  * search      — accumulates URL-deduplicated evidence across attempts
    (bounded bundle/char caps) instead of overwriting it.
  * evaluate    — detects uncovered terms and derives a REFINED query from
    them (deterministic gap→query transform); the RetrievalCoordinator
    remains the single budget/gate authority.
  * synthesize  — grounds on context-budget-truncated accumulated evidence,
    a deterministic citation manifest built from ACTUAL results, and any
    detected source conflicts.
  * validate    — structural validation: insufficiency disclosure, citation
    resolvability against the manifest, and the existing relevance check.

Ownership boundaries (immutable):
  * Model selection  — Novi (this graph RECEIVES an already-constructed
    LangChain chat model; it never resolves, recommends, selects, substitutes,
    or falls back). Decomposition reuses the SAME bound model handle.
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
import time
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from ..runtime.evidence import RetrievalQuality
from ..runtime.retrieval import RetrievalExecutor

from . import research_intel as ri
from .state import ResearchState, append_error, should_stop, emit_event

log = logging.getLogger("novi.graphs.research")


_DEFAULT_SYSTEM_PROMPT = (
    "You are Novi, a helpful assistant. Answer the user's question using the "
    "retrieved evidence below when it is relevant. If the evidence does not "
    "answer the question, say so clearly rather than inventing facts."
)

_MISSING_EVIDENCE_HINT = (
    "No retrieved evidence is available for this question. Answer from your "
    "own knowledge, and clearly note that current/web information could not "
    "be verified."
)


def _stream_event(state: dict, ev: dict) -> None:
    """Buffer a phase/retry marker AND forward it to the live channel.

    The buffer keeps parity/evaluation harnesses working unchanged; the
    ``emit_event`` call lets the WebUI observe the marker during execution
    instead of in the end-of-run replay."""
    state.setdefault("stream_events", []).append(ev)
    emit_event(state, ("phase", ev))


class ResearchGraph:
    """LangGraph research workflow bound to injected collaborators.

    Args:
        model: Already-constructed LangChain chat model (Runnable). Novi
            resolved and built it upstream — never constructed here.
        search: Callable ``(query: str) -> EvidenceBundle``. Defaults to a
            thin wrapper over ``RetrievalExecutor.execute_search`` (reuses the
            existing EvidenceCollector / SearXNG pipeline).
        coordinator: Optional ``RetrievalCoordinator`` whose budget the search
            node must respect (single retrieval-budget authority).
        max_search_attempts: Bounded re-search budget (default 2). Never
            unbounded.
        understand / plan / decompose: Optional node callables. Defaults pass
            upstream-computed analysis/retrieval_plan through unchanged;
            ``decompose`` defaults to the built-in LLM-assisted decomposition.
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
        decompose: Callable[[dict], dict] | None = None,
    ):
        if max_search_attempts < 1:
            raise ValueError("max_search_attempts must be >= 1")
        self._model = model
        self._search = search
        self._coordinator = coordinator
        self._max_search_attempts = max_search_attempts
        self._understand = understand
        self._plan = plan
        self._decompose = decompose
        self.max_search_attempts = max_search_attempts
        self._graph = self._build()

    # ── workflow definition ─────────────────────────────────────────────

    def _build(self):
        g = StateGraph(ResearchState)
        g.add_node("understand", self._node_understand)
        g.add_node("plan", self._node_plan)
        g.add_node("decompose", self._node_decompose)
        g.add_node("search", self._node_search)
        g.add_node("evaluate", self._node_evaluate)
        g.add_node("synthesize", self._node_synthesize)
        g.add_node("validate", self._node_validate)

        g.add_edge(START, "understand")
        g.add_edge("understand", "plan")
        g.add_edge("plan", "decompose")
        g.add_edge("decompose", "search")
        g.add_edge("search", "evaluate")
        g.add_conditional_edges(
            "evaluate",
            _route_after_evaluate,
            {"search": "search", "synthesize": "synthesize", END: END},
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

        Cancellation (Phase 8A): when the runtime's stop signal has already
        fired, no node executes at all — the run terminates deterministically
        with ``completion_reason="stopped"``.
        """
        s = dict(state)
        s["max_search_attempts"] = self._max_search_attempts
        s.setdefault("stream_events", [])
        s.setdefault("original_question", s.get("user_input") or "")
        s.setdefault("sub_questions", [])
        s.setdefault("evidence_bundles", [])
        s.setdefault("metrics", {})
        if should_stop(s):
            s["completion_reason"] = "stopped"
            return s
        t0 = time.perf_counter()
        result = self._graph.invoke(s)
        # Phase 8F wall-clock visibility: record elapsed time without ever
        # imposing an arbitrary cutoff (local models may legitimately be slow;
        # iteration/budget bounds own termination).
        try:
            metrics = result.get("metrics")
            if isinstance(metrics, dict):
                metrics["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
                metrics["searches"] = int(result.get("search_attempts") or 0)
                result["metrics"] = metrics
        except Exception:
            pass
        if not result.get("completion_reason"):
            answer = (result.get("answer") or "").strip()
            result["completion_reason"] = "completed" if answer else "empty"
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
        """Retrieval plan already computed upstream by RetrievalPolicy. Injected
        override (e.g. for standalone use) replaces it."""
        if should_stop(state):
            state["completion_reason"] = "stopped"
            return state
        if self._plan is not None:
            return self._plan(state)
        return state

    def _node_decompose(self, state: dict) -> dict:
        """8B.1 — LLM-assisted query decomposition.

        Skips trivial questions without spending a model call. Uses the SAME
        already-bound model handle from state (never resolves one), a
        deterministic JSON contract, bounded parse retries, and a graceful
        fallback to the original question on any malformed output.
        """
        if should_stop(state):
            state["completion_reason"] = "stopped"
            return state
        override = self._decompose
        if override is not None:
            return override(state)

        question = state.get("user_input") or ""
        state["original_question"] = state.get("original_question") or question
        if len(state.get("sub_questions") or []) > 0 or not ri.should_decompose(question):
            # Trivial question (or already decomposed): keep original query.
            state.setdefault("sub_questions", [])
            return state

        model = state.get("model") or self._model
        if model is None:
            return state

        _stream_event(state, {"phase": "understanding"})
        prompt = ri.build_decompose_prompt(question)
        msgs: list[Any] = [
            SystemMessage(content=(
                "You decompose research questions. Respond with ONLY the "
                "requested JSON object.")),
            HumanMessage(content=prompt),
        ]
        for attempt in range(1 + ri.MAX_DECOMPOSE_RETRIES):
            if should_stop(state):
                state["completion_reason"] = "stopped"
                return state
            try:
                result = model.invoke(msgs)
                raw = str(getattr(result, "content", "") or "")
            except Exception as e:
                log.warning("research graph: decomposition failed: %s", e)
                append_error(state, source="graph.decompose",
                             stage="decompose", kind="model", message=str(e))
                break
            subs = ri.parse_decomposition(raw)
            if subs:
                state["sub_questions"] = subs
                break
        if not state.get("sub_questions"):
            # Malformed / unavailable output: deterministic fallback.
            state["sub_questions"] = []
            state["query"] = question
        else:
            # Search the FIRST sub-question first; evaluate derives follow-ups.
            state["query"] = state["sub_questions"][0]
            _stream_event(state, {
                "phase": "decomposed",
                "sub_questions": len(state["sub_questions"]),
            })
        return state

    def _node_search(self, state: dict) -> dict:
        """Search node — bounded by the coordinator budget AND metered through
        it (Phase 8A): every actual search is gated via ``gate_search`` and
        recorded via ``record_search`` so the coordinator remains the single
        budget authority even though the graph calls the retrieval pipeline
        directly instead of through ToolExecutor.

        Phase 8B: results ACCUMULATE into ``evidence_bundles`` with strict
        URL-deduplication and count bounds instead of being overwritten.

        Prefers per-run collaborators from ``state`` (search callable +
        coordinator) so the runtime's own retrieval executor and budget are
        used; the construction-time defaults are a standalone fallback.

        When the runtime's pre-loop retrieval already produced SUFFICIENT
        evidence (``search_attempts == 0``), the node reuses it instead of
        re-searching — the graph never double-pays the web budget.
        """
        if should_stop(state):
            state["completion_reason"] = "stopped"
            return state
        search = state.get("search") or self._search
        coord = state.get("coordinator") or self._coordinator
        if state.get("search_attempts", 0) == 0:
            if state.get("quality") == RetrievalQuality.SUFFICIENT.value:
                if state.get("grounding_text"):
                    log.debug("research graph: reusing sufficient pre-loop evidence")
                    state["search_blocked"] = True
                    return state
        query = state.get("query") or state.get("user_input") or ""
        if search is None or not query:
            state["search_blocked"] = True
            return state
        if coord is not None:
            # Budget authority gate: exhausted budget OR duplicate query.
            # A duplicate means the results are already in grounding_text —
            # searching again would double-pay for identical evidence.
            if not coord.gate_search(query):
                log.debug("research graph: search gated (budget/duplicate); forcing synthesize")
                state["search_blocked"] = True
                return state
        state["search_blocked"] = False

        attempts = state.get("search_attempts", 0)
        if attempts >= 1:
            _stream_event(state, {
                "phase": "retry", "attempt": attempts + 1,
                "reason": "insufficient_evidence",
                "query": query,
            })
        _stream_event(state, {"phase": "searching"})
        bundle = search(query)
        state["evidence"] = bundle
        bundles, added = ri.accumulate_bundle(
            state.get("evidence_bundles") or [], bundle)
        state["evidence_bundles"] = bundles
        state["grounding_text"] = (
            (bundle.merged_text or "") if bundle and bundle.merged_text
            else state.get("grounding_text") or ""
        )
        state["quality"] = (
            bundle.quality.value if bundle and bundle.quality else ""
        )
        state["query"] = query
        state["search_attempts"] = attempts + 1
        if coord is not None:
            # Account EVERY executed search — success or failure — exactly as
            # the ToolExecutor path does. This is the recording half of the
            # single-budget-authority contract.
            merged = (bundle.merged_text or "") if bundle else ""
            coord.record_search(query, merged)
        if added == 0 and attempts >= 1:
            _stream_event(state, {
                "phase": "deduplicated", "new_sources": 0,
            })
        if bundle is not None and bundle.error:
            append_error(state, source="graph.search", stage="search",
                         kind="search", message=bundle.error)
        return state

    def _node_evaluate(self, state: dict) -> dict:
        """Evaluate evidence quality + detect gaps (explicit transition).

        Phase 8B: gaps are the key terms NOT yet covered by accumulated
        evidence; when meaningful they deterministically derive the next
        refined query (gap→query refinement). With no gaps the current query
        is kept unchanged, so the coordinator's duplicate gate behaves exactly
        as before refinement existed.
        """
        if should_stop(state):
            state["completion_reason"] = "stopped"
            return state
        quality = _quality(state)
        grounding = state.get("grounding_text") or ""
        terms = RetrievalExecutor.extract_key_terms(state.get("user_input") or "")
        relevance = (
            RetrievalExecutor.compute_relevance(grounding, terms)
            if terms and grounding
            else 1.0
        )
        low_grounding = grounding.lower()
        uncovered = [t for t in terms if t.lower() not in low_grounding]
        _stream_event(state, {"phase": "evaluating"})
        gaps: list[str] = []
        if quality is RetrievalQuality.SUFFICIENT:
            if terms and relevance < 0.5:
                gaps = uncovered or terms
        elif grounding:
            # Weak/partial evidence: only genuinely missing terms are gaps.
            gaps = uncovered
        else:
            gaps = list(terms)
        state["gaps"] = gaps

        attempts = state.get("search_attempts", 0)
        subs = state.get("sub_questions") or []
        budget = state.get("max_search_attempts", 2)

        # Phase 8 remediation (audit B): decomposition may yield more
        # sub-questions than the bounded search budget allows. That state is
        # RECORDED explicitly so synthesis and validation know coverage is
        # incomplete instead of silently implying every part was researched.
        if subs:
            searched = min(len(subs), max(int(attempts), 0))
            unresearched = subs[searched:]
            if unresearched and int(attempts) >= int(budget):
                if not state.get("coverage_incomplete"):
                    state["coverage_incomplete"] = True
                    state["unresearched_questions"] = list(unresearched)
                    _stream_event(state, {
                        "phase": "coverage_incomplete",
                        "unresearched": len(unresearched),
                    })

        if subs and attempts < len(subs):
            # Decomposition coverage loop: unsearched sub-questions take
            # priority over gap-derived refinement.
            nxt = subs[attempts]
            if nxt != state.get("query"):
                state["query"] = nxt
                _stream_event(state, {
                    "phase": "refining", "gaps": len(gaps),
                    "next_query": nxt[:120],
                })
            return state

        if gaps and attempts >= 1:
            # Refine from the ORIGINAL question (audit A): anchors — entity,
            # subject, timeframe — always derive from what the user asked,
            # never from an already-refined keyword string, so successive
            # refinements cannot drift away from the question's context.
            base = (state.get("original_question")
                    or state.get("user_input")
                    or state.get("query") or "")
            refined = ri.refine_query(base, gaps, grounding)
            if refined and refined != state.get("query"):
                state["query"] = refined
                _stream_event(state, {
                    "phase": "refining", "gaps": len(gaps),
                    "next_query": refined[:120],
                })
        return state

    def _node_synthesize(self, state: dict) -> dict:
        """Explicit synthesis node — moves the inline answer step out of the
        ReAct loop. Invokes the injected model with the runtime's system prompt
        + truncated accumulated evidence + citation manifest + conflicts.

        ``state["model"]`` (the runnable Novi bound for this execution) wins
        over the graph's construction-time model, so the graph always consumes
        the model Novi selected and built for the current run.
        """
        if should_stop(state):
            state["completion_reason"] = "stopped"
            return state

        # Manifest + conflicts derive from ACTUAL accumulated evidence and
        # are built regardless of model availability so evaluation harnesses
        # can inspect them even for runs without a bound runnable.
        bundles = state.get("evidence_bundles") or []
        manifest = ri.build_manifest(bundles)
        state["citation_manifest"] = manifest
        conflicts = ri.collect_conflicts(bundles) if bundles else []
        state["conflicts"] = conflicts

        model = state.get("model") or self._model
        if model is None:
            state["answer"] = ""
            state["validation"] = "empty"
            return state

        budget = ri.context_budget_for(model)
        if bundles:
            grounding, truncated = ri.truncate_grounding(bundles, budget)
        else:
            raw = state.get("grounding_text") or ""
            truncated = len(raw) > budget
            grounding = raw[:budget] if truncated else raw

        system_prompt = state.get("system_prompt") or _DEFAULT_SYSTEM_PROMPT
        extras = ri.build_synthesis_extras(manifest, conflicts, truncated)

        # Phase 8 remediation (audit B): budget-exhausted sub-questions must
        # never be presented as verified. The synthesis model is told, in
        # bounded deterministic text, exactly which parts remain unresearched.
        if state.get("coverage_incomplete"):
            missing = list(state.get("unresearched_questions") or [])[:3]
            if missing:
                lines = "; ".join(q[:120] for q in missing)
                extras = "\n\n".join(filter(None, [extras,
                    "COVERAGE WARNING: the search budget was exhausted before "
                    f"these sub-questions could be researched: {lines}. "
                    "State clearly that these aspects remain UNVERIFIED; do "
                    "not present them as established."]))
        if extras:
            system_prompt = f"{system_prompt}\n\n{extras}"
        if not grounding:
            system_prompt = f"{system_prompt}\n\n{_MISSING_EVIDENCE_HINT}"

        msgs: list[Any] = [SystemMessage(content=system_prompt)]
        msgs.append(HumanMessage(content=state.get("user_input") or ""))
        state["messages"] = msgs
        _stream_event(state, {"phase": "synthesizing"})

        # Re-synthesis passes (validate → re-search → synthesize) would stream
        # a SECOND answer on top of the first one in the UI. Tell the frontend
        # to clear the partial answer before a new pass begins.
        if state.get("answer_streamed"):
            emit_event(state, ("answer_reset", ""))

        try:
            # Live synthesis: stream reasoning + answer pieces to the WebUI
            # while the model generates, mirroring the general workflow's
            # reason node. Runnables without .stream (test doubles) keep the
            # buffered invoke semantics.
            acc = None
            content_buf = ""
            stream = getattr(model, "stream", None)
            if callable(stream):
                for chunk in model.stream(msgs):
                    if should_stop(state):
                        state["completion_reason"] = "stopped"
                        return state
                    acc = chunk if acc is None else acc + chunk
                    rc = (chunk.additional_kwargs.get("reasoning_content", "")
                          if hasattr(chunk, "additional_kwargs") else "")
                    if rc:
                        emit_event(state, ("reasoning", rc))
                    piece = chunk.content or ""
                    if piece:
                        content_buf += piece
                        state["answer_streamed"] = True
                        emit_event(state, ("token", piece))
            if acc is not None:
                state["answer"] = content_buf or str(
                    getattr(acc, "content", "") or "")
            else:
                result = model.invoke(msgs)
                state["answer"] = str(getattr(result, "content", "") or "")
        except Exception as e:
            log.warning("research graph: synthesis failed: %s", e)
            append_error(state, source="graph.synthesize", stage="synthesize",
                         kind="model", message=str(e))
            # A partially-streamed dead answer must not stay on screen.
            if state.get("answer_streamed"):
                emit_event(state, ("answer_reset", ""))
            state["answer"] = ""
        return state

    def _node_validate(self, state: dict) -> dict:
        """Validation node — structural checks plus the existing relevance
        heuristic. If the retrieved evidence was relevant but the answer does
        not incorporate it, the run routes back to search (bounded).

        Checks (forgiving by design — wording differences never fail):
          * empty answer                       → "empty"
          * honest insufficiency disclosure     → "insufficient"
          * relevant evidence, irrelevant answer → "insufficient" (re-search)
          * otherwise                           → "sufficient"
        Citation problems are RECORDED (invalid_citations, citations_used)
        but only fail validation structurally, never on minor wording.
        """
        if should_stop(state):
            state["completion_reason"] = "stopped"
            return state
        answer = state.get("answer") or ""
        if not answer.strip():
            state["validation"] = "empty"
            state["validation_detail"] = {"status": "empty"}
            return state

        detail = ri.validate_answer(
            answer, state.get("citation_manifest") or ri.CitationManifest(),
            has_evidence=bool(state.get("evidence_bundles")),
        )

        # Existing relevance heuristic (Phase 7): relevant evidence that the
        # answer ignored → one more bounded attempt at better coverage.
        if detail["status"] == "sufficient":
            terms = RetrievalExecutor.extract_key_terms(
                state.get("user_input") or "")
            grounding = state.get("grounding_text") or ""
            quality = _quality(state)
            if terms and grounding and quality is RetrievalQuality.SUFFICIENT:
                ground_rel = RetrievalExecutor.compute_relevance(grounding, terms)
                ans_rel = RetrievalExecutor.compute_relevance(answer, terms)
                if ground_rel >= 0.3 and ans_rel < ground_rel * 0.5:
                    detail["status"] = "insufficient"

        state["validation_detail"] = detail
        state["validation"] = detail["status"]
        # Phase 8 remediation (audit B): incomplete decomposition coverage is
        # part of the honest validation record.
        if state.get("coverage_incomplete"):
            detail["coverage_incomplete"] = True
        # Phase 8G: surface the citation/insufficiency state without exposing
        # graph topology.
        _stream_event(state, {
            "phase": "validating",
            "citations_used": bool(detail.get("citations_used")),
            "insufficient": detail.get("status") == "insufficient",
            "coverage_incomplete": bool(state.get("coverage_incomplete")),
        })
        return state


def _quality(state: dict) -> RetrievalQuality:
    try:
        return RetrievalQuality(state.get("quality") or "empty")
    except ValueError:
        return RetrievalQuality.EMPTY


def _route_after_evaluate(state: dict) -> str:
    """Explicit transition: insufficient/gaps → search; sufficient → synthesize.

    A blocked search node (no search callable / coordinator gate) forces
    synthesize so the workflow can never spin on an unavailable search.
    A cancelled run terminates immediately (Phase 8A).
    """
    if state.get("completion_reason") == "stopped":
        return END
    # Decomposition coverage: unsearched sub-questions keep the loop going.
    subs = state.get("sub_questions") or []
    attempts = state.get("search_attempts", 0)
    if (
        subs and attempts < len(subs)
        and not state.get("search_blocked")
        and attempts < state.get("max_search_attempts", 2)
    ):
        return "search"
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
    otherwise END. A cancelled run terminates immediately (Phase 8A)."""
    if state.get("completion_reason") == "stopped":
        return END
    if state.get("search_blocked"):
        return END
    if (
        state.get("validation") == "insufficient"
        and state.get("search_attempts", 0) < state.get("max_search_attempts", 2)
    ):
        return "search"
    return END
