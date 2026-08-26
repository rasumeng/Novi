"""RuntimeTracer — unified tracing interface for the runtime loop.

Consolidates trace event emission, debug event recording, step metadata,
tool usage tracking, and execution trace finalization. NoviRuntime
interacts with tracing exclusively through this class — no direct
TraceEvent/DebugTraceEvent construction in the runtime module.
"""

from __future__ import annotations

import time

from .trace import (
    DebugTraceEvent,
    ExecutionTrace,
    StepTrace,
    ToolCallTrace,
    TraceAction,
    TraceEvent,
)


class RuntimeTracer:
    """Single interface for all runtime tracing concerns.

    Responsibilities:
    - Emit TraceEvent / DebugTraceEvent
    - Record tool calls and step-level model metadata
    - Finalize execution trace (timings, bus notification)
    - EventBus integration for trace-level events
    """

    def __init__(self, event_bus=None, debug_trace: bool = False):
        self._event_bus = event_bus
        self._debug_trace = debug_trace

    # ── lifecycle ───────────────────────────────────────────────────────

    def create_trace(self, user_input: str) -> ExecutionTrace:
        """Create a new ExecutionTrace for one run."""
        return ExecutionTrace(user_input=user_input)

    # ── trace events ────────────────────────────────────────────────────

    def emit(self, trace: ExecutionTrace | None,
             action: TraceAction, category: str, summary: str,
             debug_category: str | None = None,
             debug_data: dict | None = None) -> TraceEvent:
        """Build TraceEvent, append to trace, emit to bus.

        When *debug_trace* is enabled and *debug_category* is set, also
        records a DebugTraceEvent with the supplied *debug_data*.
        """
        event = TraceEvent(action=action, category=category, summary=summary)
        if trace is not None:
            trace.user_events.append(event)
        self._bus("trace_event", trace_event=event.to_dict())
        if self._debug_trace and trace is not None and debug_category:
            trace.debug_events.append(DebugTraceEvent(
                category=debug_category, data=debug_data,
            ))
        return event

    def debug(self, trace: ExecutionTrace | None,
              category: str, data: dict | None = None):
        """Append a DebugTraceEvent when debug_trace is enabled."""
        if self._debug_trace and trace is not None:
            trace.debug_events.append(DebugTraceEvent(
                category=category, data=data,
            ))

    # ── step metadata ───────────────────────────────────────────────────

    def record_step_metadata(self, trace: ExecutionTrace | None,
                             step_idx: int,
                             model_inference_ms: float,
                             tokens_generated: int):
        """Set model-level metadata for a ReAct step."""
        if trace is None:
            return
        while len(trace.steps) <= step_idx:
            trace.steps.append(StepTrace(step=len(trace.steps)))
        trace.steps[step_idx].model_inference_ms = model_inference_ms
        trace.steps[step_idx].tokens_generated = tokens_generated

    # ── tool tracking ───────────────────────────────────────────────────

    def record_tool(self, step_idx: int, name: str, args: dict,
                    result: str, latency_ms: float, success: bool,
                    error: str | None = None,
                    fallback_used: str | None = None,
                    trace: ExecutionTrace | None = None):
        """Record a tool call in the execution trace."""
        if trace is None:
            return
        while len(trace.steps) <= step_idx:
            trace.steps.append(StepTrace(step=len(trace.steps)))
        step = trace.steps[step_idx]
        step.tool_calls.append(ToolCallTrace(
            name=name,
            args=dict(args),
            result_preview=(result or "")[:200],
            latency_ms=round(latency_ms, 2),
            success=success,
            error=error,
            fallback_used=fallback_used,
        ))

    # ── finalization ────────────────────────────────────────────────────

    def finalize(self, trace: ExecutionTrace | None,
                 stop_reason: str = "completed"):
        """Set final timings and emit completed trace via EventBus."""
        if trace is None:
            return
        trace.total_latency_ms = round(
            (time.time() - trace.started_at) * 1000, 2)
        trace.total_tool_calls = sum(
            len(s.tool_calls) for s in trace.steps)
        trace.stop_reason = stop_reason
        trace.emit_event(self._event_bus)

    # ── internal ────────────────────────────────────────────────────────

    def _bus(self, event_type: str, **data):
        if self._event_bus:
            try:
                self._event_bus.emit(event_type, **data)
            except Exception:
                pass
