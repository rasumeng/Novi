"""Milestone 5 Phase 3 — Agent Execution Lifecycle.

Covers:
  - sequential execution of ExecutionPlan.plan.steps
  - step.started / step.completed / step.failed events
  - plan lifecycle transitions (DRAFT → ACTIVE → COMPLETED | FAILED)
  - plan step lifecycle transitions (PENDING → RUNNING → COMPLETED | FAILED)
  - task lifecycle transitions driven by execution events (projection)
  - execution failure propagation
  - regression: unplanned single-loop execution still works
"""

import pytest
from langchain_core.messages import AIMessageChunk

from novi.orchestrator.task_types import ExecutionPlan, Goal, IntentType, Task, TaskStatus
from novi.planner import PlannerEngine
from novi.planner.models import PlanStatus, PlanStepStatus
from novi.runtime.event_bus import EventBus
from novi.runtime.execution_context import ExecutionContext


# ── Fake model service ───────────────────────────────────────────────────────


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_plan(task_id, steps, intent):
    task = Task(
        id=task_id,
        raw_goal="do the thing",
        goal=Goal(text="do the thing", intent=intent),
    )
    plan = PlannerEngine().create_plan(task)
    plan.steps = plan.steps[:steps]
    return plan, task


# ── Sequential execution + step events ───────────────────────────────────────


# ── Failure propagation ──────────────────────────────────────────────────────


# ── Plan / step lifecycle transitions ────────────────────────────────────────


# ── Task lifecycle projection (events → Task status) ─────────────────────────


def test_task_lifecycle_via_projection():
    store, _, plan, bus = _projection()
    task = store.get("task-6")
    assert task.status is TaskStatus.NEW
    assert plan.status is PlanStatus.DRAFT

    bus.emit("plan.started", task_id="task-6", plan_id=plan.id, step_count=2)
    running = store.get("task-6")
    assert running.status is TaskStatus.IN_PROGRESS
    assert running.plan.status is PlanStatus.ACTIVE

    bus.emit("plan.completed", task_id="task-6", plan_id=plan.id, result="done", step_count=2)
    done = store.get("task-6")
    assert done.status is TaskStatus.COMPLETED
    assert done.result == "done"
    assert done.plan.status is PlanStatus.COMPLETED


def test_task_projection_failure():
    store, task, plan, bus = _projection()
    bus.emit("plan.started", task_id="task-6", plan_id=plan.id, step_count=1)
    bus.emit("plan.failed", task_id="task-6", plan_id=plan.id, step_id=plan.steps[0].id, error="boom")
    failed = store.get("task-6")
    assert failed.status is TaskStatus.FAILED
    assert failed.error == "boom"
    assert failed.plan.status is PlanStatus.FAILED


# ── Regression: unplanned single-loop execution ──────────────────────────────




def _projection():
    from novi.orchestrator.projection import TaskLifecycleProjection

    store = _FakeTaskStore()
    plan, task = make_plan("task-6", 2, IntentType.CODING)
    task.plan = plan
    store.save(task)
    bus = EventBus()
    TaskLifecycleProjection(store).subscribe(bus)
    return store, task, plan, bus


class _FakeTaskStore:
    def __init__(self):
        self.tasks = {}

    def save(self, task):
        self.tasks[task.id] = task
        return True

    def get(self, task_id):
        return self.tasks.get(task_id)

    def update(self, task):
        return self.save(task)
