"""
ComplexityEstimator — workload-aware complexity (no keyword semantics).

Semantic keyword indicators removed. Complexity now derived from:
- message length (budget proxy)
- presence of code fence (objective fact, not keyword)
- intent (from semantic router)
No regex on vocabulary like "fix", "implement", "plan".
"""

from __future__ import annotations

import re

from ..orchestrator.task_types import ComplexityScore, IntentType


class ComplexityEstimator:
    """Estimates task complexity without keyword matching."""

    def estimate(self, user_input: str, intent: IntentType) -> ComplexityScore:
        length = len(user_input)
        has_code_block = bool(re.search(r"```", user_input))

        # Length is the only non-semantic heuristic; intent comes from router
        raw = 1
        raw += min(length // 50, 3)
        # Router-derived intent contributes without keyword counting
        if intent == IntentType.CODING:
            raw += 2
        elif intent == IntentType.PLANNING:
            raw += 2
        elif intent == IntentType.RESEARCH:
            raw += 1

        score = min(raw, 10)

        # Plan level derived from intent + code fence, not keyword hits
        if intent == IntentType.CODING and has_code_block:
            plan_level = 2
        elif intent == IntentType.CODING:
            plan_level = 1 if length > 80 else 0
        elif intent == IntentType.PLANNING:
            plan_level = 2
        elif has_code_block:
            plan_level = 1
        else:
            plan_level = 0

        max_steps = min(3 + score, 15)
        estimated_tokens = length * 4 + 500

        if intent == IntentType.CODING or has_code_block:
            model_minimum = "coding"
        elif intent == IntentType.PLANNING:
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
