"""PlannerEngine — deterministic sequential plan generation.

Milestone 5 Phase 2: the first planner is deliberately simple. It accepts a
Task, resolves a step template from the task's intent, and produces an ordered
Plan of PlanStep objects. No LLM, no tool execution, no dependency resolution,
no parallel execution, no replanning.

Boundary: PlannerEngine owns plan *generation* only. It never executes steps,
never touches Jobs, never holds Runtime state. That keeps it inside the
Planner-owns-plan-guardrail: Task owns intent, Planner owns plan structure,
Job/Runtime own execution.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from ..orchestrator.task_types import IntentType, Task
from .models import Plan, PlanStatus, PlanStep


def _ts_suffix() -> str:
    return datetime.now().strftime("%y%m%d%H%M%S")


def _short_uuid() -> str:
    return uuid.uuid4().hex[:8]


# Deterministic intent → ordered step descriptions. Each may use {goal} for
# the task goal text. Keys are IntentType values plus "default".
DEFAULT_STEP_TEMPLATES: dict[str, list[str]] = {
    "conversation": [
        "Respond to the user's message",
    ],
    "research": [
        "Gather relevant information",
        "Synthesize findings",
        "Deliver an answer",
    ],
    "coding": [
        "Understand the request",
        "Implement the change",
        "Verify the result",
    ],
    "planning": [
        "Clarify the objective",
        "Propose the plan",
    ],
    "autonomous": [
        "Assess the situation",
        "Act",
        "Report results",
    ],
    "vision": [
        "Inspect the image",
        "Describe what is seen",
    ],
    "default": [
        "Understand the request",
        "Execute",
        "Verify",
        "Report",
    ],
}


class PlannerEngine:
    """Produces a sequential Plan for a Task. Deterministic and stateless."""

    def __init__(self, step_templates: Optional[dict[str, list[str]]] = None):
        self._templates = step_templates or DEFAULT_STEP_TEMPLATES

    def create_plan(self, task: Task) -> Plan:
        """Build an ordered Plan bound to ``task.id``.

        Empty goals are handled safely: the plan still exists and is flagged in
        metadata with a single fallback step rather than raising.
        """
        plan = Plan(
            id=f"plan-{_ts_suffix()}-{_short_uuid()}",
            task_id=task.id,
            status=PlanStatus.DRAFT,
            metadata={"intent": self._intent_of(task).value},
        )
        goal_text = self._goal_text(task)

        if not goal_text.strip():
            plan.metadata["empty_goal"] = True
            plan.add_step(self._make_step(plan, 1, "No goal provided; awaiting user intent", goal_text))
            return plan

        template = self._templates.get(self._intent_of(task).value, self._templates["default"])
        for i, description in enumerate(template, start=1):
            plan.add_step(
                self._make_step(
                    plan,
                    i,
                    description.format(goal=goal_text) if "{goal}" in description else description,
                    goal_text,
                )
            )
        return plan

    # ── helpers ───────────────────────────────────────────────────────

    def _intent_of(self, task: Task) -> IntentType:
        if task.goal is not None:
            return task.goal.intent
        return IntentType.CONVERSATION

    def _goal_text(self, task: Task) -> str:
        if task.goal is not None and task.goal.text:
            return task.goal.text
        return task.raw_goal or ""

    def _make_step(self, plan: Plan, position: int, description: str, goal_text: str) -> PlanStep:
        return PlanStep(
            id=f"{plan.id}-s{position}",
            plan_id=plan.id,
            description=description,
            goal=description,
        )