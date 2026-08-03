"""
EventBus — central typed event dispatch for the entire system.

Replaces runtime-inline yields with a publish/subscribe system.
WebSocket / logging / memory / reflection subscribe independently.

Architecture:
  Engine → EventBus.emit(event) → [subscriber, subscriber, ...]
                                       ↕
                              WebSocket forwarder
                              Logging subscriber
                              Memory recorder
                              Reflection learner
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

log = logging.getLogger("cozmo.event_bus")


class EventType(str, Enum):
    # Task lifecycle
    TASK_CREATED = "task.created"
    TASK_TRIAGED = "task.triaged"
    TASK_COMPLETED = "task.completed"

    # Goal
    GOAL_EXTRACTED = "goal.extracted"
    GOAL_STARTED = "goal_started"
    GOAL_COMPLETED = "goal_completed"

    # Policy
    POLICY_CHECKED = "policy.checked"

    # Memory
    MEMORY_RETRIEVED = "memory.retrieved"

    # Planning
    PLAN_CREATED = "plan.created"
    PLAN_APPROVED = "plan.approved"
    PLAN_REJECTED = "plan.rejected"

    # Job lifecycle
    JOB_CREATED = "job.created"
    JOB_STARTED = "job.started"
    JOB_PAUSED = "job.paused"
    JOB_RESUMED = "job.resumed"
    JOB_COMPLETED = "job.completed"
    JOB_FAILED = "job.failed"
    JOB_CANCELLED = "job.cancelled"

    # Execution
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    TOOL_CALLED = "tool_called"
    TOOL_RESULT = "tool_result"
    TOOL_FAILED = "tool_failed"

    # Streaming
    TOKEN = "token"
    REASONING = "reasoning"
    THINKING = "thinking"
    STATUS = "status"
    MODE_SET = "mode_set"

    # Agent state
    STATE_CHANGED = "state_changed"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    # Reflection
    REFLECTION_COMPLETED = "reflection_completed"
    REFLECTION_LESSON = "reflection.lesson"

    # Brain
    CONVERSATION_OBSERVED = "conversation.observed"

    # Capability
    CAPABILITIES_RESOLVED = "capabilities.resolved"

    # Model
    MODEL_SELECTED = "model.selected"

    # Trace
    TRACE_COMPLETED = "trace.completed"


@dataclass
class Event:
    type: str
    data: dict = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


EventHandler = Callable[[Event], None]


class EventBus:
    """Simple typed event bus. Thread-safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._wildcards: list[EventHandler] = []

    def on(self, event_type: str | EventType, handler: EventHandler):
        """Subscribe to a specific event type."""
        key = event_type.value if isinstance(event_type, EventType) else event_type
        with self._lock:
            self._subscribers.setdefault(key, []).append(handler)

    def on_any(self, handler: EventHandler):
        """Subscribe to ALL events."""
        with self._lock:
            self._wildcards.append(handler)

    def off(self, event_type: str | EventType, handler: EventHandler):
        """Unsubscribe a handler."""
        key = event_type.value if isinstance(event_type, EventType) else event_type
        with self._lock:
            self._subscribers.get(key, []).remove(handler)

    def emit(self, event_type: str | EventType, **data):
        """Emit an event to all subscribers."""
        ev = Event(
            type=event_type.value if isinstance(event_type, EventType) else event_type,
            data=data,
        )
        key = ev.type
        with self._lock:
            handlers = list(self._subscribers.get(key, []))
            wildcards = list(self._wildcards)
        for h in handlers:
            try:
                h(ev)
            except Exception as e:
                log.warning("event handler failed for %s: %s", key, e)
        for h in wildcards:
            try:
                h(ev)
            except Exception as e:
                log.warning("wildcard handler failed: %s", e)

    def clear(self):
        """Remove all subscribers."""
        with self._lock:
            self._subscribers.clear()
            self._wildcards.clear()
