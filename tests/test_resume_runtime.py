"""Milestone 5 Phase 5C — Runtime Resume Execution.

Covers resume_from behavior on NoviRuntime.run_stream():
  - resume_from=0 behaves like a normal full run.
  - resume_from=N skips earlier steps without re-executing them.
  - step event indexes stay globally correct across the whole plan.
  - plan lifecycle still emits started/completed (or failed).
  - failure mid-resume behaves correctly.
  - unplanned execution path is untouched by resume_from.
"""

import pytest
from langchain_core.messages import AIMessageChunk

from novi.orchestrator.task_types import ExecutionPlan, Goal, IntentType, Task
from novi.planner import PlannerEngine
from novi.planner.models import PlanStatus, PlanStepStatus
from novi.runtime.event_bus import EventBus
from novi.runtime.execution_context import ExecutionContext
from novi.runtime.runtime import NoviRuntime


def _chunk(text):
    c = AIMessageChunk(content=text)
    # ensure determinism: no reasoning_content in additional_kwargs
    c.additional_kwargs = {}
    return c


class FakeModel:
    """Duck-typed model service returning canned per-call answers."""

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


def _make_plan(task_id="task-1"):
    task = Task(id=task_id, raw_goal="do the thing",
                goal=Goal(text="do the thing", intent=IntentType.CODING))
    plan = PlannerEngine().create_plan(task)
    plan.steps = plan.steps[:3]
    return plan, task


def _make_exec_plan(plan, task_id, max_steps=6):
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


def _run(plan, task, chunks, resume_from, max_steps=6):
    """Drive run_stream; return (kinds, ctx, fake, events, plan)."""
    exec_plan = _make_exec_plan(plan, task_id=task.id, max_steps=max_steps)
    fake = FakeModel(chunks)
    bus = EventBus()
    events = []
    bus.on_any(lambda ev: events.append(ev))
    runtime = NoviRuntime(model_service=fake, event_bus=bus)
    ctx = ExecutionContext(user_input="do the thing", execution_plan=exec_plan)
    kinds = [item[0] for item in runtime.run_stream(context=ctx, resume_from=resume_from)]
    return kinds, ctx, fake, events, plan


# ── resume_from=0 behaves like a normal run ───────────────────────────────

def test_resume_from_zero_is_normal_full_run():
    plan, task = _make_plan()
    kinds, _, fake, events, plan = _run(plan, task, ["a", "b", "c"], resume_from=0)

    assert fake.calls == 3
    assert plan.status is PlanStatus.COMPLETED
    assert [s.status for s in plan.steps] == [PlanStepStatus.COMPLETED] * 3
    assert kinds.count("step.started") == 3
    assert kinds.count("step.completed") == 3
    assert "step.failed" not in kinds
    assert "plan.completed" in kinds

    started = [e.data["index"] for e in events if e.type == "step.started"]
    assert started == [0, 1, 2]


# ── resume_from=N skips earlier steps ─────────────────────────────────────

def test_resume_from_skips_completed_steps_and_executes_remainder():
    plan, task = _make_plan()
    plan.steps[0].status = PlanStepStatus.COMPLETED
    plan.steps[1].status = PlanStepStatus.COMPLETED

    kinds, _, fake, events, plan = _run(
        plan, task, ["c"], resume_from=2, max_steps=30)

    assert fake.calls == 1                     # steps 0 and 1 NOT re-executed
    assert plan.status is PlanStatus.COMPLETED
    assert [s.status for s in plan.steps] == [PlanStepStatus.COMPLETED] * 3

    assert kinds.count("step.started") == 1
    assert kinds.count("step.completed") == 1
    started = [e.data["step_id"] for e in events if e.type == "step.started"]
    assert started == [plan.steps[2].id]


# ── step event indexes remain globally correct ────────────────────────────

def test_resumed_step_indexes_are_globally_correct():
    plan, task = _make_plan()
    plan.steps[0].status = PlanStepStatus.COMPLETED
    plan.steps[1].status = PlanStepStatus.COMPLETED

    _, _, _, events, _ = _run(plan, task, ["c"], resume_from=2)

    started = [e.data["index"] for e in events if e.type == "step.started"]
    completed = [e.data["index"] for e in events if e.type == "step.completed"]
    assert started == [2]
    assert completed == [2]


# ── plan lifecycle on resume ──────────────────────────────────────────────

def test_resumed_plan_emits_started_and_completed():
    plan, task = _make_plan()
    plan.steps[0].status = PlanStepStatus.COMPLETED

    kinds, _, _, events, _ = _run(plan, task, ["b", "c"], resume_from=1)
    assert "plan.started" in kinds
    assert "plan.completed" in kinds
    plan_started = [e for e in events if e.type == "plan.started"]
    assert plan_started and plan_started[0].data["step_count"] == 2
    assert plan.status is PlanStatus.COMPLETED


# ── failure mid-resume behaves correctly ──────────────────────────────────

def test_resume_failure_marks_plan_failed_and_preserves_prior_completion():
    plan, task = _make_plan()
    plan.steps[0].status = PlanStepStatus.COMPLETED

    # step index 1 completes, step index 2 raises → plan fails
    kinds, _, fake, events, _ = _run(
        plan, task, ["ok", RuntimeError("boom")], resume_from=1, max_steps=30)

    assert fake.calls == 2
    assert plan.status is PlanStatus.FAILED
    assert plan.steps[0].status is PlanStepStatus.COMPLETED  # prior work kept
    assert plan.steps[1].status is PlanStepStatus.COMPLETED
    assert plan.steps[2].status is PlanStepStatus.FAILED

    assert "plan.failed" in kinds
    assert "plan.completed" not in kinds
    assert "step.failed" in kinds
    failed = [e.data["index"] for e in events if e.type == "step.failed"]
    assert failed == [2]


# ── unplanned path untouched ──────────────────────────────────────────────

def test_unplanned_run_ignores_resume_from():
    fake = FakeModel(["plain answer"])
    runtime = NoviRuntime(model_service=fake, event_bus=EventBus())
    ctx = ExecutionContext(user_input="hello")
    kinds = [item[0] for item in runtime.run_stream(context=ctx, resume_from=3)]
    assert "token" in kinds
    assert "plan.started" not in kinds