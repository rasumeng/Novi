"""Planner domain models — Plan and PlanStep.

A Plan is the ordered set of steps a Task intends to accomplish. It is the
persistent plan reference a Task owns (Task.plan), produced by PlannerEngine
at the Orchestrator boundary. Sequential-only in this milestone; parallel
execution and dependency resolution are future work and are represented here
only as empty/dormant fields (e.g. ``PlanStep.dependencies``).

The models are plain dataclasses with JSON round-trip support so a Plan can
eventually be persisted alongside a Task (TaskStore). They perform no
execution — PlannerEngine and Runtime do that, in their own layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class PlanStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class PlanStepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


def _now() -> str:
    return datetime.now().isoformat()


@dataclass
class PlanStep:
    """One ordered step in a plan.

    Sequential by convention: steps run in ``Plan.steps`` order. 
    ``dependencies`` is reserved for future parallel/conditional execution
    and is intentionally unused (empty) in the first version.
    """

    id: str
    plan_id: str
    description: str
    goal: str = ""
    status: PlanStepStatus = PlanStepStatus.PENDING
    dependencies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "plan_id": self.plan_id,
            "description": self.description,
            "goal": self.goal,
            "status": self.status.value,
            "dependencies": list(self.dependencies),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PlanStep":
        return cls(
            id=data["id"],
            plan_id=data["plan_id"],
            description=data.get("description", ""),
            goal=data.get("goal", ""),
            status=PlanStepStatus(data.get("status", PlanStepStatus.PENDING.value)),
            dependencies=list(data.get("dependencies", [])),
        )


@dataclass
class Plan:
    """An ordered, task-bound sequence of steps."""

    id: str
    task_id: str
    steps: list[PlanStep] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    status: PlanStatus = PlanStatus.DRAFT
    metadata: dict = field(default_factory=dict)

    @property
    def ordered_steps(self) -> list[PlanStep]:
        """Steps in execution order (sequential)."""
        return list(self.steps)

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def add_step(self, step: PlanStep) -> None:
        self.steps.append(step)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
            "status": self.status.value,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Plan":
        return cls(
            id=data["id"],
            task_id=data.get("task_id", ""),
            steps=[PlanStep.from_dict(s) for s in data.get("steps", [])],
            created_at=data.get("created_at", ""),
            status=PlanStatus(data.get("status", PlanStatus.DRAFT.value)),
            metadata=data.get("metadata", {}),
        )