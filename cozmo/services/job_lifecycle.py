"""
JobLifecycle — coordinator wiring planned execution to Jobs.

Milestone 5 Phase 4 (A/B/C): Job is the owner of an execution attempt. The
Runtime stays a generic plan executor and never imports Jobs or the TaskStore
(architecture guard). This composition-root object subscribes to the lifecycle
events the Runtime already emits and derives the Job-side durable state:

  profile.started    → submit Job (task_id→), start→RUNNING, append ExecutionHistory
  profile.completed  → Job complete (COMPLETED)
  profile.failed     → Job fail (FAILED)
  step.completed     → persist a Checkpoint (job_id/task_id/plan_id/step/completed_steps)

Ownership preserved:
  - Tasks remain the source of truth for intent/plan (TaskStore).
  - Jobs remain the source of truth for attempts/checkpoints (JobStore).
  - This object only threads events between the two — it holds no planning or
    execution behavior, and it lives here (composition root), not in runtime/
    orchestrator/jobs, so the import boundary tests stay intact.

This is deliberately independent of the Runtime's execution loop: the loop is
unchanged, checkpoints are written additively, and executing non-plan runs
produce no jobs.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..jobs.job import Checkpoint, JobStatus
from ..jobs.manager import JobManager

log = logging.getLogger("cozmo.services.job_lifecycle")


class JobLifecycle:
    """Event-driven coordinator: Runtime lifecycle events → Job/Checkpoint state."""

    def __init__(self, job_manager: JobManager, task_store=None):
        self._manager = job_manager
        self._task_store = task_store
        self._active: dict[str, str] = {}          # task_id → job_id
        self._completed_steps: dict[str, list] = {}  # job_id → [step_id]

    @property
    def manager(self) -> JobManager:
        return self._manager

    def active_job(self, task_id: str) -> Optional[str]:
        """The job that currently owns execution for ``task_id`` (if any)."""
        return self._active.get(task_id)

    def subscribe(self, bus) -> "JobLifecycle":
        """Register for the Runtime's plan lifecycle events. Bus is duck-typed."""
        if bus is None:
            return self
        # Route lifecycle events from the manager through the same bus so any
        # passive projection (timeline) sees them.
        if self._manager is not None:
            self._manager.set_event_sink(
                lambda etype, data: bus.emit(etype, **data)
            )
        bus.on("plan.started", self._on_plan_started)
        bus.on("plan.completed", self._on_plan_completed)
        bus.on("plan.failed", self._on_plan_failed)
        bus.on("step.completed", self._on_step_completed)
        return self

    # ── handlers ────────────────────────────────────────────────────────

    def _on_plan_started(self, event):
        task_id = event.data.get("task_id", "")
        plan_id = event.data.get("plan_id", "")
        if not task_id:
            return

        job = self._manager.submit(
            task_id=task_id,
            strategy="execute",
            metadata={"plan_id": plan_id, "created": "planned_execution"},
            status=JobStatus.CREATED,
        )
        if not self._manager.start(job.id):
            # A start failure (duplicate/progin) leaves the job in store anyway.
            log.warning("job %s could not start for task %s", job.id, task_id)
        self._active[task_id] = job.id
        self._completed_steps[job.id] = []
        self._record_execution(task_id, job.id, plan_id, reason="started")

    def _on_plan_completed(self, event):
        task_id = event.data.get("task_id", "")
        job_id = self._active.pop(task_id, None)
        if job_id is None:
            return
        self._completed_steps.pop(job_id, None)
        result = event.data.get("result", "")
        self._manager.complete(job_id, result=result)
        self._update_history(task_id, job_id,
                             status="completed", result=result)

    def _on_plan_failed(self, event):
        task_id = event.data.get("task_id", "")
        job_id = self._active.pop(task_id, None)
        if job_id is None:
            return
        self._completed_steps.pop(job_id, None)
        error = event.data.get("error", "")
        # Terminal as FAILED so the durable lifecycle records the attempt.
        self._manager.fail(job_id, error=error)
        self._update_history(task_id, job_id,
                             status="failed", error=error)

    def _on_step_completed(self, event):
        task_id = event.data.get("task_id", "")
        job_id = self._active.get(task_id)
        if job_id is None:
            return
        plan_id = event.data.get("plan_id", "")
        step_id = event.data.get("step_id", "")
        index = int(event.data.get("index", 0))
        done = self._completed_steps.setdefault(job_id, [])
        if step_id and step_id not in done:
            done.append(step_id)
        # Checkpoint captures: job_id, task_id, plan_id, current step index,
        # completed steps, and (empty) execution state. Additive — never alters
        # the current run.
        cp = Checkpoint(
            job_id=job_id,
            task_id=task_id,
            plan_id=plan_id,
            step=index + 1,      # resume at the step *after* the one just done
            completed_steps=list(done),
            messages=[],
        )
        self._manager.checkpoint(job_id, cp)

    # ── ExecutionHistory population (Phase 4B) ─────────────────────────

    def _record_execution(self, task_id: str, job_id: str, plan_id: str = "",
                          reason: str = "initial") -> None:
        """Append ONE attempt entry to the owning Task's ExecutionHistory.

        A Task may carry many Jobs over time (failed attempt, follow-up,
        retry). History is intent-level bookkeeping; it records which attempts
        ran — Job owns the attempt itself. Terminal outcome is written back by
        :meth:`_update_history`, never as a duplicate entry.
        """
        if self._task_store is None or not task_id:
            return
        task = self._task_store.get(task_id)
        if task is None:
            return
        task.execution_history.add(job_id, reason=reason or "started")
        self._save_task(task)

    def _update_history(self, task_id: str, job_id: str,
                        status: str = "", result: str = "",
                        error: str = "") -> None:
        """Write the terminal outcome back onto the attempt's history entry."""
        if self._task_store is None or not task_id:
            return
        task = self._task_store.get(task_id)
        if task is None:
            return
        entry = task.execution_history.find(job_id)
        if entry is None:
            return
        if status:
            entry.status = status
        if result:
            task.result = result[:500] if len(result) > 500 else result
            entry.result = result[:500] if len(result) > 500 else result
        if error:
            task.error = error[:500] if len(error) > 500 else error
            entry.error = error[:500] if len(error) > 500 else error
        self._save_task(task)

    def _save_task(self, task) -> None:
        try:
            self._task_store.update(task)
        except Exception as e:
            log.warning("failed to persist execution history for %s: %s",
                        task.id, e)