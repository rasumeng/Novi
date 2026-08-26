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
from novi.runtime.runtime import NoviRuntime


# ── Fake model service ───────────────────────────────────────────────────────

def _chunk(text):
    return AIMessageChunk(content=text)


class FakeModel:
    """Duck-typed model service returning canned per-call answers.

    ``chunks`` is a list, one entry per model call:
        str                 → streamed as text (no tool calls) → step completes
        BaseException       → raised → step fails / propagates
    """

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._i = 0
        self.calls = 0

    def list_available(self):
        return {}

    def resolve(self, role, *a, **k):
        return (None, "m1")

    def bind_model(self, *a, **k):
        return self

    def client_for_model(self, *a, **k):
        return self

    def stream(self, messages):
        self.calls += 1
        if self._i >= len(self._chunks):
            raise RuntimeError("fake model exhausted")
        item = self._chunks[self._i]
        self._i += 1
        if isinstance(item, BaseException):
            raise item
        yield item if isinstance(item, AIMessageChunk) else _chunk(item)


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


def make_exec_plan(plan, task_id, max_steps=6):
    return ExecutionPlan(
        task_id=task_id,
        goal=Goal(text="do the thing", intent=IntentType.CODING),
        plan=plan,
        tools=[],
        model_spec={"model": "m1", "supports_tools": True},
        max_steps=max_steps,
        temperature=0.0,
        context={},
    )


def run_plan(plan, task, chunks, max_steps=6):
    """Drive run_stream with a plan; return (stream_kinds, fake_model, bus_events)."""
    exec_plan = make_exec_plan(plan, task_id=task.id, max_steps=max_steps)
    fake = FakeModel(chunks)
    bus = EventBus()
    events = []
    bus.on_any(lambda ev: events.append(ev))
    runtime = NoviRuntime(model_service=fake, event_bus=bus)
    ctx = ExecutionContext(user_input="do the thing", execution_plan=exec_plan)
    kinds = [item[0] for item in runtime.run_stream(context=ctx)]
    return kinds, ctx, fake, bus, events, plan


# ── Sequential execution + step events ───────────────────────────────────────

def test_executes_plan_steps_sequentially_to_completion():
    plan, task = make_plan("task-1", 3, IntentType.CODING)
    kinds, _, fake, _, events, plan = run_plan(plan, task, ["a", "b", "c"])

    assert fake.calls == 3  # one model call per step
    assert plan.status is PlanStatus.COMPLETED
    assert [s.status for s in plan.steps] == [PlanStepStatus.COMPLETED] * 3

    assert kinds.count("step.started") == 3
    assert kinds.count("step.completed") == 3
    assert "step.failed" not in kinds
    assert "plan.started" in kinds
    assert "plan.completed" in kinds
    assert "plan.failed" not in kinds

    step_ids = [e.data["step_id"] for e in events if e.type == "step.started"]
    assert step_ids == [s.id for s in plan.steps]


def test_step_events_carry_structured_payloads():
    plan, task = make_plan("task-2", 2, IntentType.CODING)
    _, _, _, _, events, _ = run_plan(plan, task, ["one", "two"], max_steps=4)

    started = [e for e in events if e.type == "step.started"]
    completed = [e for e in events if e.type == "step.completed"]
    assert len(started) == 2
    assert [e.data["step_id"] for e in started] == [plan.steps[0].id, plan.steps[1].id]
    assert all(e.data["plan_id"] == plan.id for e in started)
    assert all("description" in e.data for e in started)
    assert [e.data["step_id"] for e in completed] == [plan.steps[0].id, plan.steps[1].id]
    assert all("result" in e.data for e in completed)


# ── Failure propagation ──────────────────────────────────────────────────────

def test_step_failure_fails_plan_and_halts():
    plan, task = make_plan("task-3", 3, IntentType.CODING)
    # model call #2 raises → step 2 fails → plan fails, step 3 never runs
    kinds, _, fake, _, events, plan = run_plan(plan, task, ["ok1", RuntimeError("boom"), "never"], max_steps=6)

    assert plan.status is PlanStatus.FAILED
    assert plan.steps[0].status is PlanStepStatus.COMPLETED
    assert plan.steps[1].status is PlanStepStatus.FAILED
    assert plan.steps[2].status is PlanStepStatus.PENDING  # never attempted

    assert "step.failed" in kinds
    assert "plan.failed" in kinds
    assert "step.completed" in kinds
    assert "plan.completed" not in kinds
    assert fake.calls == 2  # only 2 of 3 steps ran

    # error detail captured on the step.failed bus event
    failed = [e for e in events if e.type == "step.failed"]
    assert failed and "boom" in failed[0].data.get("error", "")


# ── Plan / step lifecycle transitions ────────────────────────────────────────

def test_plan_and_step_lifecycle_transitions():
    plan, task = make_plan("task-4", 1, IntentType.CONVERSATION)
    assert plan.status is PlanStatus.DRAFT
    assert plan.steps[0].status is PlanStepStatus.PENDING

    run_plan(plan, task, ["hi"], max_steps=3)

    assert plan.status is PlanStatus.COMPLETED
    assert plan.steps[0].status is PlanStepStatus.COMPLETED


def test_plan_lifecycle_fails_with_steps():
    plan, task = make_plan("task-5", 1, IntentType.CODING)
    run_plan(plan, task, [RuntimeError("bad")], max_steps=3)
    assert plan.status is PlanStatus.FAILED
    assert plan.steps[0].status is PlanStepStatus.FAILED


# ── Task lifecycle projection (events → Task status) ─────────────────────────

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


def _projection():
    from novi.orchestrator.projection import TaskLifecycleProjection

    store = _FakeTaskStore()
    plan, task = make_plan("task-6", 2, IntentType.CODING)
    task.plan = plan
    store.save(task)
    bus = EventBus()
    TaskLifecycleProjection(store).subscribe(bus)
    return store, task, plan, bus


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

def test_unplanned_single_loop_still_works():
    fake = FakeModel(["plain answer"])
    runtime = NoviRuntime(model_service=fake, event_bus=EventBus())
    ctx = ExecutionContext(user_input="hello")
    kinds = [item[0] for item in runtime.run_stream(context=ctx)]
    assert "token" in kinds
    assert ctx.trace.final_response_length > 0


def test_unplanned_run_returns_text():
    fake = FakeModel(["the final answer"])
    runtime = NoviRuntime(model_service=fake, event_bus=EventBus())
    result = runtime.run("do a thing")
    assert "final answer" in result