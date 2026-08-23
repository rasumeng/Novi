"""RetrievalExecutor — execute retrieval plans and web/knowledge search.

Phase 6.5 Task 2: single entry point ``execute(ctx, user_input)``.
Runtime delegates all retrieval branching to this module.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Generator

if TYPE_CHECKING:
    from .execution_context import ExecutionContext

from .evidence import EvidenceBundle, RetrievalQuality
from .retrieval_budget import ContextAllocation
from .retrieval_coordinator import RetrievalCoordinator
from .retrieval_policy import RetrievalStrategy, SourceType
from .sources import (
    KnowledgeRetrievalSource,
    MemoryRetrievalSource,
    ProjectRetrievalSource,
    WebRetrievalSource,
)
from .trace import DebugTraceEvent, TraceAction, TraceEvent
from .unified_retrieval import SourceBinding, UnifiedRetriever

log = logging.getLogger("cozmo.retrieval")

_SEARCH_STOPWORDS = {
    "what", "is", "the", "are", "how", "to", "in", "of", "for", "a", "an",
    "and", "or", "on", "at", "by", "with", "from", "do", "does", "can",
    "will", "would", "should", "could", "did", "has", "have", "had",
    "was", "were", "be", "been", "being", "get", "got", "am", "its",
    "it's", "its", "that", "this", "these", "those", "i", "my", "me",
    "you", "your", "we", "our", "they", "them", "their", "he", "she",
    "him", "her", "his", "tell", "give", "show", "find", "help",
    "when", "where", "why", "which", "who", "whom",
}

# Intent → memory types. Moved from runtime._query_memory (Phase 9 step 4);
# intent not present queries all memory types.
_MEMORY_TYPE_FILTERS = {
    "conversation": ["conversation", "preference", "fact"],
    "research": ["reference", "fact", "conversation"],
    "coding": ["project", "learning", "reference"],
    "planning": ["project", "reference", "fact"],
    "vision": ["reference", "conversation"],
}


class RecoveryAction(str, Enum):
    """Recovery actions the executor may recommend for a run."""

    NONE = "none"
    UPGRADE_SEARCH = "upgrade_search"
    ESCALATE_WEB = "escalate_web"


@dataclass
class RecoveryDecision:
    """A recommendation produced by the executor.

    Runtime consumes the recommendation and performs the mechanical
    tool-binding side effects (grant tools, rebind runnable, append message).
    ``commit_on_grant`` marks decisions that should only be recorded when a
    capability grant actually occurred (plan-required web tooling that is
    already bound needs no recovery attempt).
    """

    action: RecoveryAction = RecoveryAction.NONE
    reason: str = ""
    commit_on_grant: bool = False


@dataclass
class RetrievalRecoveryState:
    """Per-run structured recovery state owned by the executor.

    Runtime reads this object instead of interpreting retrieval internals.
    Small by design: counts, quality snapshot, and the latest recommendation.
    """

    attempts_used: int = 0
    max_attempts: int = 1
    quality: str = ""
    retry_attempted: bool = False
    action: str = ""
    reason: str = ""
    recommendation: RecoveryAction = RecoveryAction.NONE

    @property
    def retry_available(self) -> bool:
        return self.attempts_used < self.max_attempts


class RetrievalExecutor:
    """Execute retrieval plans and direct search.

    Single entry point: ``execute(ctx, user_input)`` yields trace events
    and populates context retrieval fields.
    """

    def __init__(
        self,
        event_bus=None,
        debug_trace: bool = False,
        memory=None,
        brain=None,
        project_index=None,
        max_memory_results: int = 3,
        memory_distance_threshold: float = 0.5,
        max_project_results: int = 3,
        web_source: WebRetrievalSource | None = None,
        knowledge_source: KnowledgeRetrievalSource | None = None,
    ):
        self.event_bus = event_bus
        self.debug_trace = debug_trace
        self._memory = memory
        self._brain = brain
        self._project_index = project_index
        self.max_memory_results = max_memory_results
        self.memory_distance_threshold = memory_distance_threshold
        self.max_project_results = max_project_results
        # Adapters own pipeline/store access (Phase 9 step 3). The executor
        # composes them; it never builds an EvidenceCollector or resolves the
        # knowledge index itself. Runtime injects the knowledge source (the
        # index is a process-global resolved at composition time).
        self._web_source = web_source or WebRetrievalSource()
        self._knowledge_source = knowledge_source

    def set_project_index(self, project_index):
        """Update the project index (runtime set_config may swap it later).

        When a Brain is wired it owns project retrieval, so the swapped index
        is propagated there too (Architecture Rule #6).
        """
        self._project_index = project_index
        if self._brain is not None and hasattr(self._brain, "set_project_index"):
            self._brain.set_project_index(project_index)

    # ── recovery ownership (moved from runtime, Phase 9 step 7) ──────────

    def _recovery(self, ctx: ExecutionContext) -> RetrievalRecoveryState:
        """The run's recovery state, initializing it lazily when absent."""
        state = getattr(ctx, "retrieval_recovery", None)
        if state is None:
            state = self.init_recovery(ctx)
        return state

    def init_recovery(self, ctx: ExecutionContext) -> RetrievalRecoveryState:
        """Initialize per-run recovery state on the context."""
        state = RetrievalRecoveryState(quality=ctx.grounding_quality or "")
        ctx.retrieval_recovery = state
        return state

    @staticmethod
    def plan_requires_web(plan) -> bool:
        """Whether the plan's strategy implies web search capability."""
        if plan is None:
            return False
        return plan.strategy in (
            RetrievalStrategy.WEB_ONLY,
            RetrievalStrategy.KNOWLEDGE_THEN_WEB,
            RetrievalStrategy.MEMORY_FIRST,
        )

    def recommend_pre_loop(self, ctx: ExecutionContext) -> RecoveryDecision:
        """Pre-loop recommendation: grant web search before the agent loop.

        Two triggers, mirroring the runtime branches this replaces:
          1. The retrieval plan requires web capability (grant only when the
             capability is actually missing).
          2. Pre-loop retrieval left grounding quality below sufficient
             (recorded as an attempt regardless of current tool binding).
        """
        state = self._recovery(ctx)
        plan = self._retrieval_plan(ctx)
        if self.plan_requires_web(plan):
            return RecoveryDecision(
                action=RecoveryAction.UPGRADE_SEARCH,
                reason=f"retrieval plan requires web: {plan.strategy.value}",
                commit_on_grant=True,
            )
        if ctx.grounding_quality and ctx.grounding_quality != RetrievalQuality.SUFFICIENT.value:
            if state.retry_available:
                return RecoveryDecision(
                    action=RecoveryAction.UPGRADE_SEARCH,
                    reason=f"pre-loop: retrieval quality={ctx.grounding_quality}",
                )
        return RecoveryDecision(action=RecoveryAction.NONE, reason="")

    def recommend_when_model_answered(self, ctx: ExecutionContext) -> RecoveryDecision:
        """Mid-loop recommendation: the model finished without tool calls.

        Upgrades to web search when grounding is poor, retrieval was actually
        requested, and a recovery attempt remains. Runtime invokes this only
        on the "no tool calls" path.
        """
        state = self._recovery(ctx)
        if not ctx.grounding_quality:
            return RecoveryDecision(action=RecoveryAction.NONE,
                                    reason="no retrieval quality recorded")
        try:
            quality = RetrievalQuality(ctx.grounding_quality)
        except ValueError:
            return RecoveryDecision(action=RecoveryAction.NONE,
                                    reason="unrecognized quality")
        if quality == RetrievalQuality.SUFFICIENT:
            return RecoveryDecision(action=RecoveryAction.NONE,
                                    reason="retrieval was sufficient")
        needs_grounding = ctx.analysis is not None and ctx.analysis.grounding.needs_grounding
        had_plan = ctx.analysis is not None and ctx.analysis.retrieval_plan is not None \
            and ctx.analysis.retrieval_plan.strategy != RetrievalStrategy.NONE
        if not needs_grounding and not had_plan:
            return RecoveryDecision(action=RecoveryAction.NONE,
                                    reason="no retrieval was requested")
        if not state.retry_available:
            return RecoveryDecision(action=RecoveryAction.NONE,
                                    reason="max recovery attempts reached")
        return RecoveryDecision(
            action=RecoveryAction.UPGRADE_SEARCH,
            reason=f"retrieval quality={ctx.grounding_quality}, model answered without tools",
        )

    def recommend_after_tool(self, ctx: ExecutionContext, tool_name: str, output: str) -> RecoveryDecision:
        """Post-tool recommendation: knowledge search came back empty.

        Escalates to web search when ``search_knowledge`` returned no results
        mid-loop and a recovery attempt remains. Runtime decides whether web
        tools are still missing and performs the grant.
        """
        state = self._recovery(ctx)
        if (tool_name == "search_knowledge"
                and ("No matching knowledge found" in output or not output.strip())
                and state.retry_available):
            return RecoveryDecision(
                action=RecoveryAction.ESCALATE_WEB,
                reason="search_knowledge returned empty in loop",
                commit_on_grant=True,
            )
        return RecoveryDecision(action=RecoveryAction.NONE, reason="")

    def commit_recovery(self, ctx: ExecutionContext, decision: RecoveryDecision,
                        action: str) -> RetrievalRecoveryState:
        """Record an executed recovery action on the run's state and trace."""
        state = self._recovery(ctx)
        state.attempts_used += 1
        state.retry_attempted = True
        state.action = action
        state.recommendation = decision.action
        state.reason = decision.reason
        if ctx.trace is not None:
            ctx.trace.recovery_attempts = state.attempts_used
            ctx.trace.recovery_action = action
        return state

    @staticmethod
    def _retrieval_plan(ctx: ExecutionContext):
        """The analysis's retrieval plan, or None when no analysis/plan."""
        analysis = ctx.analysis
        if analysis is None:
            return None
        return getattr(analysis, "retrieval_plan", None)

    @staticmethod
    def _allocation_debug(plan) -> dict:
        """Policy-determined context allocation, recorded for traceability."""
        a = getattr(plan, "allocation", None)
        if a is None:
            return {}
        return {
            "max_sources": a.max_sources,
            "max_results": a.max_results,
            "max_context_chars": a.max_context_chars,
        }

    # ── internal helpers ────────────────────────────────────────────────

    # ── structured evidence (Phase 7 → live path integration) ───────────

    def _apply_web_evidence(self, ctx: ExecutionContext, bundle: EvidenceBundle) -> None:
        """Populate grounding for a collected web bundle.

        Default behavior is unchanged: ``ctx.grounding_text`` receives the raw
        merged text. The EvidenceProcessor then runs best-effort; when it
        produces a trusted non-fallback context, the model-facing grounding is
        upgraded to the rendered structured form (which preserves source
        identity/URLs and adds confidence, conflicts and attribution). Any
        processor failure keeps the raw text — a processing failure can never
        fabricate or erase evidence.
        """
        ctx.grounding_text = bundle.merged_text
        if not bundle.results:
            return
        try:
            from ..evidence import EvidenceProcessor, render_evidence_context

            processed = EvidenceProcessor().process(bundle)
        except Exception:
            log.warning("evidence processing failed; keeping raw grounding",
                        exc_info=True)
            return
        ctx.evidence_context = processed
        rendered = render_evidence_context(processed)
        if rendered:
            ctx.grounding_text = rendered

    def _emit_bus(self, event_type: str, **data):
        if self.event_bus:
            try:
                self.event_bus.emit(event_type, **data)
            except Exception:
                pass

    def _trace_event(self, action: TraceAction, category: str, summary: str,
                     trace=None,
                     debug_category: str | None = None,
                     debug_data: dict | None = None) -> TraceEvent:
        event = TraceEvent(action=action, category=category, summary=summary)
        if trace is not None:
            trace.user_events.append(event)
        self._emit_bus("trace_event", trace_event=event.to_dict())
        if self.debug_trace and trace is not None and debug_category:
            dbg = DebugTraceEvent(category=debug_category, data=debug_data)
            trace.debug_events.append(dbg)
        return event

    # ── static utilities ────────────────────────────────────────────────

    @staticmethod
    def extract_key_terms(query: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z0-9]+", query.lower())
        return [t for t in tokens if t not in _SEARCH_STOPWORDS and len(t) > 1]

    @staticmethod
    def compute_relevance(results_text: str, key_terms: list[str]) -> float:
        if not key_terms:
            return 1.0
        lower = results_text.lower()
        hits = sum(1 for t in key_terms if t in lower)
        return hits / len(key_terms)

    @staticmethod
    def reformulate_query(original: str, key_terms: list[str]) -> str:
        return " ".join(key_terms[:6])

    # ── low-level search ────────────────────────────────────────────────

    def execute_search(self, user_input: str, trace=None) -> EvidenceBundle:
        if not user_input or not user_input.strip():
            return EvidenceBundle(query=user_input)
        collector = self._web_source
        bundle = collector.collect(user_input, min_sources=2)

        if bundle.error:
            log.warning("grounding search failed for '%s': %s", user_input, bundle.error)
            bundle.quality = RetrievalQuality.FAILED
            if trace is not None and self.debug_trace:
                trace.debug_events.append(DebugTraceEvent(
                    category="retrieval",
                    data={
                        "status": "failed",
                        "error": bundle.error,
                        "query": user_input,
                        "provider": "searxng",
                        "quality": bundle.quality.value,
                    },
                ))
            return bundle

        if not bundle.results or bundle.source_count == 0:
            log.info("grounding search found no textual sources for '%s'", user_input)
            bundle.quality = RetrievalQuality.EMPTY
            if trace is not None and self.debug_trace:
                trace.debug_events.append(DebugTraceEvent(
                    category="retrieval",
                    data={
                        "status": "empty",
                        "query": user_input,
                        "provider": "searxng",
                        "quality": bundle.quality.value,
                    },
                ))
            return bundle

        key_terms = self.extract_key_terms(user_input)
        relevance = self.compute_relevance(bundle.merged_text, key_terms) if key_terms else 1.0
        if key_terms and relevance < 0.3:
            reformulated = self.reformulate_query(user_input, key_terms)
            log.info("low relevance (%.2f) for '%s', retrying with '%s'",
                     relevance, user_input, reformulated)
            retry = collector.collect(reformulated, min_sources=1)
            if retry.results and retry.source_count > 0:
                bundle = retry
                relevance = self.compute_relevance(bundle.merged_text, key_terms) if key_terms else 1.0

        has_text = bool(bundle.merged_text and bundle.merged_text.strip())
        bundle.quality = (
            RetrievalQuality.SUFFICIENT
            if has_text and relevance >= 0.3
            else RetrievalQuality.WEAK
        )
        return bundle

    def retrieve_knowledge(self, query: str, k: int = 5) -> str:
        if self._knowledge_source is None:
            return ""
        items = self._unified_knowledge_items(query, k)
        if items is None:
            # Single-source binding: preserve the legacy byte-identical path.
            try:
                result = self._knowledge_source.retrieve(
                    query, ContextAllocation(max_results=min(k, 20)))
            except Exception:
                return ""
            items = list(result.items)
        if not items:
            return ""
        lines = []
        for item in items:
            meta = item.metadata
            path = meta.get("path", "?")
            title = meta.get("title", path)
            text = item.text[:300].replace("\n", " ")
            lines.append(f"- **{title}** ({path}, score={item.score:.2f}): {text}")
        return "\n".join(lines)

    def _unified_knowledge_items(self, query: str, k: int):
        """M5 unified candidate pool: knowledge (+ graph) merged with memory
        and project context when those stores are wired.

        Returns the minimum-sufficient selected items, the full ranked pool
        when selection comes back empty (empty-query edge), or None when only
        the knowledge source participates — in which case the caller keeps
        the pre-M5 behavior exactly.
        """
        bindings = []
        memory_store = self._brain if self._brain is not None else self._memory
        if memory_store is not None:
            bindings.append(
                SourceBinding("memory", MemoryRetrievalSource(memory_store))
            )
        project_store = self._brain if self._brain is not None else self._project_index
        if project_store is not None:
            bindings.append(
                SourceBinding("project", ProjectRetrievalSource(project_store))
            )
        if not bindings:
            return None
        bindings.append(SourceBinding("knowledge", self._knowledge_source))

        allocation = ContextAllocation(max_results=min(k, 20))
        outcome = UnifiedRetriever().retrieve(query, allocation, bindings)
        selected = outcome.selected
        if not selected:
            # Nothing survived ranking/dedup/budget — report empty rather
            # than re-querying through the legacy path.
            return [] if not outcome.merged.items else list(outcome.merged.items)

        # Knowledge-grounding lines are knowledge-shaped; a project/memory
        # row without path/title metadata still renders via its id/text.
        shaped = [
            it for it in selected
            if "path" in it.metadata or it.source == "knowledge"
        ]
        return shaped or list(selected)

    # ── single entry point ──────────────────────────────────────────────

    def execute(
        self, ctx: ExecutionContext, user_input: str
    ) -> Generator[tuple[str, Any], None, None]:
        """Single retrieval entry point. Yields trace events for the stream.

        Dispatch order:
          1. Structured retrieval plan (non-NONE strategy)
          2. Analysis-based grounding needed
          3. Analysis exists but no grounding
          4. Research intent fallback
          5. No-op (no retrieval required)

        After dispatch, builds and configures the run's retrieval coordinator
        (budget per plan strategy, cache seeded from pre-loop grounding) and
        populates memory/project prompt context.
        """
        self.init_recovery(ctx)
        if (ctx.analysis is not None
                and ctx.analysis.retrieval_plan is not None
                and ctx.analysis.retrieval_plan.strategy != RetrievalStrategy.NONE):
            yield from self._execute_retrieval_plan(ctx, user_input)
        elif ctx.analysis is not None and ctx.analysis.grounding.needs_grounding:
            yield from self._execute_grounding_search(ctx, user_input)
        elif ctx.analysis is not None:
            yield from self._emit_no_grounding(ctx)
        elif ctx.intent_str == "research":
            yield from self._execute_direct_web(ctx, user_input)

        self._setup_coordinator(ctx)
        self._setup_memory_context(ctx, user_input)
        self._setup_project_context(ctx, user_input)
        self._recovery(ctx).quality = ctx.grounding_quality or ""

    # ── coordinator lifecycle (ownership moved from runtime, Phase 9 step 2) ──

    def _setup_coordinator(self, ctx: ExecutionContext) -> None:
        """Build and configure the retrieval coordinator for this run.

        Runtime no longer prepares the coordinator; it consumes the ready
        instance via ``ctx.retrieval_coordinator`` / ``ctx.retrieval_budget``.
        """
        coord = RetrievalCoordinator()
        if ctx.analysis is not None and ctx.analysis.retrieval_plan is not None:
            plan_strat = ctx.analysis.retrieval_plan.strategy
            if plan_strat in (
                RetrievalStrategy.WEB_ONLY,
                RetrievalStrategy.KNOWLEDGE_THEN_WEB,
                RetrievalStrategy.MEMORY_FIRST,
            ):
                coord.budget.max_web_searches = 1
                coord.budget.max_web_fetches = 1
            elif plan_strat in (
                RetrievalStrategy.KNOWLEDGE_ONLY,
                RetrievalStrategy.PROJECT_FIRST,
            ):
                coord.budget.max_web_searches = 0
                coord.budget.max_web_fetches = 0
        if ctx.grounding_text and ctx.grounding_quality:
            coord.seed_cache(ctx.user_input, ctx.grounding_text)
        ctx.retrieval_coordinator = coord
        ctx.retrieval_budget = coord.budget

    # ── memory + project prompt context (ownership moved from runtime, Phase 9 step 4) ──

    def _setup_memory_context(self, ctx: ExecutionContext, user_input: str) -> None:
        """Populate ``ctx.memory_context`` for prompt construction.

        Mirrors the removed runtime ``_query_memory`` path: intent type filter,
        importance ranking, truncation, and section formatting are preserved
        exactly. Runtime no longer queries memory directly.

        Source participation is plan-driven (Phase 9 step 5): the executor
        queries memory only when the policy's plan lists it as a source.
        """
        if self._memory is None and self._brain is None:
            return
        plan = self._retrieval_plan(ctx)
        if plan is not None:
            should_query = SourceType.MEMORY in plan.sources
        else:
            analysis = ctx.analysis
            if analysis is not None:
                evidence = getattr(analysis, "evidence", None)
                should_query = bool(getattr(evidence, "needs_memory", False)) if evidence is not None else False
            else:
                should_query = ctx.intent_str in ("conversation", "planning")
        if not should_query:
            return

        # Architecture Rule #4: the runtime asks the Brain for context; the
        # Brain decides how that context is assembled. When a Brain is wired,
        # the memory source asks it for flat rows (temporary RecallResult→row
        # compat adapter). Legacy MemoryManager remains the no-brain fallback
        # (WebUI/tools keep using it directly).
        store = self._brain if self._brain is not None else self._memory
        source = MemoryRetrievalSource(
            store,
            memory_types=_MEMORY_TYPE_FILTERS.get(ctx.intent_str),
            distance_threshold=self.memory_distance_threshold,
        )
        t0 = time.time()
        result = source.retrieve(
            user_input,
            ContextAllocation(max_results=self.max_memory_results * 3),
        )
        if result.quality == RetrievalQuality.FAILED:
            return
        if ctx.trace is not None:
            ctx.trace.memory_queried = True
        if result.quality != RetrievalQuality.SUFFICIENT or not result.items:
            return
        try:
            items = self._rank_memories(result.items)[:self.max_memory_results]
            if ctx.trace is not None:
                ctx.trace.memory_result_count = len(items)
                ctx.trace.memory_latency_ms = round((time.time() - t0) * 1000, 2)
            ctx.memory_context = self._format_memory_context(items)
        except Exception:
            return

    @staticmethod
    def _rank_memories(items: list) -> list:
        """Rank memories by frequency × recency × relevance.

        Moved unchanged from runtime._rank_memories (Phase 9 step 4).
        """
        now = datetime.now()
        scored = []
        for r in items:
            meta = r.metadata
            freq = meta.get("frequency", 1)
            ts = meta.get("timestamp", "")
            try:
                age_hours = (now - datetime.fromisoformat(ts)).total_seconds() / 3600 if ts else 24
            except Exception:
                age_hours = 24
            recency = max(0.1, 1.0 - age_hours / 168)
            distance = meta.get("distance", 0.5)
            importance = freq * recency * (1.0 - distance)
            scored.append((importance, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored]

    @staticmethod
    def _format_memory_context(items: list) -> str:
        """Format ranked memories as type-labeled sections for the prompt.

        Moved unchanged from runtime._query_memory (Phase 9 step 4).
        """
        sections = []
        type_labels = set()
        for r in items:
            t = r.metadata.get("type", "")
            if t not in type_labels:
                type_labels.add(t)
                sections.append(f"\n--- {t.capitalize()} ---") if t else None
            sections.append(f"  {r.text}")
        return "\n".join(sections) if sections else ""

    def _setup_project_context(self, ctx: ExecutionContext, user_input: str) -> None:
        """Populate ``ctx.project_context`` for coding/work intents.

        Replaces runtime's inline ``ProjectIndex.query`` call. Participation is
        plan-driven (Phase 9 step 5): project context is queried when the
        policy's plan lists the project source.
        """
        if self._project_index is None and self._brain is None:
            return
        plan = self._retrieval_plan(ctx)
        if plan is not None:
            should_query = SourceType.PROJECT in plan.sources
        else:
            should_query = ctx.intent_str in ("coding", "work")
        if not should_query:
            return
        # Architecture Rule #6: the runtime asks the Brain for context. When a
        # Brain is wired, the project source queries it (which owns the project
        # index internally). Legacy ProjectIndex remains the no-brain fallback.
        store = self._brain if self._brain is not None else self._project_index
        source = ProjectRetrievalSource(store)
        result = source.retrieve(
            user_input,
            ContextAllocation(max_results=self.max_project_results),
        )
        if result.quality == RetrievalQuality.SUFFICIENT and result.items:
            ctx.project_context = result.items[0].text

    # ── retrieval plan strategies (private) ──────────────────────────────

    def _execute_retrieval_plan(
        self, ctx: ExecutionContext, user_input: str
    ) -> Generator[tuple[str, Any], None, None]:
        plan = ctx.analysis.retrieval_plan
        ctx.trace.retrieval_strategy = plan.strategy.value
        ctx.trace.retrieval_sources = ",".join(s.value for s in plan.sources)
        ctx.retrieval_plan = plan

        if plan.strategy == RetrievalStrategy.NONE:
            event = self._trace_event(
                action=TraceAction.RESPONDING,
                category="knowledge",
                summary="This is a stable concept well-covered in available knowledge.",
                trace=ctx.trace,
                debug_category="grounding",
                debug_data={
                    "retrieval_plan": plan.strategy.value,
                    "reason": plan.reason,
                },
            )
            yield ("trace", event)
            return

        if plan.strategy == RetrievalStrategy.WEB_ONLY:
            event = self._trace_event(
                action=TraceAction.RETRIEVING,
                category="information_retrieval",
                summary="This question may depend on recent information. Looking up current data.",
                trace=ctx.trace,
                debug_category="grounding",
                debug_data={
                    "retrieval_plan": plan.strategy.value,
                    "reason": plan.reason,
                    "allocation": self._allocation_debug(plan),
                },
            )
            yield ("trace", event)
            yield ("thinking", event.action.value, event.summary, user_input)
            t0 = time.time()
            bundle = self.execute_search(user_input, trace=ctx.trace)
            self._apply_web_evidence(ctx, bundle)
            ctx.grounding_error = bundle.error
            ctx.grounding_quality = bundle.quality.value if bundle.quality else ""
            ctx.trace.grounding_searched = bool(ctx.grounding_text) or bool(bundle.error)
            ctx.trace.grounding_latency_ms = round((time.time() - t0) * 1000, 2)
            ctx.trace.grounding_quality = ctx.grounding_quality
            ctx.trace.grounding_source_count = bundle.source_count
            ctx.trace.grounding_relevance_score = bundle.quality.value if bundle.quality else 0.0
            return

        if plan.strategy in (RetrievalStrategy.KNOWLEDGE_ONLY, RetrievalStrategy.PROJECT_FIRST):
            event = self._trace_event(
                action=TraceAction.RETRIEVING,
                category="knowledge_retrieval",
                summary="Searching local knowledge base for relevant information.",
                trace=ctx.trace,
                debug_category="retrieval",
                debug_data={
                    "retrieval_plan": plan.strategy.value,
                    "reason": plan.reason,
                    "allocation": self._allocation_debug(plan),
                },
            )
            yield ("trace", event)
            yield ("thinking", "Searching knowledge base...", "", user_input)
            t0 = time.time()
            kb_text = self.retrieve_knowledge(user_input)
            ctx.grounding_text = kb_text
            ctx.grounding_quality = RetrievalQuality.SUFFICIENT.value if kb_text else RetrievalQuality.EMPTY.value
            ctx.trace.grounding_searched = bool(kb_text)
            ctx.trace.grounding_latency_ms = round((time.time() - t0) * 1000, 2)
            ctx.trace.grounding_quality = ctx.grounding_quality
            ctx.trace.grounding_source_count = 1 if kb_text else 0
            ctx.trace.grounding_relevance_score = 1.0 if kb_text else 0.0
            return

        if plan.strategy in (RetrievalStrategy.KNOWLEDGE_THEN_WEB, RetrievalStrategy.MEMORY_FIRST):
            event = self._trace_event(
                action=TraceAction.RETRIEVING,
                category="information_retrieval",
                summary="Searching local knowledge first, then web if needed.",
                trace=ctx.trace,
                debug_category="retrieval",
                debug_data={
                    "retrieval_plan": plan.strategy.value,
                    "reason": plan.reason,
                    "allocation": self._allocation_debug(plan),
                },
            )
            yield ("trace", event)
            yield ("thinking", "Searching knowledge base...", "", user_input)
            t0 = time.time()
            kb_text = self.retrieve_knowledge(user_input)
            if kb_text:
                ctx.grounding_text = kb_text
                ctx.grounding_quality = RetrievalQuality.SUFFICIENT.value
                ctx.retrieval_escalated = False
                ctx.trace.grounding_searched = True
                ctx.trace.grounding_latency_ms = round((time.time() - t0) * 1000, 2)
                ctx.trace.grounding_quality = ctx.grounding_quality
                ctx.trace.grounding_source_count = 1
                ctx.trace.grounding_relevance_score = 1.0
                return

            ctx.retrieval_escalated = True
            ctx.trace.retrieval_escalated = True
            yield ("thinking", "Escalating to web search...", "", user_input)
            bundle = self.execute_search(user_input, trace=ctx.trace)
            self._apply_web_evidence(ctx, bundle)
            ctx.grounding_error = bundle.error
            ctx.grounding_quality = bundle.quality.value if bundle.quality else ""
            ctx.trace.grounding_searched = bool(ctx.grounding_text) or bool(bundle.error)
            ctx.trace.grounding_latency_ms = round((time.time() - t0) * 1000, 2)
            ctx.trace.grounding_quality = ctx.grounding_quality
            ctx.trace.grounding_source_count = bundle.source_count
            ctx.trace.grounding_relevance_score = bundle.quality.value if bundle.quality else 0.0
            return

    def _execute_grounding_search(
        self, ctx: ExecutionContext, user_input: str
    ) -> Generator[tuple[str, Any], None, None]:
        grounding_data = {
            "needs_grounding": True,
            "confidence": ctx.analysis.grounding.confidence,
            "source": ctx.analysis.grounding.source,
            "reason": ctx.analysis.grounding.reason,
            "signals": [s.type for s in ctx.analysis.evidence.signals],
            "evidence_confidence": ctx.analysis.evidence.confidence,
        }
        event = self._trace_event(
            action=TraceAction.RETRIEVING,
            category="information_retrieval",
            summary="This question may depend on recent information. Looking up current data.",
            trace=ctx.trace,
            debug_category="grounding",
            debug_data=grounding_data,
        )
        yield ("trace", event)
        yield ("thinking", event.action.value, event.summary, user_input)
        t0 = time.time()
        bundle = self.execute_search(user_input, trace=ctx.trace)
        self._apply_web_evidence(ctx, bundle)
        ctx.grounding_error = bundle.error
        ctx.grounding_quality = bundle.quality.value if bundle.quality else ""
        ctx.trace.grounding_searched = bool(ctx.grounding_text) or bool(bundle.error)
        ctx.trace.grounding_latency_ms = round((time.time() - t0) * 1000, 2)
        ctx.trace.grounding_quality = ctx.grounding_quality
        ctx.trace.grounding_source_count = bundle.source_count
        ctx.trace.grounding_relevance_score = bundle.quality.value if bundle.quality else 0.0

    def _emit_no_grounding(
        self, ctx: ExecutionContext
    ) -> Generator[tuple[str, Any], None, None]:
        grounding_data = {
            "needs_grounding": False,
            "confidence": ctx.analysis.grounding.confidence,
            "source": ctx.analysis.grounding.source,
            "reason": ctx.analysis.grounding.reason,
            "signals": [s.type for s in ctx.analysis.evidence.signals],
            "evidence_confidence": ctx.analysis.evidence.confidence,
        }
        event = self._trace_event(
            action=TraceAction.RESPONDING,
            category="knowledge",
            summary="This is a stable concept well-covered in available knowledge.",
            trace=ctx.trace,
            debug_category="grounding",
            debug_data=grounding_data,
        )
        yield ("trace", event)

    def _execute_direct_web(
        self, ctx: ExecutionContext, user_input: str
    ) -> Generator[tuple[str, Any], None, None]:
        intent_str = ctx.intent_str
        event = self._trace_event(
            action=TraceAction.RETRIEVING,
            category="information_retrieval",
            summary="This question depends on current information. Looking up data.",
            trace=ctx.trace,
            debug_category="grounding",
            debug_data={
                "intent": intent_str,
                "source": "fallback_intent",
            },
        )
        yield ("trace", event)
        yield ("thinking", event.action.value, event.summary, user_input)
        t0 = time.time()
        bundle = self.execute_search(user_input, trace=ctx.trace)
        self._apply_web_evidence(ctx, bundle)
        ctx.grounding_error = bundle.error
        ctx.trace.grounding_searched = bool(ctx.grounding_text) or bool(bundle.error)
        ctx.trace.grounding_latency_ms = round((time.time() - t0) * 1000, 2)
        ctx.grounding_quality = bundle.quality.value if bundle.quality else ""
        ctx.trace.grounding_quality = ctx.grounding_quality
        ctx.trace.grounding_source_count = bundle.source_count
        ctx.trace.grounding_relevance_score = bundle.quality.value if bundle.quality else 0.0
