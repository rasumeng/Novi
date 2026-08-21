"""LangGraph workflow state for Cozmo's agentic graphs (Phase 7 Stage 3).

Graph state is per-run WORKFLOW state only. It never carries configuration
ownership, persisted selection, recommendation state, or checkpoint
persistence — those remain Cozmo-owned authorities.
"""

from __future__ import annotations

from typing import Any, TypedDict


class ResearchState(TypedDict, total=False):
    """Per-run state for the research workflow.

    ``analysis`` / ``retrieval_plan`` / ``system_prompt`` are computed
    upstream by the orchestrator / planner / runtime and injected into the
    graph — the graph never derives or persists them.

    ``model`` / ``search`` / ``coordinator`` are the per-run collaborators
    the runtime injects: the ALREADY-bound runnable, a search callable, and
    the run's RetrievalCoordinator (the single budget authority).

    ``evidence`` / ``grounding_text`` / ``quality`` / ``gaps`` / ``answer``
    are produced by graph nodes composing the existing retrieval/evidence
    components.
    """

    user_input: str
    analysis: Any            # TaskAnalysis (orchestrator)
    retrieval_plan: Any      # RetrievalPlan (retrieval policy)
    evidence: Any            # EvidenceBundle produced by the search node
    grounding_text: str      # merged evidence text fed to the synthesizer
    quality: str             # RetrievalQuality.value of the current evidence
    gaps: list[str]          # detected knowledge gaps driving re-search
    query: str               # current search query
    search_attempts: int     # bounded re-search counter
    max_search_attempts: int # explicit re-search budget
    search_blocked: bool     # True when no search can run (None/budget) — forces synthesize
    system_prompt: str       # runtime-built system prompt (runtime authority)
    messages: list[Any]      # langchain messages assembled by the synthesizer
    plan_step_index: int     # plan step this workflow run belongs to
    answer: str              # synthesized final answer
    validation: str          # "sufficient" | "insufficient" | "empty"

    # per-run collaborators injected by the runtime boundary
    model: Any               # already-bound LangChain runnable/chat model
    search: Any              # callable (query: str) -> EvidenceBundle
    coordinator: Any         # RetrievalCoordinator (single budget authority)


class CodingState(TypedDict, total=False):
    """Per-run state for the coding workflow (Phase 7 Stage 3D).

    Shares the same boundaries as :class:`ResearchState`: ``analysis`` /
    ``retrieval_plan`` / ``system_prompt`` are computed upstream by the
    orchestrator / planner / runtime and injected; the graph never derives or
    persists them.

    ``model`` / ``run_loop`` are the per-run collaborators the runtime
    injects: the ALREADY-bound runnable, and a callable wrapping the runtime's
    ReAct agent loop for one implement attempt.

    ``events`` captures the inner agent-loop stream (token/reasoning/tool_* /
    terminal sentinel) so the runtime can replay exactly what the loop would
    have streamed — the graph orchestrates, it does not swallow output.

    ``attempt`` / ``max_attempts`` bound the verify→implement re-loop. The
    graph is authoritative for ``max_attempts`` and forces it on ``run()``.
    """

    user_input: str
    analysis: Any            # TaskAnalysis (orchestrator)
    retrieval_plan: Any      # RetrievalPlan (retrieval policy)
    system_prompt: str       # runtime-built system prompt (runtime authority)
    messages: list[Any]      # langchain messages assembled by the implement node
    plan_step_index: int     # plan step this workflow run belongs to
    answer: str              # final implemented output
    stop_reason: str         # "completed" | "max_steps" | "empty" | "error" | "stopped"
    verify_note: str         # "retry" | "done" — drives the bounded re-loop
    attempt: int             # implement attempts consumed
    max_attempts: int        # explicit re-implement budget (graph-authoritative)
    events: list[Any]        # captured runtime stream events to replay

    # per-run collaborators injected by the runtime boundary
    model: Any               # already-bound LangChain runnable/chat model
    run_loop: Any            # callable(state) -> (events, final, reason, ok)