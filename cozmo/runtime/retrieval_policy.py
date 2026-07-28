"""RetrievalPolicy — decides WHERE and HOW to retrieve information.

Separates:
  "Should we retrieve?" (GroundingDecision)
from:
  "Where should retrieval happen?" (RetrievalPolicy)

Consumes existing structured signals only:
  - GroundingDecision (needs_grounding, source)
  - EvidenceAnalysis (signals, requirements)
  - Intent

Outputs RetrievalPlan that the runtime executes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class RetrievalSource(str, Enum):
    """Information sources available for retrieval."""
    KNOWLEDGE = "knowledge"
    WEB = "web"


class RetrievalStrategy(str, Enum):
    """Retrieval strategy — source selection and ordering."""
    NONE = "none"
    KNOWLEDGE_ONLY = "knowledge_only"
    WEB_ONLY = "web_only"
    KNOWLEDGE_THEN_WEB = "knowledge_then_web"


@dataclass
class RetrievalPlan:
    """What, where, and how to retrieve before the ReAct loop."""
    sources: list[RetrievalSource] = field(default_factory=list)
    strategy: RetrievalStrategy = RetrievalStrategy.NONE
    reason: str = ""


class RetrievalPolicy:
    """Pure decision logic. No runtime dependencies, no keyword matching.

    Consumes GroundingDecision + EvidenceAnalysis + intent string.
    Outputs RetrievalPlan describing which sources to query and in what order.
    """

    @staticmethod
    def resolve(
        needs_grounding: bool,
        signal_types: list[str],
        signal_strengths: dict[str, str],
        has_external: bool,
        intent: str,
    ) -> RetrievalPlan:
        """Decide retrieval strategy from existing structured signals.

        Args:
            needs_grounding: GroundingDecision.needs_grounding
            signal_types: List of evidence signal types (e.g. ["temporal", "dynamic"])
            signal_strengths: Map of signal type -> strength (e.g. {"temporal": "medium"})
            has_external: EvidenceAnalysis.requirements.external
            intent: Intent type string (e.g. "conversation", "research", "coding")
        """
        # ── Grounding says yes → need current information ────────────
        if needs_grounding:
            # Dynamic signals (game meta, prices, builds) are always time-sensitive
            if "dynamic" in signal_types:
                return RetrievalPlan(
                    sources=[RetrievalSource.WEB],
                    strategy=RetrievalStrategy.WEB_ONLY,
                    reason="Time-sensitive dynamic content requested; direct web retrieval",
                )
            # Research intent is explicitly about current information
            if intent == "research":
                return RetrievalPlan(
                    sources=[RetrievalSource.WEB],
                    strategy=RetrievalStrategy.WEB_ONLY,
                    reason="Explicit research intent; direct web retrieval",
                )
            # High-confidence temporal (today, latest, breaking) → web
            if signal_strengths.get("temporal") == "high":
                return RetrievalPlan(
                    sources=[RetrievalSource.WEB],
                    strategy=RetrievalStrategy.WEB_ONLY,
                    reason="Time-sensitive temporal query; direct web retrieval",
                )
            # External signal present (temporal medium/low, comparative) → KB first, web fallback
            if has_external or "temporal" in signal_types or "comparative" in signal_types:
                return RetrievalPlan(
                    sources=[RetrievalSource.KNOWLEDGE, RetrievalSource.WEB],
                    strategy=RetrievalStrategy.KNOWLEDGE_THEN_WEB,
                    reason="Current information; local knowledge attempted first with web fallback",
                )
            # LLM said yes but no external signal → conservative: KB first, then web
            return RetrievalPlan(
                sources=[RetrievalSource.KNOWLEDGE, RetrievalSource.WEB],
                strategy=RetrievalStrategy.KNOWLEDGE_THEN_WEB,
                reason="Information may be available locally or externally; hierarchical retrieval",
            )

        # ── Grounding says no → stable/timeless knowledge ────────────
        # Coding intent: technical knowledge may exist in KB
        if intent == "coding":
            return RetrievalPlan(
                sources=[RetrievalSource.KNOWLEDGE],
                strategy=RetrievalStrategy.KNOWLEDGE_ONLY,
                reason="Technical knowledge query; checking local knowledge base",
            )

        # No retrieval needed
        return RetrievalPlan(
            sources=[],
            strategy=RetrievalStrategy.NONE,
            reason="No retrieval needed",
        )


def _has_type(signals, type_name: str) -> bool:
    """Check if any signal has the given type."""
    return any(getattr(s, "type", "") == type_name for s in signals)


def _signal_strengths(signals) -> dict[str, str]:
    """Build {type: strength} map from signal list, highest strength wins."""
    result: dict[str, str] = {}
    strength_order = {"high": 3, "medium": 2, "low": 1}
    for s in signals:
        t = getattr(s, "type", "")
        st = getattr(s, "strength", "low")
        if t not in result or strength_order.get(st, 0) > strength_order.get(result[t], 0):
            result[t] = st
    return result
