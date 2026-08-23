"""LangGraph workflow state for Cozmo's agentic graphs (Phase 7 Stage 3).

Graph state is per-run WORKFLOW state only. It never carries configuration
ownership, persisted selection, recommendation state, or checkpoint
persistence — those remain Cozmo-owned authorities.

Phase 8A: a minimal :class:`AgentStateBase` holds the fields every workflow
shares, plus a bounded :class:`ErrorRecord` representation. Specialized
fields stay in the per-graph states — this is shared fundamentals, NOT state
unification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

# Upper bound on retained ErrorRecords per run. Oldest entries are dropped
# when exceeded — errors are structured signal, not an unbounded log store.
MAX_STATE_ERRORS = 8


@dataclass(frozen=True)
class ErrorRecord:
    """One bounded, stage-aware error observed during graph execution.

    Fields are intentionally small and safe to expose to later graph logic
    (and to evaluation harnesses). ``message`` keeps the existing
    human-readable text available alongside the structured view.
    """

    source: str            # which collaborator/node surfaced it ("graph", "model", ...)
    stage: str             # node/stage name ("synthesize", "search", "implement", ...)
    kind: str              # model | search | cancellation | internal |
                           # environment | permission | timeout
    message: str           # short human-readable message (already bounded by caller)


def append_error(state: dict, *, source: str, stage: str, kind: str,
                 message: str) -> None:
    """Append a bounded ErrorRecord to ``state["errors"]``.

    Keeps at most :data:`MAX_STATE_ERRORS` newest records. Never raises —
    error recording must not be able to fail execution.
    """
    try:
        errors = list(state.get("errors") or [])
        errors.append(ErrorRecord(
            source=source, stage=stage, kind=kind,
            message=(message or "")[:500],
        ))
        state["errors"] = errors[-MAX_STATE_ERRORS:]
    except Exception:
        pass


def should_stop(state: dict) -> bool:
    """Whether the runtime's stop signal has fired for this run.

    Cancellation stays owned by CozmoRuntime: the runtime injects a
    ``should_stop`` callable into per-run state; graphs only consult it at
    node boundaries. Missing callable / callable failure ⇒ not stopped.
    """
    check = state.get("should_stop")
    if check is None:
        return False
    try:
        return bool(check())
    except Exception:
        return False


class AgentStateBase(TypedDict, total=False):
    """Minimal shared fundamentals for all agent workflows.

    Only fields with cross-workflow meaning live here:

    ``user_input`` / ``system_prompt`` / ``messages``
        The request, the runtime-built prompt (runtime authority), and the
        LangChain messages assembled by nodes.
    ``model``
        The ALREADY-bound LangChain runnable injected by the runtime. Graphs
        never resolve, select, or construct models.
    ``attempt`` / ``max_attempts``
        Uniform attempt-budget naming for workflows that retry a primary
        action (the coding loop uses these directly; research exposes its own
        specialized ``search_attempts`` budget).
    ``errors``
        Bounded list of :class:`ErrorRecord`.
    ``completion_reason``
        Terminal outcome of the run: "completed" | "empty" | "max_steps" |
        "error" | "stopped".
    ``should_stop``
        Runtime-injected cancellation probe (see :func:`should_stop`).

    Deliberately ABSENT: intent, plan, current_step, generic observation /
    tool-result bags, persistence, checkpoint, configuration, or model
    selection fields.
    """

    user_input: str
    system_prompt: str
    messages: list[Any]
    model: Any               # already-bound LangChain runnable (runtime authority)
    attempt: int
    max_attempts: int
    errors: list[Any]        # list[ErrorRecord]
    completion_reason: str   # completed | empty | max_steps | error | stopped
    should_stop: Any         # Callable[[], bool] injected by the runtime


class ResearchState(AgentStateBase, total=False):
    """Per-run state for the research workflow.

    ``analysis`` / ``retrieval_plan`` are computed upstream by the
    orchestrator / retrieval policy and injected into the graph — the graph
    never derives or persists them.

    ``search`` / ``coordinator`` are the per-run collaborators the runtime
    injects: a search callable and the run's RetrievalCoordinator (the single
    budget authority — every graph-initiated search is gated and recorded
    through it).

    ``evidence`` / ``grounding_text`` / ``quality`` / ``gaps`` / ``answer``
    are produced by graph nodes composing the existing retrieval/evidence
    components. ``stream_events`` carries additive phase/retry markers the
    runtime replays on its stream channel.

    Phase 8B adds bounded accumulation state: ``original_question`` /
    ``sub_questions`` (decomposition), ``evidence_bundles`` (cross-attempt,
    URL-deduplicated, count-bounded), ``citation_manifest`` (deterministic,
    built from actual retrieved results), ``conflicts`` (descriptive,
    reused ConflictDetector output), and ``validation_detail`` (structural
    validation result). All collections are strictly bounded by
    ``research_intel`` constants — graph state never grows unbounded.
    """

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
    plan_step_index: int     # plan step this workflow run belongs to
    answer: str              # synthesized final answer
    validation: str          # "sufficient" | "insufficient" | "empty"
    stream_events: list[Any]  # ("phase", {...}) / ("retry", {...}) replayed by the runtime

    original_question: str   # untouched user question (decompose anchor)
    sub_questions: list[str]  # bounded decomposition output (≤ MAX_SUB_QUESTIONS)
    evidence_bundles: list[Any]  # accumulated bundles (≤ MAX_EVIDENCE_BUNDLES)
    citation_manifest: Any   # CitationManifest built from actual results
    conflicts: list[dict]    # descriptive contradictions (bounded)
    validation_detail: dict  # structural validation outcome of ``answer``
    metrics: dict            # bounded run counters (elapsed_ms, searches)

    # Phase 8 remediation (audit B): honest decomposition-coverage state.
    coverage_incomplete: bool     # True when the search budget left sub-questions unresearched
    unresearched_questions: list[str]  # bounded list of those sub-questions

    # per-run collaborators injected by the runtime boundary
    search: Any              # callable (query: str) -> EvidenceBundle
    coordinator: Any         # RetrievalCoordinator (single budget authority)


class RuntimeState(AgentStateBase, total=False):
    """Per-run state for the general runtime workflow (dual-path migration).

    Covers the target workflow: analyze → retrieve → reason → (act → reason)*
    → reflect → answer. Same boundaries as the research/coding states:

    - ``analysis`` comes from the Orchestrator (computed upstream or via the
      injected ``analyze`` collaborator) — never derived in-graph.
    - Retrieved context arrives through the injected ``prepare_context``
      collaborator, snapshotting what RetrievalExecutor / UnifiedRetriever /
      the evidence pipeline ALREADY produced for this run. Zero retrieval
      logic lives in-graph; there is no second retrieval system.
    - ``model`` is the runnable Cozmo bound for THIS run — graphs never
      select, substitute, or fall back. ModelUnavailableError propagates.
    - ``execute_tool`` routes every call through ToolExecutor (the sole
      execution gate); ``tool_events`` captures runtime-style
      thinking/tool_call/tool_result chunks for verbatim stream replay.
    - ``reflect`` is the optional Brain consolidation hook; default no-op
      preserves current observe-per-turn memory semantics.

    Deliberately ABSENT: storage handles, Brain, configuration, checkpoints,
    model-selection state.
    """

    analysis: Any                 # TaskAnalysis (orchestrator)
    grounding_text: str           # retrieved context snapshot (runtime-owned)
    memory_context: str
    project_context: str
    evidence_context: Any         # EvidenceContext | None (observational)
    quality: str                  # RetrievalQuality.value snapshot
    messages: list[Any]           # LangChain conversation incl. ToolMessages
    seed_messages: list[Any]      # runtime-supplied history+human seed (base_msgs[1:])
    pending_tool_calls: list[Any] # parsed AIMessage.tool_calls awaiting Act
    seen_calls: list[str]         # "name:{args}" dedup registry (legacy parity)
    observations: list[Any]       # [{name, args, output}] per executed tool
    events: list[Any]             # thinking/tool_call/tool_result/token chunks replayed verbatim
    answer: str                   # final response text

    # per-run collaborators injected by the runtime boundary
    analyze: Any                  # Callable[[str], TaskAnalysis] | None
    prepare_context: Any          # Callable[[], dict] context snapshot
    execute_tool: Any             # Callable[[str, dict, int], tuple(output, diff, success)]
    reflect: Any                  # Callable[[], Any] | None


class CodingState(AgentStateBase, total=False):
    """Per-run state for the coding workflow (Phase 7 Stage 3D; 8A seam).

    Shares the same boundaries as :class:`ResearchState`: ``analysis`` /
    ``retrieval_plan`` / ``system_prompt`` are computed upstream and injected;
    the graph never derives or persists them.

    ``run_loop`` is the per-run collaborator the runtime injects: a callable
    wrapping the runtime's ReAct agent loop for one implement attempt.

    ``events`` captures the inner agent-loop stream so the runtime can replay
    exactly what the loop would have streamed. ``stream_events`` carries
    additive phase/retry markers emitted by the graph itself.

    ``attempt`` / ``max_attempts`` (inherited from :class:`AgentStateBase`)
    bound the verify→implement re-loop. The graph is authoritative for
    ``max_attempts`` and forces it on ``run()``. ``stop_reason`` keeps the
    inner loop's terminal reason; ``completion_reason`` (base) mirrors the
    run's final outcome uniformly.

    Phase 8C adds real verification: ``verify`` is the per-run collaborator
    (injected by the runtime) that routes verification commands through
    ToolExecutor; ``verification_reports`` holds bounded
    :class:`~cozmo.graphs.coding_intel.VerificationReport` results;
    ``repair_context`` carries the bounded failure feedback injected into the
    next repair attempt; ``metrics`` tracks bounded edit/verification
    counters. The graph never executes commands itself.
    """

    analysis: Any            # TaskAnalysis (orchestrator)
    retrieval_plan: Any      # RetrievalPlan (retrieval policy)
    plan_step_index: int     # plan step this workflow run belongs to
    answer: str              # final implemented output
    stop_reason: str         # "completed" | "max_steps" | "empty" | "error" | "stopped"
    verify_note: str         # "retry" | "done" — drives the bounded re-loop
    events: list[Any]        # captured runtime stream events to replay
    stream_events: list[Any]  # ("phase", {...}) / ("retry", {...}) replayed by the runtime

    verification_reports: list[Any]  # bounded VerificationReport records
    verification_skipped: str   # "" | "no_verifier" | "no_edits"
    verification_passed: bool   # aggregate verdict of the last verify pass
    # Phase 8 remediation (audit E): structured status distinct from the bool.
    # verified | failed | unavailable | skipped — zero executed commands can
    # never be reported as "verified".
    verification_status: str
    verification_classification: str  # "" | implementation | environment | permission_denied | timeout
    edits_this_attempt: dict    # bounded per-attempt edit metrics
    repair_context: str     # bounded verification feedback for the next attempt
    metrics: dict           # attempts / edits / diff size / verifications

    # per-run collaborators injected by the runtime boundary
    run_loop: Any            # callable(state) -> (events, final, reason, ok)
    verify: Any              # callable(state) -> list[VerificationReport]
