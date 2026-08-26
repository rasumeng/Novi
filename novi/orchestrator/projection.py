"""TaskLifecycleProjection — execution events → Task lifecycle.

Milestone 5 Phase 3: the Runtime is a deterministic plan executor. It never
mutates Task state and never imports the TaskStore. Instead it emits
plan/step lifecycle events on the event bus. This projection subscribes and
derives the Task's lifecycle transitions from those events, then persists via
TaskStore. Tasks are the projection's target; planning and execution stay in
their own subsystems.

Transition map (driven purely by events):
  NEW → IN_PROGRESS        on plan.started
  IN_PROGRESS → COMPLETED  on plan.completed
  IN_PROGRESS → FAILED     on plan.failed

The Plan object the Runtime mutates is the *same* reference the Task owns
(Orchestrator binds task.plan → ExecutionPlan.plan), so persisting the Task
also persists the plan/step statuses the Runtime drove.
"""

from __future__ import annotations

from typing import Optional

from ..planner.models import PlanStatus
from .task_types import TaskStatus


class TaskLifecycleProjection:
    """Subscribes to Runtime plan lifecycle events and transitions Tasks."""

    def __init__(self, task_store=None):
        self._task_store = task_store

    def subscribe(self, bus):
        """Register for the runtime's lifecycle events. Bus is duck-typed."""
        if bus is None:
            return self
        bus.on("plan.started", self._on_plan_started)
        bus.on("plan.completed", self._on_plan_completed)
        bus.on("plan.failed", self._on_plan_failed)
        return self

    # ── handlers ─────────────────────────────────────────────────────

    def _on_plan_started(self, event):
        task_id = event.data.get("task_id", "")
        task = self._load(task_id)
        if task is None:
            return
        if task.plan is not None:
            task.plan.status = PlanStatus.ACTIVE
        task.status = TaskStatus.IN_PROGRESS
        self._save(task)

    def _on_plan_completed(self, event):
        task = self._load(event.data.get("task_id", ""))
        if task is None:
            return
        if task.plan is not None:
            task.plan.status = PlanStatus.COMPLETED
        task.status = TaskStatus.COMPLETED
        task.result = event.data.get("result", "") or task.result
        task.error = ""
        self._save(task)

    def _on_plan_failed(self, event):
        task = self._load(event.data.get("task_id", ""))
        if task is None:
            return
        if task.plan is not None:
            task.plan.status = PlanStatus.FAILED
        task.status = TaskStatus.FAILED
        task.error = event.data.get("error", "") or task.error
        self._save(task)

    # ── helpers ─────────────────────────────────────────────────────

    def _load(self, task_id) -> Optional[object]:
        if self._task_store is None or not task_id:
            return None
        return self._task_store.get(task_id)

    def _save(self, task) -> None:
        if self._task_store is None:
            return
        try:
            self._task_store.update(task)
        except Exception:
            pass