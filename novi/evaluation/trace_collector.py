"""TraceCollector — in-memory, bounded trace collection for evaluation.

Two consumption modes:

- Passive: subscribes to EventBus TRACE_COMPLETED and stores the serialized
  trace dicts. Evaluation observes via events — it never controls execution.
- Active: ``record()`` accepts an ExecutionTrace or a serialized dict.

Bounded by a fixed capacity (deque). Trace collection is intentionally
lightweight — no persistent storage, no analytics (see Phase 8 non-goals).
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any


class TraceCollector:
    """Collects finalized ExecutionTraces for evaluation consumption."""

    def __init__(self, event_bus=None, max_traces: int = 100):
        self._max = max(1, int(max_traces))
        self._traces: deque = deque(maxlen=self._max)
        self._lock = threading.Lock()
        self._event_bus = None
        if event_bus is not None:
            self.attach(event_bus)

    # ── EventBus integration ────────────────────────────────────────────

    def attach(self, event_bus) -> "TraceCollector":
        """Subscribe to TRACE_COMPLETED events."""
        from ..runtime.event_bus import EventType

        self._event_bus = event_bus
        event_bus.on(EventType.TRACE_COMPLETED, self._on_event)
        return self

    def detach(self) -> None:
        if self._event_bus is not None:
            from ..runtime.event_bus import EventType

            try:
                self._event_bus.off(EventType.TRACE_COMPLETED, self._on_event)
            except (ValueError, KeyError):
                pass
            self._event_bus = None

    def _on_event(self, event) -> None:
        trace = getattr(event, "data", {}).get("trace")
        if trace is not None:
            self.record(trace)

    # ── Recording ───────────────────────────────────────────────────────

    def record(self, trace: Any) -> None:
        """Store an ExecutionTrace or serialized dict."""
        if trace is None:
            return
        data = trace
        if not isinstance(trace, dict):
            to_dict = getattr(trace, "to_dict", None)
            if callable(to_dict):
                data = to_dict()
            else:
                return
        if not isinstance(data, dict):
            return
        with self._lock:
            self._traces.append(dict(data))

    def clear(self) -> None:
        with self._lock:
            self._traces.clear()

    # ── Access ──────────────────────────────────────────────────────────

    @property
    def traces(self) -> list[dict]:
        with self._lock:
            return list(self._traces)

    @property
    def last(self) -> dict | None:
        with self._lock:
            return self._traces[-1] if self._traces else None

    @property
    def max_traces(self) -> int:
        return self._max

    def __len__(self) -> int:
        with self._lock:
            return len(self._traces)

    def __iter__(self):
        return iter(self.traces)
