"""ExecutionContext — unified runtime state for one execution run.

Replaces the many loosely-passed parameters in run_stream() with a single
structured object. Every execution decision reads from and writes to this
context, making the full state visible and serializable at any point.

Usage:
    # Build manually
    ctx = ExecutionContext(user_input="fix auth.py")
    ctx.analysis = orchestrator.analyze(...)
    ctx.model_name = "qwen3:8b"

    # Or use the builder
    ctx = ExecutionContext.from_input("fix auth.py")

    # Pass to runtime
    for kind, text in runtime.run_stream(context=ctx):
        ...

Backward compat: run_stream() still accepts old parameters. When old params
are used, an ExecutionContext is built internally. New code should prefer
the context= parameter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from ..orchestrator.task_types import (
    EvidenceAnalysis,
    ExecutionPlan,
    ExecutionStrategy,
    IntentType,
    TaskAnalysis,
    ComplexityScore,
)
from .retrieval_policy import RetrievalPlan
from .retrieval_coordinator import RetrievalBudget, RetrievalCoordinator
from .retrieval import RetrievalRecoveryState
from .trace import ExecutionTrace
from .evidence import RetrievalQuality

if TYPE_CHECKING:
    from ..evidence.context import EvidenceContext


@dataclass
class ExecutionContext:
    """Unified state for a single execution run.

    Fields are grouped by phase. Not all fields are populated at construction
    time — the runtime fills routing/grounding/trace during execution.
    """

    # ── Input ────────────────────────────────────────────────────────────
    user_input: str = ""
    attachments: list[dict] = field(default_factory=list)
    conversation_id: str = ""
    """Brain conversation identity this turn belongs to. Empty when the
    Brain should assign one (single-turn / background paths)."""

    # ── Analysis (from orchestrator) ─────────────────────────────────────
    analysis: Optional[TaskAnalysis] = None

    # ── Plan (from orchestrator or pre-computed) ─────────────────────────
    execution_plan: Optional[ExecutionPlan] = None

    # ── Resume pointer (Milestone 5 Phase 5C / 6A contract) ──────────────
    resume_from: Optional[int] = None
    """0-based Plan.steps index to begin execution at, when resuming a
    previously-interrupted run. ``None`` (default) means start at step 0.

    Phase 6A invariant: ``resume_from`` must equal ``Checkpoint.step`` —
    the completed-step count, passed through UNCHANGED. The caller resolves
    the checkpoint and hands this integer over; the runtime never loads or
    reads checkpoints, Tasks, or Jobs. Steps before ``resume_from`` are
    treated as already completed and are skipped, not re-executed."""

    # ── Session state ────────────────────────────────────────────────────
    history: list[tuple[str, str]] = field(default_factory=list)
    summary: str = ""

    # ── Routing (resolved by runtime or caller) ──────────────────────────
    model_name: str = ""
    role: str = "chat"
    temperature: float = 0.4
    max_steps: int = 10
    model_reason: str = ""  # "role_match" | "config_override" | "execution_plan" | "force_capability"

    # ── Tools ────────────────────────────────────────────────────────────
    allowed_tools: list[str] = field(default_factory=list)
    activated_skills: list[dict] = field(default_factory=list)

    # ── Grounding ────────────────────────────────────────────────────────
    grounding_text: str = ""
    """MIGRATION TARGET: prefer retrieval_plan fields on RetrievalExecutor in new code."""
    grounding_error: str | None = None
    grounding_quality: str = ""
    """Serialized RetrievalQuality value. Empty string means no retrieval was attempted."""
    evidence_context: Optional[EvidenceContext] = None
    """Phase 7 structured evidence (frozen contract). Observational only —
       never set by runtime; populated by an optional downstream consumer.
       Coexists with grounding_text during migration."""

    # ── Retrieval Plan (from RetrievalPolicy) ────────────────────────────
    retrieval_plan: RetrievalPlan = field(default_factory=RetrievalPlan)
    retrieval_escalated: bool = False
    """True when KB returned empty results and runtime escalated to web."""

    # ── Retrieval Budget / Coordinator ───────────────────────────────────
    retrieval_budget: RetrievalBudget = field(default_factory=RetrievalBudget)
    retrieval_coordinator: Optional[RetrievalCoordinator] = None

    # ── Retrieval Recovery (owned by RetrievalExecutor, Phase 9 step 7) ──
    retrieval_recovery: Optional[RetrievalRecoveryState] = None

    # ── Memory ───────────────────────────────────────────────────────────
    memory_context: str = ""

    # ── Project ──────────────────────────────────────────────────────────
    project_context: str = ""

    # ── Planning ─────────────────────────────────────────────────────────
    plan_context: str = ""

    # ── Observability ────────────────────────────────────────────────────
    trace: Optional[ExecutionTrace] = None

    # ── Overrides (debug/convenience) ────────────────────────────────────
    force_model: str = ""
    force_capability: str = ""

    # ── Flags ────────────────────────────────────────────────────────────
    has_images: bool = False
    model_supports_tools: bool = True

    # ── Metadata (extensible) ────────────────────────────────────────────
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Derived helpers ──────────────────────────────────────────────────

    @property
    def intent_str(self) -> str:
        """Shortcut: intent as string for backward-compat code paths."""
        if self.analysis is not None:
            return self.analysis.intent.value
        if self.execution_plan is not None:
            return self.execution_plan.goal.intent.value
        return "conversation"

    @property
    def cap_ids(self) -> list[str]:
        """Resolved capability IDs from analysis or plan."""
        if self.execution_plan is not None and self.execution_plan.capabilities:
            return [c.id for c in self.execution_plan.capabilities]
        if self.analysis is not None:
            return self.analysis.capabilities
        return ["conversation"]

    @property
    def complexity_score(self) -> int:
        if self.analysis is not None:
            return self.analysis.complexity.score
        return 1

    @property
    def plan_level(self) -> int:
        if self.analysis is not None:
            return self.analysis.complexity.plan_level
        return 0

    @property
    def needs_grounding(self) -> bool:
        """Whether this request needs a grounding search before execution."""
        if self.analysis is not None:
            return self.analysis.grounding.needs_grounding
        if self.execution_plan is not None:
            plan_analysis = self.execution_plan.context.get("analysis")
            if plan_analysis and hasattr(plan_analysis, "grounding"):
                return plan_analysis.grounding.needs_grounding
        return self.intent_str == "research"

    @property
    def needs_memory(self) -> bool:
        """Whether memory retrieval should be triggered."""
        if self.analysis is not None:
            return self.analysis.evidence.needs_memory
        return self.intent_str in ("conversation", "planning")

    @property
    def should_plan(self) -> bool:
        """Whether plan generation should be triggered (checked against threshold externally)."""
        return self.plan_level > 0

    # ── Serialization ────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serializable snapshot for debugging / event bus."""
        d: dict[str, Any] = {
            "user_input": self.user_input[:200],
            "intent": self.intent_str,
            "cap_ids": self.cap_ids,
            "model_name": self.model_name,
            "role": self.role,
            "max_steps": self.max_steps,
            "temperature": self.temperature,
            "allowed_tools": self.allowed_tools,
            "has_images": self.has_images,
            "model_supports_tools": self.model_supports_tools,
        }
        if self.analysis is not None:
            d["complexity_score"] = self.analysis.complexity.score
            d["plan_level"] = self.analysis.complexity.plan_level
            d["strategy"] = self.analysis.strategy.value
            d["evidence_signals"] = [s.type for s in self.analysis.evidence.signals]
        if self.execution_plan is not None:
            d["plan_strategy"] = self.execution_plan.strategy.value
        if self.resume_from is not None:
            d["resume_from"] = self.resume_from
        if self.grounding_text:
            d["grounding_length"] = len(self.grounding_text)
        if self.grounding_quality:
            d["grounding_quality"] = self.grounding_quality
        d["retrieval_strategy"] = self.retrieval_plan.strategy.value
        d["retrieval_sources"] = [s.value for s in self.retrieval_plan.sources]
        d["retrieval_escalated"] = self.retrieval_escalated
        if self.memory_context:
            d["memory_context_length"] = len(self.memory_context)
        if self.plan_context:
            d["plan_context_length"] = len(self.plan_context)
        if self.force_model:
            d["force_model"] = self.force_model
        if self.force_capability:
            d["force_capability"] = self.force_capability
        return d

    # ── Builder ──────────────────────────────────────────────────────────

    @classmethod
    def from_input(
        cls,
        user_input: str,
        *,
        attachments: list[dict] | None = None,
        history: list[tuple[str, str]] | None = None,
        summary: str = "",
        force_model: str = "",
        force_capability: str = "",
    ) -> ExecutionContext:
        """Convenience builder: bare context from user input.

        The runtime fills analysis, routing, grounding, and trace during
        run_stream(). This only sets the input-side fields.
        """
        return cls(
            user_input=user_input,
            attachments=attachments or [],
            history=history or [],
            summary=summary,
            force_model=force_model,
            force_capability=force_capability,
        )
