"""RetrievalExecutor — execute retrieval plans and web/knowledge search.

Phase 6.5 Task 2: single entry point ``execute(ctx, user_input)``.
Runtime delegates all retrieval branching to this module.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any, Generator

if TYPE_CHECKING:
    from .execution_context import ExecutionContext

from .evidence import EvidenceBundle, EvidenceCollector, RetrievalQuality
from .retrieval_policy import RetrievalStrategy
from .trace import DebugTraceEvent, TraceAction, TraceEvent

from ..memory.knowledge_index import get_knowledge_index

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


class RetrievalExecutor:
    """Execute retrieval plans and direct search.

    Single entry point: ``execute(ctx, user_input)`` yields trace events
    and populates context retrieval fields.
    """

    def __init__(self, event_bus=None, debug_trace: bool = False):
        self.event_bus = event_bus
        self.debug_trace = debug_trace

    # ── internal helpers ────────────────────────────────────────────────

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
        collector = EvidenceCollector()
        bundle = collector.collect(user_input, min_sources=2)

        if bundle.error:
            log.warning("grounding search failed for '%s': %s", user_input, bundle.error)
            bundle.quality = RetrievalQuality.FAILED
            if trace is not None:
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
            if trace is not None:
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
        try:
            ki = get_knowledge_index()
        except Exception:
            return ""
        if ki is None:
            return ""
        try:
            results = ki.search(query, k=min(k, 20))
        except Exception:
            return ""
        if not results:
            return ""
        lines = []
        for r in results:
            meta = r.get("metadata", {})
            path = meta.get("path", "?")
            title = meta.get("title", path)
            score = r.get("score", 0.0)
            text = r.get("text", "")[:300].replace("\n", " ")
            lines.append(f"- **{title}** ({path}, score={score:.2f}): {text}")
        return "\n".join(lines)

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
        """
        if (ctx.analysis is not None
                and ctx.analysis.retrieval_plan is not None
                and ctx.analysis.retrieval_plan.strategy != RetrievalStrategy.NONE):
            yield from self._execute_retrieval_plan(ctx, user_input)
            return

        if ctx.analysis is not None and ctx.analysis.grounding.needs_grounding:
            yield from self._execute_grounding_search(ctx, user_input)
            return

        if ctx.analysis is not None:
            yield from self._emit_no_grounding(ctx)
            return

        if ctx.intent_str == "research":
            yield from self._execute_direct_web(ctx, user_input)
            return

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
                },
            )
            yield ("trace", event)
            yield ("thinking", event.action.value, event.summary, user_input)
            t0 = time.time()
            bundle = self.execute_search(user_input, trace=ctx.trace)
            ctx.grounding_text = bundle.merged_text
            ctx.grounding_error = bundle.error
            ctx.grounding_quality = bundle.quality.value if bundle.quality else ""
            ctx.trace.grounding_searched = bool(ctx.grounding_text) or bool(bundle.error)
            ctx.trace.grounding_latency_ms = round((time.time() - t0) * 1000, 2)
            ctx.trace.grounding_quality = ctx.grounding_quality
            ctx.trace.grounding_source_count = bundle.source_count
            ctx.trace.grounding_relevance_score = bundle.quality.value if bundle.quality else 0.0
            return

        if plan.strategy == RetrievalStrategy.KNOWLEDGE_ONLY:
            event = self._trace_event(
                action=TraceAction.RETRIEVING,
                category="knowledge_retrieval",
                summary="Searching local knowledge base for relevant information.",
                trace=ctx.trace,
                debug_category="retrieval",
                debug_data={
                    "retrieval_plan": plan.strategy.value,
                    "reason": plan.reason,
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

        if plan.strategy == RetrievalStrategy.KNOWLEDGE_THEN_WEB:
            event = self._trace_event(
                action=TraceAction.RETRIEVING,
                category="information_retrieval",
                summary="Searching local knowledge first, then web if needed.",
                trace=ctx.trace,
                debug_category="retrieval",
                debug_data={
                    "retrieval_plan": plan.strategy.value,
                    "reason": plan.reason,
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
            ctx.grounding_text = bundle.merged_text
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
        ctx.grounding_text = bundle.merged_text
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
        ctx.grounding_text = bundle.merged_text
        ctx.grounding_error = bundle.error
        ctx.trace.grounding_searched = bool(ctx.grounding_text) or bool(bundle.error)
        ctx.trace.grounding_latency_ms = round((time.time() - t0) * 1000, 2)
        ctx.grounding_quality = bundle.quality.value if bundle.quality else ""
        ctx.trace.grounding_quality = ctx.grounding_quality
        ctx.trace.grounding_source_count = bundle.source_count
        ctx.trace.grounding_relevance_score = bundle.quality.value if bundle.quality else 0.0
