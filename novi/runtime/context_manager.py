"""ContextManager — single authoritative gatekeeper between execution state and model.

Agent-wide, usable across conversation/coding/research/workspace/tools/memory/jobs.
Agent-wide primitive, not workspace-specific. First consumer is Workspace,
but same system manages Memory/Project/Web/Attachments/Tool results/Skills
and long-running execution state.

Flow: Orchestrator → ExecutionCoordinator → ExecutionContext → ContextManager → Retrieval/Compression → NoviRuntime → Model
Answers: "Given goal, execution state, model budget, and available sources, what context does model actually need now?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .context_budget import ContextBudgetManager, BudgetBreakdown, estimate_tokens
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

    def to_text(self, max_chars: int = 1200) -> str:
        parts = []
        if self.goal:
            parts.append(f"Goal: {self.goal}")
        if self.current_objective:
            parts.append(f"Current objective: {self.current_objective}")
        if self.completed:
            parts.append(f"Completed: {'; '.join(self.completed[:5])}")
        if self.discoveries:
            parts.append(f"Discoveries: {'; '.join(self.discoveries[:5])}")
        if self.important_files:
            parts.append(f"Important files: {', '.join(self.important_files[:8])}")
        if self.errors:
            parts.append(f"Errors: {'; '.join(self.errors[:3])}")
        if self.unresolved:
            parts.append(f"Unresolved: {'; '.join(self.unresolved[:3])}")
        if self.next_action:
            parts.append(f"Next: {self.next_action}")
        text = "\n".join(parts)
        return text[:max_chars]


class ContextManager:
    """Single gatekeeper. Consumes existing sources, no second memory.

    Agent-wide, usable across conversation/coding/research/workspace/tools/memory/jobs
    """

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name

    def budget_for(self, ctx: ExecutionContext, *, extra_retrieved: str = "", extra_tool: str = "") -> BudgetBreakdown:
        # Gather what would reach model
        stable_text = ""
        try:
            # Prefer StableState if present in metadata
            stable = ctx.metadata.get("stable_state")
            if stable:
                stable_text = str(stable)[:1200]
        except Exception:
            stable_text = ""
        recent = "\n".join(f"{a}:{b[:200]}" for a, b in ctx.history[-6:])
        retrieved = (ctx.memory_context or "") + (ctx.project_context or "") + (ctx.workspace_context or "") + (ctx.grounding_text or "") + extra_retrieved
        tool_out = extra_tool or ""
        # system prompt est is fixed, but we include stable
        return ContextBudgetManager.compute(
            ctx.model_name or self.model_name,
            system_prompt="",
            stable_state=stable_text,
            recent_conversation=recent,
            retrieved_context=retrieved,
            tool_output=tool_out,
        )

    def should_compact(self, ctx: ExecutionContext) -> str | None:
        bd = self.budget_for(ctx)
        # store breakdown for instrumentation
        try:
            ctx.metadata["budget_breakdown"] = bd.__dict__
            if ctx.trace is not None:
                ctx.trace.metadata["budget_breakdown"] = bd.__dict__  # type: ignore
        except Exception:
            pass
        return ContextBudgetManager.should_compact(bd)

    def compress_tool_result(self, text: str, budget_chars: int = 4000) -> str:
        """L1: keep paths, snippets, errors, counts, summary."""
        if not text or len(text) <= budget_chars:
            return text
        # Keep head + tail with marker, preserve filenames
        head = text[: budget_chars // 2]
        tail = text[-(budget_chars // 2) :]
        # Ensure we keep lines with paths/errors
        important_lines = [l for l in text.splitlines() if any(k in l.lower() for k in ["error", "failed", "path:", "file:", ".py", ".ts", "/"])]
        keep = "\n".join(important_lines[:5])
        return f"{head}\n…[truncated {len(text)-budget_chars} chars]…\n{keep}\n{tail}"

    def compact_history(self, ctx: ExecutionContext) -> None:
        """L2: rolling compaction — preserve hierarchy, not just delete."""
        # Build stable from ctx if not present
        stable = StableState(
            goal=ctx.user_input[:200],
            current_objective=getattr(ctx.analysis, "intent", "") and str(ctx.analysis.intent) or ctx.intent_str,
            completed=[s.label for s in getattr(ctx, "inlineSteps", [])[:5]] if hasattr(ctx, "inlineSteps") else [],
            discoveries=getattr(ctx, "workspace_files_used", [])[:5],
            important_files=getattr(ctx, "workspace_files_used", [])[:8],
            next_action="continue",
        )
        # Create summary via simple_llm if available else extractive
        summary_text = stable.to_text()
        # Truncate history to last 6, keep summary
        if len(ctx.history) > 6:
            ctx.summary = (ctx.summary + "\n" + summary_text)[:2000] if ctx.summary else summary_text
            ctx.history = ctx.history[-6:]
        ctx.metadata["stable_state"] = stable.to_text()
        ctx.metadata["compacted"] = True

    def checkpoint_stable(self, ctx: ExecutionContext) -> StableState:
        """L3: produce checkpoint stable state, not message dump."""
        return StableState(
            goal=ctx.user_input[:500],
            current_objective=ctx.intent_str,
            plan=str(getattr(ctx, "execution_plan", "") or "")[:800],
            completed=[s for s in (getattr(ctx, "metadata", {}).get("completed", []) or [])],
            current_step=getattr(ctx, "metadata", {}).get("current_step", 0),
            discoveries=getattr(ctx, "workspace_files_used", [])[:8],
            important_files=getattr(ctx, "workspace_files_used", [])[:8],
            workspace_paths=[getattr(ctx, "project_id", "")] if getattr(ctx, "project_id", "") else [],
            errors=getattr(ctx, "metadata", {}).get("errors", [])[:3],
            next_action=getattr(ctx, "metadata", {}).get("next_action", "continue"),
            memory_refs=[],
            budget_breakdown=getattr(ctx, "metadata", {}).get("budget_breakdown", {}),
            project_id=getattr(ctx, "project_id", "") or "",
        )
