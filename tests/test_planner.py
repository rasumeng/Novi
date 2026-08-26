"""Milestone 5 Phase 2 — Planner and Plan Step tests.

PlannerEngine produces an ordered, sequential Plan for a Task; the Orchestrator
coordinates Task + Plan creation at the pipeline boundary.
"""

import pytest

from novi.orchestrator.task_types import IntentType, Task, Goal, TaskStatus, ExecutionStrategy
from novi.planner import (
    DEFAULT_STEP_TEMPLATES,
    Plan,
    PlanStep,
    PlanStepStatus,
    PlanStatus,
    PlannerEngine,
)


@pytest.fixture
def engine():
    return PlannerEngine()


def make_task(goal_text="Refactor the auth module", intent=IntentType.CODING, task_id="task-1"):
    return Task(
        id=task_id,
        conversation_id="conv-1",
        raw_goal=goal_text,
        goal=Goal(text=goal_text, intent=intent),
        status=TaskStatus.NEW,
    )


# ── PlannerEngine ───────────────────────────────────────────────────────────


def test_create_plan_from_task(engine):
    task = make_task()
    plan = engine.create_plan(task)

    assert isinstance(plan, Plan)
    assert plan.task_id == "task-1"
    assert plan.id
    assert plan.status is PlanStatus.DRAFT
    assert plan.metadata["intent"] == "coding"
    assert plan.created_at


def test_plan_contains_ordered_steps(engine):
    plan = engine.create_plan(make_task(intent=IntentType.RESEARCH))

    steps = plan.ordered_steps
    assert len(steps) == len(DEFAULT_STEP_TEMPLATES["research"]) == 3
    # Sequential: consecutive numbered ids in template order.
    assert [s.id for s in steps] == [
        f"{plan.id}-s1", f"{plan.id}-s2", f"{plan.id}-s3"
    ]
    assert all(s.plan_id == plan.id for s in steps)
    assert all(s.status is PlanStepStatus.PENDING for s in steps)
    # dependencies reserve future parallel work but are unused.
    assert all(s.dependencies == [] for s in steps)
    descriptions = [s.description for s in steps]
    assert descriptions == [
        "Gather relevant information",
        "Synthesize findings",
        "Deliver an answer",
    ]


def test_plan_references_correct_task_id(engine):
    plan = engine.create_plan(make_task(task_id="task-42"))
    assert plan.task_id == "task-42"


def test_steps_carry_goal(engine):
    plan = engine.create_plan(make_task("ship the fix"))
    assert all(s.goal for s in plan.steps)
    assert plan.steps[0].goal == plan.steps[0].description


def test_empty_goal_handled_safely(engine):
    plan = engine.create_plan(make_task(goal_text=""))
    assert plan.task_id == "task-1"
    assert isinstance(plan, Plan)
    # plan is still valid with a fallback step and a flag — no crash.
    assert plan.step_count == 1
    assert plan.metadata.get("empty_goal") is True
    assert "No goal provided" in plan.steps[0].description


def test_whitespace_goal_handled_safely(engine):
    plan = engine.create_plan(make_task(goal_text="   \n  "))
    assert plan.metadata.get("empty_goal") is True
    assert plan.step_count == 1


def test_plan_without_goal_object_uses_raw_goal(engine):
    task = Task(id="task-9", conversation_id="", raw_goal="finish the report", status=TaskStatus.NEW)
    plan = engine.create_plan(task)
    assert plan.step_count >= 1
    assert plan.metadata.get("intent") == "conversation"
    assert "empty_goal" not in plan.metadata


def test_custom_templates_replace_default():
    eng = PlannerEngine(step_templates={"default": ["Do it", "Ship it"]})
    plan = eng.create_plan(make_task(intent=IntentType.CONVERSATION))
    assert [s.description for s in plan.steps] == ["Do it", "Ship it"]


def test_conversation_intent_single_step(engine):
    plan = engine.create_plan(make_task("hello", intent=IntentType.CONVERSATION))
    assert plan.step_count == len(DEFAULT_STEP_TEMPLATES["conversation"]) == 1


# ── Serialization (plan data ensured persistable) ───────────────────────────


