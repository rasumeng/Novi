"""
IntentDetector — thin compatibility layer over HeuristicRouter.

Beta uses WorkloadRouter directly; this module remains importable for
legacy callers but delegates to the heuristic router. No LLM, no keywords,
no has_images workload selection.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..orchestrator.task_types import Goal, IntentType

log = logging.getLogger("novi.intent")

# Kept empty for import compat — not used
_CONTINUATION_KEYWORDS: list[str] = []
_RESEARCH_KEYWORDS: list[str] = []
_CODING_KEYWORDS: list[str] = []
_CODING_PATTERNS: list[str] = []
_ROUTE_PROMPT = ""

_INTENT_MAP = {
    "conversation": IntentType.CONVERSATION,
    "general": IntentType.CONVERSATION,
    "coding": IntentType.CODING,
    "code": IntentType.CODING,
    "research": IntentType.RESEARCH,
    "planning": IntentType.CODING,
    "vision": IntentType.CONVERSATION,
}


def _map_workload_to_intent(workload: str) -> IntentType:
    wl = (workload or "general").lower().strip()
    return _INTENT_MAP.get(wl, IntentType.CONVERSATION)


def classify_intent(user_input: str,
                    llm=None,
                    history: Optional[list[tuple[str, str]]] = None,
                    has_images: bool = False) -> IntentType:
    """Legacy classify via HeuristicRouter. has_images never determines workload."""
    try:
        from .router import WorkloadRouter
        router = WorkloadRouter()
        decision = router.route(
            user_message=user_input,
            state=None,
            history=history,
            has_images=has_images,
        )
        return _map_workload_to_intent(decision.workload)
    except Exception as e:
        log.warning("intent router failed: %s", e)
        return IntentType.CONVERSATION


class IntentDetector:
    """Thin wrapper over HeuristicRouter."""

    def __init__(self, llm=None, router=None):
        self.llm = None
        self._router = router

    def detect(self, user_input: str,
               history: Optional[list[tuple[str, str]]] = None,
               has_images: bool = False) -> tuple[IntentType, float]:
        if self._router is not None:
            try:
                decision = self._router.route(
                    user_message=user_input,
                    state=None,
                    history=history,
                    has_images=has_images,
                )
                return _map_workload_to_intent(decision.workload), float(decision.confidence)
            except Exception as e:
                log.warning("IntentDetector router failed: %s", e)
        intent = classify_intent(user_input, None, history, has_images)
        return intent, 0.75


class GoalExtractor:
    """Extracts a Goal from user input and conversation context."""

    def extract(self, user_input: str, history: Optional[list] = None) -> Goal:
        return Goal(
            text=user_input[:500],
            intent=IntentType.CONVERSATION,
            extracted_from=user_input[:200],
        )
