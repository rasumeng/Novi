"""Strict typed AgentEvent — no tuple equality/indexing."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AgentEvent:
    type: str
    run_id: str
    conversation_id: str
    message_id: Optional[str] = None
    message: Optional[str] = None
    tool: Optional[str] = None
    args: Optional[dict] = None
    result: Optional[str] = None
    error: Optional[str] = None
    phase: Optional[str] = None
    detail: Optional[str] = None
    call_id: Optional[str] = None
    category: Optional[str] = None
    diff: Optional[dict] = None


EVENT_TYPES = {
    "run.started",
    "run.completed",
    "run.failed",
    "run.cancelled",
    "message.started",
    "message.delta",
    "message.completed",
    "tool.started",
    "tool.completed",
    "status",
    "reasoning",
    "context.compacting",
    "context.compacted",
    "plan.started",
    "plan.completed",
    "step.started",
    "step.completed",
}
