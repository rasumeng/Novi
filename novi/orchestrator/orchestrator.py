"""
Orchestrator — lightweight coordinator that turns user input into an ExecutionPlan.

Thin by design: classifies intent, estimates complexity, resolves capabilities,
and produces a plan for the Runtime to execute. Does NOT execute anything itself.

Architecture:
  user_input → Orchestrator → ExecutionPlan → Runtime.run_stream(plan)
"""

from __future__ import annotations

import logging
from typing import Optional

from ..orchestrator.task_types import (
    ExecutionPlan, ExecutionStrategy, Goal, IntentType, TaskProfile,
    EvidenceAnalysis, ComplexityScore, TaskAnalysis, GroundingDecision, Relation,
)
from ..orchestrator.complexity import ComplexityEstimator
from ..orchestrator.evidence import EvidenceDetector
from ..orchestrator.router import WorkloadRouter, RouterState
from ..orchestrator.conversation_state import ConversationStateStore
from ..runtime.retrieval_policy import RetrievalPolicy
from ..capabilities import CapabilityRegistry

log = logging.getLogger("novi.orchestrator")

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

# Beta: exactly 3 workloads map to 3 intents. Legacy intents are aliases only.
_INTENT_TO_STRATEGY = {
    IntentType.CONVERSATION: ExecutionStrategy.RESPOND,
    IntentType.RESEARCH: ExecutionStrategy.RESEARCH,
    IntentType.CODING: ExecutionStrategy.EXECUTE,
    IntentType.AUTONOMOUS: ExecutionStrategy.AUTONOMOUS,
}

# Router workload → IntentType mapping (canonical beta)
_WORKLOAD_TO_INTENT = {
    "general": IntentType.CONVERSATION,
    "code": IntentType.CODING,
    "research": IntentType.RESEARCH,
    # legacy aliases — map to beta intents
    "conversation": IntentType.CONVERSATION,
    "coding": IntentType.CODING,
    "vision": IntentType.CONVERSATION,
    "planning": IntentType.CODING,
}