def test_plan_to_from_dict_roundtrip(engine):
    plan = engine.create_plan(make_task("deploy the service", intent=IntentType.AUTONOMOUS))
    restored = Plan.from_dict(plan.to_dict())

    assert restored.id == plan.id
    assert restored.task_id == plan.task_id
    assert restored.status is plan.status
    assert restored.metadata == plan.metadata
    assert [s.id for s in restored.steps] == [s.id for s in plan.steps]
    assert restored.steps[0].plan_id == plan.id
    assert restored.steps[0].status is PlanStepStatus.PENDING


def test_plan_step_to_dict():
    step = PlanStep(id="p-s1", plan_id="p", description="d", goal="g")
    d = step.to_dict()
    assert d["id"] == "p-s1"
    assert d["plan_id"] == "p"
    assert d["status"] == "pending"
    restored = PlanStep.from_dict(d)
    assert restored.description == "d"
    assert restored.goal == "g"


# ── Orchestrator boundary ───────────────────────────────────────────────────


@pytest.fixture
def orchestrator(tmp_path):
    from novi.orchestrator.orchestrator import Orchestrator
    from novi.orchestrator.task_store import TaskStore

    return Orchestrator(
        task_store=TaskStore(persist_dir=str(tmp_path / "tasks")),
        planner_engine=PlannerEngine(),
    )


def test_orchestrator_creates_task_and_plan_together(orchestrator):
    plan = orchestrator.plan("Fix the database", conversation_id="conv-7")

    # ExecutionPlan → Task → Plan linkage.
    assert plan.task_id
    stored = orchestrator.task_store.get(plan.task_id)
    assert stored is not None
    assert stored.plan is not None
    assert isinstance(stored.plan, Plan)

    # ExecutionPlan surfaces Task + Plan together, sharing the task id.
    assert plan.plan.task_id == plan.task_id == stored.id
    assert plan.plan.id == stored.plan.id
    assert plan.plan.step_count == stored.plan.step_count


def test_orchestrator_plan_steps_ordered(orchestrator):
    plan = orchestrator.plan("build the feature", conversation_id="conv-3")
    p = plan.plan
    assert len(p.ordered_steps) == len(p.steps) >= 1
    assert [s.id for s in p.ordered_steps] == [f"{p.id}-s{i}" for i in range(1, len(p.steps) + 1)]


def test_orchestrator_persists_plan_with_task(orchestrator):
    plan = orchestrator.plan("scan the notes", conversation_id="conv-8")
    stored = orchestrator.task_store.get(plan.task_id)
    assert stored.plan is not None
    assert stored.plan.task_id == stored.id
    assert [s.description for s in stored.plan.steps]


def test_orchestrator_without_planner_leaves_plan_none():
    from novi.orchestrator.orchestrator import Orchestrator

    bare = Orchestrator()  # no task_store, no planner → forward-compatible no-op
    plan = bare.plan("hello")
    assert plan.task_id == ""
    assert plan.plan is None


def test_reuse_conversation_keeps_same_task_and_plan(orchestrator):
    p1 = orchestrator.plan("fix auth", conversation_id="conv-9")
    p2 = orchestrator.plan("and the login flow", conversation_id="conv-9")
    assert p1.task_id == p2.task_id
    assert p1.plan.id == p2.plan.id


# ── Explicit user-mode override (Deep Research) ──────────────────────────────


def test_analyze_force_intent_bypasses_detection(orchestrator):
    # Even for input that detection would classify as conversation, the
    # explicit force_intent override must win and drive research strategy.
    analysis = orchestrator.analyze("hello", force_intent="research")
    assert analysis.intent == IntentType.RESEARCH
    assert analysis.confidence == 1.0
    assert analysis.strategy is ExecutionStrategy.RESEARCH


def test_plan_force_intent_runs_research_strategy(orchestrator):
    plan = orchestrator.plan(
        "what changed in the AI hardware market in 2026",
        conversation_id="conv-dr-1",
        force_intent="research",
    )
    assert plan.goal.intent == IntentType.RESEARCH
    assert plan.strategy is ExecutionStrategy.RESEARCH
    # The forced research intent is what the runtime maps to the research
    # workload model at execution time (research -> research slot).
    assert plan.goal.intent.value == "research"


def test_plan_without_force_keeps_detected_intent(orchestrator):
    # Control: no override -> intent stays whatever detection resolved, never
    # the forced research mode.
    plan = orchestrator.plan("fix the database", conversation_id="conv-ctrl")
    assert plan.goal.intent is not IntentType.RESEARCH
    assert plan.strategy is not ExecutionStrategy.RESEARCH