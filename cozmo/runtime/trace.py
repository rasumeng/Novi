"""ExecutionTrace — end-to-end observability for every execution decision.

Records analysis, routing, memory, planning, tools, and generation
latency as a single structured object. Emitted at end of every
run_stream() so subscribers (UI, logs, regression) get the full picture.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TraceAction(str, Enum):
    UNDERSTANDING = "understanding"
    RETRIEVING = "retrieving"
    PLANNING = "planning"
    EXECUTING = "executing"
    RESPONDING = "responding"


@dataclass(frozen=True)
class TraceActionMetadata:
    label: str
    icon: str


TRACE_ACTION_METADATA: dict[TraceAction, TraceActionMetadata] = {
    TraceAction.UNDERSTANDING: TraceActionMetadata(label="Understanding request", icon="brain"),
    TraceAction.RETRIEVING: TraceActionMetadata(label="Finding information", icon="search"),
    TraceAction.PLANNING: TraceActionMetadata(label="Planning response", icon="route"),
    TraceAction.EXECUTING: TraceActionMetadata(label="Using tools", icon="wrench"),
    TraceAction.RESPONDING: TraceActionMetadata(label="Preparing answer", icon="message"),
}


@dataclass
class TraceEvent:
    action: TraceAction = TraceAction.UNDERSTANDING
    category: str = ""
    summary: str = ""

    def to_dict(self) -> dict:
        meta = TRACE_ACTION_METADATA.get(self.action)
        action_str = self.action.value if isinstance(self.action, TraceAction) else str(self.action)
        return {
            "action": action_str,
            "category": self.category,
            "label": meta.label if meta else action_str,
            "summary": self.summary,
        }


@dataclass
class DebugTraceEvent:
    category: str = ""
    data: dict | None = None


@dataclass
class ToolCallTrace:
    name: str
    args: dict[str, Any]
    result_preview: str
    latency_ms: float
    success: bool
    error: str | None = None
    fallback_used: str | None = None


@dataclass
class StepTrace:
    step: int
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    model_inference_ms: float = 0.0
    tokens_generated: int = 0


@dataclass
class ExecutionTrace:
    request_id: str = ""
    user_input: str = ""
    started_at: float = 0.0

    intent: str = ""
    intent_confidence: float = 0.0
    user_events: list[TraceEvent] = field(default_factory=list)
    debug_events: list[DebugTraceEvent] = field(default_factory=list)
    complexity_score: int = 0
    plan_level: int = 0

    model_selected: str = ""
    model_reason: str = ""
    role: str = ""
    force_model: str = ""

    tools_available: list[str] = field(default_factory=list)
    tools_bound: list[str] = field(default_factory=list)

    plan_generated: bool = False
    plan_latency_ms: float = 0.0

    memory_queried: bool = False
    memory_result_count: int = 0
    memory_latency_ms: float = 0.0

    grounding_searched: bool = False
    grounding_latency_ms: float = 0.0
    grounding_quality: str = ""
    grounding_source_count: int = 0
    grounding_relevance_score: float = 0.0

    recovery_attempts: int = 0
    recovery_action: str = ""

    retrieval_strategy: str = ""
    retrieval_sources: str = ""
    retrieval_escalated: bool = False
    retrieval_search_count: int = 0
    retrieval_fetch_count: int = 0
    retrieval_budget_exhausted: bool = False

    steps: list[StepTrace] = field(default_factory=list)

    total_latency_ms: float = 0.0
    total_tool_calls: int = 0
    final_response_length: int = 0
    stop_reason: str = ""

    def __post_init__(self):
        if not self.request_id:
            self.request_id = uuid.uuid4().hex[:12]
        if not self.started_at:
            self.started_at = time.time()

    @property
    def elapsed(self) -> float:
        return time.time() - self.started_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "user_input": self.user_input[:200],
            "started_at": self.started_at,
            "total_latency_ms": round(self.total_latency_ms, 2),
            "intent": self.intent,
            "intent_confidence": round(self.intent_confidence, 2),
            "complexity_score": self.complexity_score,
            "plan_level": self.plan_level,
            "model_selected": self.model_selected,
            "model_reason": self.model_reason,
            "role": self.role,
            "plan_generated": self.plan_generated,
            "plan_latency_ms": round(self.plan_latency_ms, 2),
            "memory_queried": self.memory_queried,
            "memory_result_count": self.memory_result_count,
            "memory_latency_ms": round(self.memory_latency_ms, 2),
            "grounding_searched": self.grounding_searched,
            "grounding_latency_ms": round(self.grounding_latency_ms, 2),
            "grounding_quality": self.grounding_quality,
            "grounding_source_count": self.grounding_source_count,
            "grounding_relevance_score": round(self.grounding_relevance_score, 2),
            "recovery_attempts": self.recovery_attempts,
            "recovery_action": self.recovery_action,
            "retrieval_strategy": self.retrieval_strategy,
            "retrieval_sources": self.retrieval_sources,
            "retrieval_escalated": self.retrieval_escalated,
            "tool_count": len(self.tools_available),
            "tools_bound": self.tools_bound,
            "tools_used": self.total_tool_calls,
            "step_count": len(self.steps),
            "event_count": len(self.user_events),
            "final_response_length": self.final_response_length,
            "stop_reason": self.stop_reason,
        }

    def emit_event(self, event_bus) -> None:
        if event_bus is None:
            return
        try:
            from .event_bus import EventType
            event_bus.emit(EventType.TRACE_COMPLETED, trace=self.to_dict())
        except Exception:
            pass