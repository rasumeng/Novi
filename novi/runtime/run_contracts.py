"""Strict contracts for the replacement agent runtime.

These types deliberately expose no tuple, dict, or positional compatibility
surface.  They are the storage and service boundary for the new runtime.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from novi.runtime.transcript import TranscriptMessage, _json_value


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_PERMISSION = "awaiting_permission"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    @property
    def terminal(self) -> bool:
        return self in TERMINAL_RUN_STATUSES


TERMINAL_RUN_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.BLOCKED,
    RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.INTERRUPTED})


@dataclass(frozen=True)
class ModelSnapshot:
    provider: str
    model: str
    context_window: int | None = None


@dataclass(frozen=True)
class RunRequest:
    conversation_id: str
    user_message_id: str
    user_text: str
    project_id: str = ""
    workspace: str = ""
    attachments: tuple[dict[str, Any], ...] = ()
    research_selected: bool = False
    model: ModelSnapshot | None = None

    def __post_init__(self) -> None:
        if not self.conversation_id.strip():
            raise ValueError("conversation_id is required")
        if not self.user_message_id.strip():
            raise ValueError("user_message_id is required")
        if not self.user_text.strip():
            raise ValueError("user_text is required")


@dataclass(frozen=True)
class RunState:
    id: str
    request: RunRequest
    status: RunStatus = RunStatus.QUEUED
    terminal_reason: str | None = None
    current_turn: int = 0
    transcript: tuple[TranscriptMessage, ...] = ()
    context_summary: str = ""
    pending_call_ids: tuple[str, ...] = ()
    pending_permission_ids: tuple[str, ...] = ()
    model_turns: int = 0
    tool_calls: int = 0
    elapsed_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    task_id: str | None = None
    job_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def conversation_id(self) -> str:
        return self.request.conversation_id

    @property
    def finished(self) -> bool:
        return self.status.terminal

    def to_dict(self) -> dict[str, Any]:
        return _json_value(asdict(self))


class RunEventType(str, Enum):
    RUN_STARTED = "run.started"
    RUN_STATE_CHANGED = "run.state_changed"
    MESSAGE_STARTED = "message.started"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_COMPLETED = "message.completed"
    TOOL_REQUESTED = "tool.requested"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    PERMISSION_REQUESTED = "permission.requested"
    PERMISSION_RESOLVED = "permission.resolved"
    CONTEXT_COMPACTING = "context.compacting"
    CONTEXT_COMPACTED = "context.compacted"
    RUN_COMPLETED = "run.completed"
    RUN_BLOCKED = "run.blocked"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_INTERRUPTED = "run.interrupted"


EVENT_TYPES = frozenset(item.value for item in RunEventType)


@dataclass(frozen=True)
class RunEvent:
    id: str
    sequence: int
    run_id: str
    conversation_id: str
    type: RunEventType
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("event sequence must be positive")
        if not self.id or not self.run_id or not self.conversation_id:
            raise ValueError("event id, run id and conversation id are required")

    def to_dict(self) -> dict[str, Any]:
        return _json_value(asdict(self))


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.id or not self.name:
            raise ValueError("tool call id and name are required")


class ToolResultStatus(str, Enum):
    PENDING_PERMISSION = "pending_permission"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    status: ToolResultStatus
    output: str = ""
    error: str | None = None
    artifact_ids: tuple[str, ...] = ()
    diff: dict[str, Any] | None = None
    provenance: tuple[dict[str, Any], ...] = ()
    permission_request_id: str | None = None
    structured: dict[str, Any] | None = None


@dataclass(frozen=True)
class ModelTurn:
    text: str = ""
    calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
