"""
ComplexityEstimator — estimates task complexity from input + intent.

Heuristic-based: message length, tool mentions, code references,
planning indicators. Determines planning level, max steps, model minimum.

Architecture:
  user_input + IntentType → ComplexityEstimator → ComplexityScore
"""

from __future__ import annotations

import re

from ..orchestrator.task_types import ComplexityScore, IntentType

_CODING_INDICATORS = [
    r"fix\s+\w+\.\w+", r"implement\s+\w+", r"refactor", r"debug",
    r"write\s+(a\s+)?(function|class|test|script|module)",
    r"add\s+(feature|functionality)",
    r"\.py\b", r"\.js\b", r"\.ts\b", r"\.rs\b", r"\.go\b",
    r"npm\b", r"pip\b", r"cargo\b", r"git\b",
]

_PLANNING_INDICATORS = [
    r"plan", r"design", r"architecture", r"spec", r"proposal",
    r"roadmap", r"strategy", r"analyze", r"research",
]

_AUTONOMOUS_INDICATORS = [
    r"keep going", r"continue", r"autonomous", r"background",
    r"run\s+(in\s+)?background",
]


class ComplexityEstimator:
    """Estimates task complexity using heuristics."""

    def estimate(self, user_input: str, intent: IntentType) -> ComplexityScore:
        length = len(user_input)
        has_code_block = bool(re.search(r"```", user_input))
        coding_hits = sum(1 for p in _CODING_INDICATORS if re.search(p, user_input, re.IGNORECASE))
        planning_hits = sum(1 for p in _PLANNING_INDICATORS if re.search(p, user_input, re.IGNORECASE))
        autonomous_hits = sum(1 for p in _AUTONOMOUS_INDICATORS if re.search(p, user_input, re.IGNORECASE))

        raw = 1
        raw += min(length // 50, 3)
        raw += coding_hits
        raw += planning_hits * 2
        raw += autonomous_hits

        score = min(raw, 10)

        if intent == IntentType.CODING and coding_hits > 2:
            plan_level = 2 if "refactor" in user_input.lower() or "implement" in user_input.lower() else 1
        elif intent == IntentType.PLANNING or planning_hits > 1:
            plan_level = 2
        elif autonomous_hits:
            plan_level = 2
        else:
            plan_level = 0

        max_steps = min(3 + score, 15)

        estimated_tokens = length * 4 + 500

        if score >= 6 or has_code_block:
            model_minimum = "coding"
        elif planning_hits > 0:
            model_minimum = "planning"
        else:
            model_minimum = "chat"

        return ComplexityScore(
            score=score,
            plan_level=plan_level,
            max_steps=max_steps,
            estimated_tokens=estimated_tokens,
            model_minimum=model_minimum,
        )
