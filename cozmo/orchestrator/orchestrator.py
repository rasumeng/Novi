"""
Orchestrator — lightweight coordinator that turns user input into an ExecutionPlan.

Thin by design: classifies intent, estimates complexity, resolves capabilities,
selects model, and produces a plan for the Engine to execute. Does NOT execute
anything itself.

Architecture:
  user_input → Orchestrator → ExecutionPlan → Engine.run(plan)
"""

from __future__ import annotations

import logging
from typing import Optional

from ..orchestrator.task_types import (
    ExecutionPlan, ExecutionStrategy, Goal, IntentType, TaskProfile,
    EvidenceAnalysis, ComplexityScore, TaskAnalysis, GroundingDecision,
)
from ..orchestrator.intent import IntentDetector
from ..orchestrator.complexity import ComplexityEstimator
from ..orchestrator.evidence import EvidenceDetector
from ..runtime.retrieval_policy import RetrievalPolicy
from ..capabilities import CapabilityRegistry

log = logging.getLogger("cozmo.orchestrator")

_CAPABILITY_PRIORITY = [
    "filesystem",
    "terminal",
    "memory",
    "coding",
    "search",
    "research",
    "planning",
    "vision",
    "conversation",
]

SEARCH_CONFIDENCE_THRESHOLD = 0.5

_INTENT_TO_STRATEGY = {
    IntentType.CONVERSATION: ExecutionStrategy.RESPOND,
    IntentType.RESEARCH: ExecutionStrategy.RESEARCH,
    IntentType.CODING: ExecutionStrategy.EXECUTE,
    IntentType.PLANNING: ExecutionStrategy.PLANNED,
    IntentType.VISION: ExecutionStrategy.RESPOND,
    IntentType.AUTONOMOUS: ExecutionStrategy.AUTONOMOUS,
    IntentType.CONTINUATION: ExecutionStrategy.EXECUTE,
}

