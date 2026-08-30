"""StableState — canonical durable execution state.

Survives compaction and checkpoint. Produced from ExecutionContext,
serialized to Checkpoint.stable dict, and re-hydrated for resume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .execution_context import ExecutionContext


@dataclass
class StableState:
    """Compact structured state — checkpoint's stable, not message dump."""

    goal: str = ""
    current_objective: str = ""
    plan: str = ""  # serialized plan steps
    completed: list[str] = field(default_factory=list)
    current_step: int = 0
    discoveries: list[str] = field(default_factory=list)
    important_files: list[str] = field(default_factory=list)
    workspace_paths: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    next_action: str = ""
    memory_refs: list[str] = field(default_factory=list)
    budget_breakdown: dict = field(default_factory=dict)
    project_id: str = ""
    conversation_id: str = ""

    def to_text(self, max_chars: int = 1200) -> str:
        parts: list[str] = []
        if self.goal:
            parts.append(f"Goal: {self.goal}")
        if self.current_objective:
            parts.append(f"Current objective: {self.current_objective}")
        if self.plan:
            # keep plan bounded inside text budget
            parts.append(f"Plan: {self.plan[:400]}")
        if self.completed:
            parts.append(f"Completed: {'; '.join(self.completed[:5])}")
        if self.current_step:
            parts.append(f"Step: {self.current_step}")
        if self.discoveries:
            parts.append(f"Discoveries: {'; '.join(self.discoveries[:5])}")
        if self.important_files:
            parts.append(f"Important files: {', '.join(self.important_files[:8])}")
        if self.workspace_paths:
            parts.append(f"Workspace: {', '.join(self.workspace_paths[:5])}")
        if self.decisions:
            parts.append(f"Decisions: {'; '.join(self.decisions[:3])}")
        if self.errors:
            parts.append(f"Errors: {'; '.join(self.errors[:3])}")
        if self.unresolved:
            parts.append(f"Unresolved: {'; '.join(self.unresolved[:3])}")
        if self.next_action:
            parts.append(f"Next: {self.next_action}")
        if self.memory_refs:
            parts.append(f"Memory refs: {', '.join(self.memory_refs[:5])}")
        text = "\n".join(parts)
        return text[:max_chars]

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "current_objective": self.current_objective,
            "plan": self.plan,
            "completed": list(self.completed),
            "current_step": self.current_step,
            "discoveries": list(self.discoveries),
            "important_files": list(self.important_files),
            "workspace_paths": list(self.workspace_paths),
            "decisions": list(self.decisions),
            "errors": list(self.errors),
            "unresolved": list(self.unresolved),
            "next_action": self.next_action,
            "memory_refs": list(self.memory_refs),
            "budget_breakdown": dict(self.budget_breakdown),
            "project_id": self.project_id,
            "conversation_id": self.conversation_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StableState":
        return cls(
            goal=str(d.get("goal", "") or ""),
            current_objective=str(d.get("current_objective", "") or ""),
            plan=str(d.get("plan", "") or ""),
            completed=list(d.get("completed", []) or []),
            current_step=int(d.get("current_step", 0) or 0),
            discoveries=list(d.get("discoveries", []) or []),
            important_files=list(d.get("important_files", []) or []),
            workspace_paths=list(d.get("workspace_paths", []) or []),
            decisions=list(d.get("decisions", []) or []),
            errors=list(d.get("errors", []) or []),
            unresolved=list(d.get("unresolved", []) or []),
            next_action=str(d.get("next_action", "") or ""),
            memory_refs=list(d.get("memory_refs", []) or []),
            budget_breakdown=dict(d.get("budget_breakdown", {}) or {}),
            project_id=str(d.get("project_id", "") or ""),
            conversation_id=str(d.get("conversation_id", "") or ""),
        )

    @classmethod
    def from_context(cls, ctx: "ExecutionContext") -> "StableState":
        """Extract durable state from ExecutionContext.

        Goal from user_input, plan from execution_plan, project_id/
        conversation_id isolated, discoveries from workspace_files_used,
        errors from metadata.
        """
        goal = getattr(ctx, "user_input", "") or ""
        # plan: serialized execution_plan truncated
        raw_plan = getattr(ctx, "execution_plan", None)
        plan_str = str(raw_plan or "")[:800] if raw_plan is not None else ""
        # intent-derived objective
        try:
            current_objective = ctx.intent_str  # type: ignore[attr-defined]
        except Exception:
            current_objective = str(getattr(getattr(ctx, "analysis", None), "intent", "") or "")

        metadata = getattr(ctx, "metadata", {}) or {}
        workspace_files = list(getattr(ctx, "workspace_files_used", []) or [])
        project_id = str(getattr(ctx, "project_id", "") or "")
        conversation_id = str(getattr(ctx, "conversation_id", "") or "")

        return cls(
            goal=goal[:500],
            current_objective=str(current_objective or "")[:500],
            plan=plan_str,
            completed=list(metadata.get("completed", []) or []),
            current_step=int(metadata.get("current_step", 0) or 0),
            discoveries=workspace_files[:8],
            important_files=workspace_files[:8],
            workspace_paths=[project_id] if project_id else [],
            decisions=list(metadata.get("decisions", []) or []),
            errors=list((metadata.get("errors", []) or [])[:3]),
            unresolved=list(metadata.get("unresolved", []) or []),
            next_action=str(metadata.get("next_action", "continue") or "continue"),
            memory_refs=list(metadata.get("memory_refs", []) or []),
            budget_breakdown=dict(metadata.get("budget_breakdown", {}) or {}),
            project_id=project_id,
            conversation_id=conversation_id,
        )
