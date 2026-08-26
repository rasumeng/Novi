"""
Job — an execution instance of a Task.

JobManager owns the lifecycle. A Task may spawn multiple Jobs over its lifetime
(retry, continue, fork).

Architecture:
  Task.ExecutionHistory → [Job₁, Job₂, Job₃...]
                              │
                        JobManager.{submit, pause, resume, cancel}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class JobStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETING = "completing"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"
    COMPLETED = "done"  # alias of DONE — a successful attempt is "done"
    FAILED = "failed"
    INTERRUPTED = "interrupted"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.DONE, JobStatus.COMPLETED,
                        JobStatus.ERROR, JobStatus.FAILED,
                        JobStatus.CANCELLED, JobStatus.INTERRUPTED)


@dataclass
class Checkpoint:
    """Snapshot of execution state for pause/resume.

    Captures everything the runtime needs to resume a Job: the owning
    Task/Plan ids, the current step index, the steps already completed,
    plus the message/tool state of the run itself. ``task_id`` / ``plan_id``
    are references only — a Checkpoint never owns plan/goal content.

    CHECKPOINT.STEP CONTRACT (Phase 6A — canonical, do not duplicate):
      ``Checkpoint.step`` == number of plan steps that have already completed
                          == 0-based global plan index of the NEXT step that
                             must execute.
      Producers write ``completed_step_index + 1`` (never an arbitrary
      counter).       Consumers (ContinuationService.next_step, Runtime
      ``resume_from``, JobStore recovery ``next_step``) must pass this value
      through UNCHANGED — no ``+1`` conversion is permitted anywhere.
      A checkpoint with no completed steps carries ``step == 0``.

    PHASE 6C EXECUTION-CONTEXT CONTRACT:
      ``tool_states`` and ``messages`` hold a MINIMAL, schema-constrained,
      REDACTED slice of what a step did — tool invocations (name + redacted
      args + result preview) and a bounded step summary. They are durable
      trace for audit/UI, never a replay buffer: resume is at-least-once and
      is driven exclusively by ``Checkpoint.step`` (the completed-step count),
      never by re-feeding ``messages`` into a live loop. Producers must ensure
      anything written here is already redacted (runtime redacts tool args
      before the ``STEP_COMPLETED`` event); durable state must never carry
      credentials, config snapshots, or MCP/connector session state.
    """

    job_id: str = ""
    step: int = 0
    messages: list = field(default_factory=list)
    tool_states: dict = field(default_factory=dict)
    task_id: str = ""
    plan_id: str = ""
    completed_steps: list = field(default_factory=list)
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "step": self.step,
            "messages": self.messages,
            "tool_states": self.tool_states,
            "task_id": self.task_id,
            "plan_id": self.plan_id,
            "completed_steps": list(self.completed_steps),
            "created_at": self.created_at,
        }


@dataclass
class JobEvent:
    """A single event emitted during job execution."""

    type: str = ""
    data: dict = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class Job:
    """One execution instance of a Task.

    JobManager owns the lifecycle: submit, pause, resume, cancel, retry.
    """

    id: str
    task_id: str = ""
    status: JobStatus = JobStatus.PENDING
    strategy: str = "execute"
    checkpoint: Optional[Checkpoint] = None
    retry_count: int = 0
    max_retries: int = 2
    events: list[JobEvent] = field(default_factory=list)
    error: str = ""
    result: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    @property
    def is_running(self) -> bool:
        return self.status == JobStatus.RUNNING

    @property
    def is_done(self) -> bool:
        return self.status in (
            JobStatus.DONE, JobStatus.COMPLETED, JobStatus.ERROR,
            JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED,
        )

    @property
    def can_resume(self) -> bool:
        return self.status == JobStatus.PAUSED and self.checkpoint is not None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "status": self.status.value,
            "strategy": self.strategy,
            "retry_count": self.retry_count,
            "error": self.error[:200] if self.error else "",
            "result": self.result[:500] if self.result else "",
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "created_at": self.created_at,
            "has_checkpoint": self.checkpoint is not None,
        }