class Orchestrator:
    """Lightweight coordinator. ~150 lines. Delegates everything."""

    def __init__(
        self,
        intent_detector: Optional[IntentDetector] = None,
        complexity_estimator: Optional[ComplexityEstimator] = None,
        evidence_detector: Optional[EvidenceDetector] = None,
        capability_registry: Optional[CapabilityRegistry] = None,
        model_router=None,
        task_store=None,
        planner_engine=None,
    ):
        self.intent_detector = intent_detector or IntentDetector()
        self.complexity = complexity_estimator or ComplexityEstimator()
        self.evidence_detector = evidence_detector or EvidenceDetector()
        self.capabilities = capability_registry or CapabilityRegistry()
        self.model_router = model_router
        self.task_store = task_store
        self.planner_engine = planner_engine

    def _resolve_capabilities(
        self,
        intent: IntentType,
        evidence: EvidenceAnalysis,
        complexity: ComplexityScore,
    ) -> list[str]:
        """Additive capability resolution from intent + evidence + complexity.

        Each signal contributes independently — intent, evidence, and
        complexity each add capabilities. Result is deduplicated and
        sorted by _CAPABILITY_PRIORITY.
        """
        caps: set[str] = set()

        # ── Evidence-based capabilities ──────────────────────────────

        if evidence.requirements.vision:
            caps.add("vision")
        if evidence.requirements.memory:
            caps.add("memory")
        if evidence.requirements.project:
            caps.add("filesystem")
        if (
            evidence.requirements.external
            and evidence.confidence >= SEARCH_CONFIDENCE_THRESHOLD
        ):
            caps.add("search")
            caps.add("conversation")  # Search always includes conversation

        # ── Intent-based capabilities ─────────────────────────────────

        if intent == IntentType.CONVERSATION:
            caps.add("conversation")
        elif intent == IntentType.RESEARCH:
            caps.add("research")
            caps.add("conversation")
        elif intent == IntentType.CODING:
            caps.update(["coding", "filesystem", "terminal"])
        elif intent == IntentType.PLANNING:
            caps.add("planning")
            caps.add("conversation")
        elif intent == IntentType.VISION:
            caps.add("vision")
            caps.add("conversation")

        # ── Complexity-based capabilities ─────────────────────────────

        if complexity.plan_level > 0:
            caps.add("planning")

        # Always ensure conversation is present unless vision-only
        if not caps:
            caps.add("conversation")

        return sorted(caps, key=_CAPABILITY_PRIORITY.index)

    def _resolve_grounding(
        self,
        intent: IntentType,
        evidence: EvidenceAnalysis,
        user_input: str,
    ) -> GroundingDecision:
        """Single decision point: should we proactively fetch external evidence?

        Three tiers:
        1. Keyword: RESEARCH intent always grounds.
        2. Heuristic: strong evidence signals (conf >= 0.7 AND external) ground.
        3. LLM: medium evidence (0 < conf < 0.7) asks the grounding_reasoner.
        4. None: no signals → skip.
        """
        # Tier 1: Keyword — intent-driven grounding
        if intent == IntentType.RESEARCH:
            return GroundingDecision(
                needs_grounding=True,
                confidence=1.0,
                reason="Intent classified as research",
                source="keyword",
            )

        # Tier 2: Heuristic — strong evidence signals
        if evidence.requirements.external and evidence.confidence >= 0.7:
            reason = "; ".join(evidence.reasons[:2]) if evidence.reasons else "Strong evidence signals"
            return GroundingDecision(
                needs_grounding=True,
                confidence=evidence.confidence,
                reason=reason,
                source="heuristic",
            )

        # Tier 3: LLM — medium confidence, ambiguous signals
        if evidence.confidence > 0 and evidence.confidence < 0.7:
            llm_decision = self.evidence_detector.grounding_reasoner(user_input)
            if llm_decision is not None:
                return llm_decision
            return GroundingDecision(
                needs_grounding=False,
                confidence=evidence.confidence,
                reason="LLM reasoner unavailable; heuristic signals insufficient",
                source="heuristic",
            )

        # Tier 4: No signals
        return GroundingDecision(
            needs_grounding=False,
            confidence=0.0,
            reason="No evidence signals detected",
            source="none",
        )

    def analyze(
        self,
        user_input: str,
        history: Optional[list] = None,
        has_images: bool = False,
    ) -> TaskAnalysis:
        """Analyze user input — returns consolidated TaskAnalysis.

        Single entry point for the analysis pipeline. Bundles intent,
        evidence, complexity, and confidence into one object.
        Future signals (user profile, memory context) added here.
        """
        intent, confidence = self.intent_detector.detect(
            user_input, history, has_images
        )
        evidence = self.evidence_detector.detect(user_input, has_images)
        complexity = self.complexity.estimate(user_input, intent)
        grounding = self._resolve_grounding(intent, evidence, user_input)

        # ── Retrieval policy ─────────────────────────────────────────
        signal_types = [s.type for s in evidence.signals]
        signal_strengths: dict[str, str] = {}
        for s in evidence.signals:
            if s.type not in signal_strengths:
                signal_strengths[s.type] = s.strength
        retrieval_plan = RetrievalPolicy.resolve(
            needs_grounding=grounding.needs_grounding,
            signal_types=signal_types,
            signal_strengths=signal_strengths,
            has_external=evidence.requirements.external,
            intent=intent.value,
            needs_memory=evidence.needs_memory,
            needs_project=evidence.needs_project,
        )

        cap_ids = self._resolve_capabilities(intent, evidence, complexity)
        strategy = _INTENT_TO_STRATEGY.get(intent, ExecutionStrategy.RESPOND)

        result = TaskAnalysis(
            intent=intent,
            evidence=evidence,
            complexity=complexity,
            capabilities=cap_ids,
            strategy=strategy,
            confidence=confidence,
            grounding=grounding,
            retrieval_plan=retrieval_plan,
        )

        log.debug(
            "TaskAnalysis("
            "intent=%s, "
            "evidence={external=%s, project=%s, memory=%s, vision=%s, conf=%.2f}, "
            "complexity={score=%d, plan_level=%d, model=%s}, "
            "capabilities=%s, strategy=%s, "
            "confidence=%.2f"
            ")",
            intent.value,
            evidence.requirements.external,
            evidence.requirements.project,
            evidence.requirements.memory,
            evidence.requirements.vision,
            evidence.confidence,
            complexity.score,
            complexity.plan_level,
            complexity.model_minimum,
            cap_ids,
            strategy.value,
            confidence,
        )

        return result

    def plan(
        self,
        user_input: str,
        history: Optional[list] = None,
        has_images: bool = False,
        force_capability: Optional[str] = None,
        force_model: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> ExecutionPlan:
        """Turn user input into an ExecutionPlan.

        Overrides (force_capability / force_model) bypass detection.
        Uses analyze() for the analysis phase, then builds the plan.

        When a ``task_store`` is wired, the request creates or loads a Task at
        this boundary and the resulting plan references it via ``task_id``.
        Without a task_store, no Task is managed and ``task_id`` stays empty.
        """
        # 1–2. Analyze: intent + evidence + complexity + capabilities → TaskAnalysis
        analysis = self.analyze(user_input, history, has_images)

        # 3. Use capabilities from analysis (single source of truth)
        cap_ids = analysis.capabilities
        if force_capability:
            cap_ids = [force_capability]
        resolved_caps = self.capabilities.resolve(cap_ids)
        tool_names = self.capabilities.get_tool_names(cap_ids)

        # 4. Use strategy from analysis
        strategy = analysis.strategy

        # 5. Build task profile
        profile = TaskProfile(
            intent=analysis.intent,
            capabilities_needed=cap_ids,
            needs_planning=analysis.complexity.plan_level > 0,
            planning_level=analysis.complexity.plan_level,
            model_capability=force_capability or analysis.complexity.model_minimum,
            temperature=0.6 if analysis.intent == IntentType.CONVERSATION else 0.2,
            confidence=analysis.confidence,
        )

        # 6. Build plan
        plan = ExecutionPlan(
            goal=Goal(text=user_input[:500], intent=analysis.intent),
            strategy=strategy,
            capabilities=resolved_caps,
            tools=tool_names,
            model_spec={
                "capability": profile.model_capability,
                "model": force_model or "",
            },
            max_steps=analysis.complexity.max_steps,
            temperature=profile.temperature,
            context={
                "history": history or [],
                "has_images": has_images,
                "analysis": analysis,
                "evidence": analysis.evidence,
            },
        )

        # Resolve model via ModelRouter with complexity awareness
        supports_tools = bool(tool_names) and analysis.intent != IntentType.VISION
        if self.model_router is not None:
            from ..runtime.model_router import ModelRequirement
            req = [ModelRequirement(
                capability=profile.model_capability,
                supports_tools=supports_tools,
                supports_vision=analysis.intent == IntentType.VISION,
            )]
            model_name = self.model_router.resolve(
                requirements=req,
                preferred=force_model,
                complexity_score=analysis.complexity,
            )
            plan.model_spec["model"] = model_name

        plan.model_spec["supports_tools"] = supports_tools

        # 7. Task ownership: create or load a Task for this request. This is
        #    the universal-currency boundary — the plan references the Task.
        if self.task_store is not None:
            task = self.task_store.get_or_create(
                conversation_id=conversation_id or "",
                goal_text=user_input[:500],
                intent=analysis.intent,
            )
            plan.task_id = task.id
            plan.context["task_id"] = task.id

            # 8. Planning: coordinate plan generation, never execute it. The
            #    resulting Plan is attached to the Task (Task owns the plan
            #    reference) and surfaced on the ExecutionPlan. A Task that
            #    already carries a plan is NOT replanned (Phase 2 is
            #    sequential, non-replanning).
            if self.planner_engine is not None:
                if task.plan is None:
                    task.plan = self.planner_engine.create_plan(task)
                    self.task_store.update(task)
                plan.plan = task.plan
                plan.context["plan"] = task.plan

            log.debug(
                "Task bound: %s (conversation=%r, status=%s)",
                task.id, task.conversation_id, task.status.value,
            )

        log.debug(
            "Plan: intent=%s capabilities=%s tools=%d strategy=%s model=%s",
            analysis.intent.value,
            cap_ids,
            len(tool_names),
            strategy.value,
            plan.model_spec.get("model", "(not set)"),
        )

        return plan