class Orchestrator:
    """Lightweight coordinator. Heuristic router (Beta deterministic)."""

    def __init__(
        self,
        intent_detector: Optional[object] = None,
        complexity_estimator: Optional[ComplexityEstimator] = None,
        evidence_detector: Optional[EvidenceDetector] = None,
        capability_registry: Optional[CapabilityRegistry] = None,
        task_store=None,
        planner_engine=None,
        router: Optional[WorkloadRouter] = None,
        conversation_state_store: Optional[ConversationStateStore] = None,
        config: Optional[dict] = None,
    ):
        # Heuristic router is authoritative; no LLM in Beta path
        self.router = router or WorkloadRouter()
        self.conversation_state_store = conversation_state_store or ConversationStateStore()
        self.complexity = complexity_estimator or ComplexityEstimator()
        self.evidence_detector = evidence_detector or EvidenceDetector()
        self.capabilities = capability_registry or CapabilityRegistry()
        self.task_store = task_store
        self.planner_engine = planner_engine
        self._config = config

    def _resolve_capabilities(
        self,
        intent: IntentType,
        evidence: EvidenceAnalysis,
        complexity: ComplexityScore,
    ) -> list[str]:
        """Additive capability resolution from router workload (intent).

        Evidence here is already workload-derived (no keyword detection).
        """
        caps: set[str] = set()

        # ── Evidence-based (workload-derived) ─────────────────────────
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
            caps.add("conversation")

        # ── Intent (router workload — beta 3 only) ─────────────────────
        if intent == IntentType.CONVERSATION:
            caps.add("conversation")
        elif intent == IntentType.RESEARCH:
            caps.add("research")
            caps.add("conversation")
        elif intent == IntentType.CODING:
            caps.update(["coding", "filesystem", "terminal", "conversation"])
        else:
            caps.add("conversation")

        if complexity.plan_level > 0:
            caps.add("planning")

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
        force_intent: Optional[str] = None,
        conversation_id: Optional[str] = None,
        attachments: Optional[list[dict]] = None,
    ) -> TaskAnalysis:
        """Analyze verbatim user_input via semantic router (dispatcher).

        Invariant: user_input is passed verbatim to workload after routing;
        router output is metadata (workload/relation/state) only.

        ``force_intent`` bypasses router — explicit user-mode override (Deep Research).
        """
        # ── Explicit override (deterministic, objective) ──────────────
        if force_intent:
            intent = IntentType(force_intent)
            confidence = 1.0
            workload = force_intent
            relation = Relation.NEW
            router_state = RouterState(topic="", workload=workload, status="in_progress")
            router_reasoning = "explicit force_intent override"
        else:
            # ── Heuristic router (deterministic, Beta) ─────────────────────
            prior_state = None
            if conversation_id:
                prior_state = self.conversation_state_store.get(conversation_id)
            decision = self.router.route(
                user_message=user_input,  # VERBATIM, never truncated
                state=prior_state,
                history=history,
                has_images=has_images,
                attachments=attachments,
            )
            workload = decision.workload
            intent = _WORKLOAD_TO_INTENT.get(workload, IntentType.CONVERSATION)
            confidence = float(decision.confidence)
            relation = decision.relation if isinstance(decision.relation, Relation) else Relation(decision.relation)
            router_state = decision.state
            router_reasoning = decision.reasoning
            # Persist updated state
            if conversation_id:
                self.conversation_state_store.set(conversation_id, router_state)

        # Evidence derived from router workload (no keyword detection)
        evidence = self.evidence_detector.detect_from_workload(workload, has_images=has_images)
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
            relation=relation,
            router_state=router_state.to_dict() if router_state else None,
            router_workload=workload,
            router_reasoning=router_reasoning,
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
        force_intent: Optional[str] = None,
        conversation_id: Optional[str] = None,
        attachments: Optional[list[dict]] = None,
    ) -> ExecutionPlan:
        """Turn verbatim user_input into an ExecutionPlan (dispatcher).

        Invariant: original user_input is delivered unchanged to the selected
        workload via ExecutionPlan.goal.text. Router decision is metadata only.

        Overrides (force_capability / force_model / force_intent) bypass
        router. ``force_intent`` re-runs analysis as that intent (Deep Research).
        """
        # 1–2. Analyze: router workload → capabilities → TaskAnalysis
        analysis = self.analyze(
            user_input, history, has_images, force_intent=force_intent,
            conversation_id=conversation_id, attachments=attachments)

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

        # 6. Build plan. Verbatim user message is preserved for dispatcher.
        # Runtime resolves workload model at execution time.
        plan = ExecutionPlan(
            goal=Goal(text=user_input, intent=analysis.intent),  # VERBATIM, not truncated to 500
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
                # Dispatcher context: router state is additive, never replacement
                "router_state": analysis.router_state,
                "router_workload": analysis.router_workload,
                "relation": analysis.relation.value,
                "original_message": user_input,  # invariant check
            },
        )

        supports_tools = bool(tool_names)
        plan.model_spec["supports_tools"] = supports_tools

        # 7. Task ownership: create or load a Task for this request.
        # Store truncated goal_text for display, but preserve original via analysis.
        if self.task_store is not None:
            task = self.task_store.get_or_create(
                conversation_id=conversation_id or "",
                goal_text=user_input[:800],
                intent=analysis.intent,
            )
            # Persist router state for continuation semantics
            if analysis.router_state:
                task.metadata["router_state"] = analysis.router_state
                task.metadata["relation"] = analysis.relation.value
                task.metadata["router_workload"] = analysis.router_workload
                self.task_store.update(task)
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
            "Plan: intent=%s capabilities=%s tools=%d strategy=%s",
            analysis.intent.value,
            cap_ids,
            len(tool_names),
            strategy.value,
        )

        return plan
