"""ContextManager — single authoritative gatekeeper between execution state and model.

Agent-wide, usable across conversation/coding/research/workspace/tools/memory/jobs.
Agent-wide primitive, not workspace-specific. First consumer is Workspace,
but same system manages Memory/Project/Web/Attachments/Tool results/Skills
and long-running execution state.

Flow: Orchestrator → ExecutionCoordinator → ExecutionContext → ContextManager → Retrieval/Compression → NoviRuntime → Model
Answers: "Given goal, execution state, model budget, and available sources, what context does model actually need now?"
"""

from __future__ import annotations

from .context_budget import ContextBudgetManager, BudgetBreakdown, estimate_tokens
from .execution_context import ExecutionContext
from .execution_state import StableState

# Re-export for backward compat: `from novi.runtime.context_manager import StableState`
__all__ = ["StableState", "ContextManager"]


class ContextManager:
    """Single gatekeeper. Consumes existing sources, no second memory.

    Agent-wide, usable across conversation/coding/research/workspace/tools/memory/jobs
    """

    def __init__(self, model_name: str | None = None, simple_llm: object | None = None):
        self.model_name = model_name
        self.simple_llm = simple_llm

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
        """L1: keep paths, snippets, errors, counts, summary.

        Threshold 75/85/90 handled by should_compact; L1 itself triggers at
        len > budget_chars (default 4000) and preserves isolation — never mixes
        project contexts.
        """
        if not text or len(text) <= budget_chars:
            return text
        # Keep head + tail with marker, preserve filenames
        head = text[: budget_chars // 2]
        tail = text[-(budget_chars // 2) :]
        # Ensure we keep lines with paths/errors/counts — critical for isolation
        important_lines = [
            l for l in text.splitlines()
            if any(k in l.lower() for k in ["error", "failed", "path:", "file:", ".py", ".ts", ".js", "/", "count:", "total"])
        ]
        keep = "\n".join(important_lines[:5])
        if keep:
            return f"{head}\n…[truncated {len(text)-budget_chars} chars]…\n{keep}\n{tail}"
        return f"{head}\n…[truncated {len(text)-budget_chars} chars]…\n{tail}"

    def compact_history(self, ctx: ExecutionContext) -> None:
        """L2: rolling compaction — preserve hierarchy, not just delete.

        Keeps last 6 turns, summarizes via StableState.to_text() + simple_llm
        if available else extractive (goal+completed+errors). Stores
        stable_state in ctx.metadata and ctx.summary. Truncates history only,
        never discards StableState. Preserves project_id isolation.
        """
        # Canonical base — preserves project_id/conversation_id/plan/errors/budget_breakdown isolation
        stable = StableState.from_context(ctx)
        # Bounded summary for history compaction (from_context already truncates goal[:500], plan[:800])
        summary_text = stable.to_text()
        # Try simple_llm summarization if available (optional, never required)
        llm = getattr(self, "simple_llm", None) or getattr(ctx, "simple_llm", None) if hasattr(ctx, "simple_llm") else getattr(self, "simple_llm", None)
        # also check metadata-injected llm or ctx attribute
        if llm is None and hasattr(self, "simple_llm") and self.simple_llm:
            llm = self.simple_llm
        if llm is not None:
            try:
                maybe = llm.invoke(f"Condense into 4-6 sentences. Keep: goal, completed, errors.\n{summary_text}")
                if maybe and not str(maybe).lower().startswith("error"):
                    summary_text = str(maybe).strip()[:1200]
            except Exception:
                pass
        # Truncate history to last 6, keep summary — never discard StableState
        if len(ctx.history) > 6:
            ctx.summary = (ctx.summary + "\n" + summary_text)[:2000] if ctx.summary else summary_text
            ctx.history = ctx.history[-6:]
        else:
            # Even when history short, ensure summary reflects stable state
            if not ctx.summary:
                ctx.summary = summary_text[:2000]
        # Store stable_state — dict preserves project_id isolation, text for system prompt injection
        ctx.metadata["stable_state"] = stable.to_dict()
        ctx.metadata["stable_state_text"] = summary_text
        ctx.metadata["compacted"] = True

    def checkpoint_stable(self, ctx: ExecutionContext) -> StableState:
        """L3: produce checkpoint stable state, not message dump.

        Caller persists to Checkpoint.stable = stable.to_dict() in job
        lifecycle; bounded messages/tool_states (500/1000) already enforced.
        """
        stable = StableState.from_context(ctx)
        # Ensure project_id isolation preserved in checkpoint
        return stable
