"""ContextBudgetManager — model-aware token budgeting.

Single source for: system prompt, stable state, recent conversation,
retrieved context, tool output, output reserve, safety margin.

Never fabricates context_length. Uses ModelRecord.context_length when
available, else conservative fallback (4096 small, 8192 default).
"""

from __future__ import annotations

from dataclasses import dataclass, field


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


CONSERVATIVE_SMALL = 4096
CONSERVATIVE_DEFAULT = 8192
OUTPUT_RESERVE = 1024
SAFETY_MARGIN = 512
SYSTEM_PROMPT_EST = 800
STABLE_STATE_EST = 1200


@dataclass
class BudgetBreakdown:
    context_window: int
    system_prompt: int
    stable_state: int
    recent_conversation: int
    retrieved_context: int
    tool_output: int
    output_reserve: int
    safety_margin: int
    available: int
    utilization_pct: float
    model_name: str = ""
    source: str = ""  # "model_record" | "fallback_small" | "fallback_default"


@dataclass
class ContextBudgetManager:
    """Model-aware budget. Fail safe, never fabricate."""

    @staticmethod
    def get_context_window(model_name: str | None = None) -> tuple[int, str]:
        """Return (window, source) for model, or conservative fallback.

        Resolution order:
          1. Global ModelRegistry (get_global_registry().get)
          2. load_model_record(model_name)
          3. Conservative fallback (4096 small / 8192 default)
        Never fabricates context_length — None stays None.
        """
        if model_name:
            try:
                from ..models.registry import get_global_registry

                reg = get_global_registry()
                rec = reg.get(model_name) if reg else None  # type: ignore
                if rec and getattr(rec, "context_length", None):
                    return int(rec.context_length), "model_record"  # type: ignore
            except Exception:
                pass
            try:
                from ..configuration.model_records import load_model_record

                rec = load_model_record(model_name)  # type: ignore
                if rec and getattr(rec, "context_length", None):
                    return int(rec.context_length), "model_record"  # type: ignore
            except Exception:
                pass
        if model_name and any(s in model_name.lower() for s in ["7b", "3b", "mini", "small"]):
            return CONSERVATIVE_SMALL, "fallback_small"
        return CONSERVATIVE_DEFAULT, "fallback_default"

    @staticmethod
    def compute(
        model_name: str | None = None,
        *,
        system_prompt: str = "",
        stable_state: str = "",
        recent_conversation: str = "",
        retrieved_context: str = "",
        tool_output: str = "",
    ) -> BudgetBreakdown:
        window, source = ContextBudgetManager.get_context_window(model_name)
        sp = estimate_tokens(system_prompt) if system_prompt else SYSTEM_PROMPT_EST
        st = estimate_tokens(stable_state) if stable_state else STABLE_STATE_EST
        rc = estimate_tokens(recent_conversation)
        ret = estimate_tokens(retrieved_context)
        to = estimate_tokens(tool_output)
        used = sp + st + rc + ret + to + OUTPUT_RESERVE + SAFETY_MARGIN
        available = max(0, window - used)
        utilization = (used / window * 100) if window else 100.0
        return BudgetBreakdown(
            context_window=window,
            system_prompt=sp,
            stable_state=st,
            recent_conversation=rc,
            retrieved_context=ret,
            tool_output=to,
            output_reserve=OUTPUT_RESERVE,
            safety_margin=SAFETY_MARGIN,
            available=available,
            utilization_pct=round(utilization, 1),
            model_name=model_name or "",
            source=source,
        )

    @staticmethod
    def should_compact(bd: BudgetBreakdown) -> str | None:
        """Return level or None: 85%+ compact, 90%+ emergency, 75% warning."""
        if bd.utilization_pct >= 90:
            return "emergency"
        if bd.utilization_pct >= 85:
            return "compact"
        if bd.utilization_pct >= 75:
            return "warning"
        return None
