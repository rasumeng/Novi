"""
ContinuationService — resolve "continue / resume previous work".

Milestone 5 Phase 5B: read-only resolver that joins TaskStore + JobStore to
surface resumable work. It is deliberately passive — it never executes, never
creates Jobs, and never mutates Task or Job state. It only answers the
question "what can the user continue, and where did it stop?"

Ownership (guard: tests/test_task_job_runtime_boundaries.py):
  - Brain        owns conversations only.
  - Task         owns intent / plan / history (TaskStore).
  - Job          owns attempts / checkpoints (JobStore).
  - Runtime      executes (untouched by this module).

Continuation *logic* is a cross-subsystem join, so it must live in the
composition root (co/services) — never in orchestrator/ (which may not import
jobs), jobs/ (which may not import task state), or runtime/ (which may not
import persistence). This module only *queries* both stores through their
public APIs and assembles user-facing ResumeTargets.

Effort target: support
  1. Same-conversation — prefer a non-terminal task in the current thread.
  2. Global            — any task with a paused/interrupted job + checkpoint.
  3. Ambiguity         — return candidates and let the caller decide instead
     of silently picking one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..jobs.job import Checkpoint, Job, JobStatus
from ..orchestrator.task_types import Task, TaskStatus

log = logging.getLogger("cozmo.services.continuation")

# A task is no longer resumable once it reached a dead-end.
_TASK_TERMINAL = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.ERROR,
        TaskStatus.CANCELLED,
        TaskStatus.ARCHIVED,
    }
)

# A job is a resume candidate once it stalled mid-flight with a checkpoint.
_RESUME_JOB_STATUSES = frozenset(
    {
        JobStatus.RUNNING,      # crashed mid-run, pre mark_interrupted
        JobStatus.PAUSED,       # paused + checkpoint (can_resume)
        JobStatus.INTERRUPTED,  # startup recovery marked it
    }
)


@dataclass
class ResumeTarget:
    """User-facing description of one resumable piece of work.

    References only — the continuation layer never takes ownership of the
    underlying objects; it holds ids so a later phase can reload the exact
    plan and checkpoint when asked to actually resume.

    ``next_step`` equals ``checkpoint.step`` (Phase 6A contract): the number
    of already-completed plan steps and the 0-based index of the next step
    to execute. Consumers pass it straight to Runtime as ``resume_from`` —
    no ``+1`` is applied here or anywhere downstream.
    """

    task_id: str
    job_id: str
    plan_id: str
    checkpoint: Optional[Checkpoint]
    next_step: int
    task_status: str = ""
    title: str = ""
    conversation_id: str = ""
    updated_at: str = ""
    started_at: str = ""
    completed_steps: list = field(default_factory=list)
    progress: str = ""

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "job_id": self.job_id,
            "plan_id": self.plan_id,
            "next_step": self.next_step,
            "task_status": self.task_status,
            "title": self.title,
            "conversation_id": self.conversation_id,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed": len(self.completed_steps),
            "progress": self.progress,
            "has_checkpoint": self.checkpoint is not None,
        }


class ContinuationService:
    """Query layer that surfaces resumable work from TaskStore + JobStore."""

    def __init__(self, task_store=None, job_store=None, job_manager=None):
        self._task_store = task_store
        self._job_store = job_store
        self._job_manager = job_manager

    @property
    def task_store(self):
        return self._task_store

    @property
    def job_store(self):
        return self._job_store

    # ── public API ────────────────────────────────────────────────────────

    def candidates(self, conversation_id: str | None = None,
                   limit: int = 10) -> list[ResumeTarget]:
        """Ranked resumable work. Never returns execution — only candidates.

        Ordering:
        1. Same-conversation match first (strongly-scoped, most likely intent
           behind "keep going").
        2. Then every globally resumable task, most recently touched first.

        Returns an empty list when there is nothing to resume.
        """
        tasks = self._task_store.list() if self._task_store else []
        by_task: dict[str, Task] = {t.id: t for t in tasks}
        jobs = self._job_store.list() if self._job_store else []

        # Group resumable jobs by task, keeping the latest attempt per task.
        resumable: dict[str, Job] = {}
        for job in jobs:
            if job.status not in _RESUME_JOB_STATUSES:
                continue
            prev = resumable.get(job.task_id)
            if prev is None or (job.completed_at or job.created_at) >= (
                    prev.completed_at or prev.created_at):
                resumable[job.task_id] = job

        targets = []
        for task_id, job in resumable.items():
            task = by_task.get(task_id)
            if task is None or task.status in _TASK_TERMINAL:
                continue
            target = self._build_target(task, job)
            if target is not None:
                targets.append(target)

        # Stable recency order for the global set.
        targets.sort(key=lambda t: t.updated_at or "", reverse=True)

        # Promote a same-conversation candidate to the front.
        if conversation_id:
            for i, t in enumerate(targets):
                if t.conversation_id == conversation_id:
                    targets.insert(0, targets.pop(i))
                    break

        return targets[:limit] if limit else targets

    def resolve(self, conversation_id: str | None = None,
                limit: int = 10) -> list[ResumeTarget]:
        """Alias of :meth:`candidates` — the ranked candidate list."""
        return self.candidates(conversation_id=conversation_id, limit=limit)

    def recommended(self, conversation_id: str | None = None
                    ) -> Optional[ResumeTarget]:
        """The single best candidate, or None when none / when ambiguous.

        Returns None if there is no resumable work OR when the top candidates
        are genuinely distinct pieces of work (a caller that must not guess
        silently should prefer :meth:`candidates` and ask the user).
        """
        cands = self.candidates(conversation_id=conversation_id, limit=2)
        if not cands:
            return None
        first = cands[0]
        # Same-conversation match is unambiguous enough to prefer.
        if conversation_id and first.conversation_id == conversation_id:
            return first
        # Across conversations, several distinct pieces of work → refuse to
        # pick quietly when the top two are genuinely different tasks.
        if len(cands) > 1 and cands[0].task_id != cands[1].task_id:
            return None
        return first

    # ── internal ─────────────────────────────────────────────────────────

    def _build_target(self, task: Task, job: Job) -> Optional[ResumeTarget]:
        cp = job.checkpoint
        completed = list(cp.completed_steps) if cp else []
        # Checkpoint.step IS the resume pointer (Phase 6A contract): the
        # completed-step count equals the 0-based index of the next step.
        # Pass it through unchanged — never +1.
        next_step = cp.step if cp is not None else 0

        plan_id = ""
        if cp is not None and cp.plan_id:
            plan_id = cp.plan_id
        elif task.plan is not None:
            plan_id = getattr(task.plan, "id", "")

        title = task.raw_goal or ((task.goal.text if task.goal else "") or "")

        total = 0
        if task.plan is not None and hasattr(task.plan, "step_count"):
            total = task.plan.step_count
        progress = f"{len(completed)}/{total}" if total else ""

        return ResumeTarget(
            task_id=task.id,
            job_id=job.id,
            plan_id=plan_id,
            checkpoint=cp,
            next_step=next_step,
            task_status=task.status.value if task.status else "",
            title=title,
            conversation_id=task.conversation_id or "",
            updated_at=task.updated_at or "",
            started_at=job.started_at or "",
            completed_steps=completed,
            progress=progress,
        )