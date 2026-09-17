from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from novi.jobs.job import Checkpoint
from novi.runtime.execution_context import ExecutionContext


class AgentRunStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AgentRun:
    id: str
    conversation_id: str
    goal: str
    status: AgentRunStatus
    context: ExecutionContext
    checkpoint: Optional[Checkpoint] = None
    iteration: int = 0
    token_usage: Optional[int] = None
    message_seq: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def finished(self) -> bool:
        return self.status in (AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED)
