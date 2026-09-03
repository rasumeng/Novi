"""
EvidenceDetector — objective evidence requirements (no semantic keywords).

Semantic routing removed: vocabulary-based temporal/comparative/etc patterns
deleted. Only objective facts remain: has_images → vision.
All semantic evidence (needs_external/project/memory) is now derived
downstream from the router's workload decision, not from keyword matching.

Kept for backward compatibility and for objective facts.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from ..orchestrator.task_types import EvidenceAnalysis, EvidenceRequirements, EvidenceSignal, GroundingDecision

log = logging.getLogger("novi.evidence")

# Strength weights kept for confidence calc if router-derived signals used
_STRENGTH_WEIGHTS = {
    "high": 0.70,
    "medium": 0.40,
    "low": 0.20,
}

# Pattern tables removed — no semantic keyword matching
_TEMPORAL_PATTERNS: list[tuple[str, str]] = []
_COMPARATIVE_PATTERNS: list[tuple[str, str]] = []
_LOCALITY_PATTERNS: list[tuple[str, str]] = []
_PROJECT_PATTERNS: list[tuple[str, str]] = []
_MEMORY_PATTERNS: list[tuple[str, str]] = []
_DYNAMIC_PATTERNS: list[tuple[str, str]] = []

_GROUNDING_PROMPT = """You are a grounding judge. Determine if answering this question requires current information that the model may not know.

Question: %s

Respond with a JSON object with these fields:
- needs_grounding: boolean — true if answer depends on changeable info (news, releases, prices, events, rankings, patches, meta)
- confidence: float 0.0 to 1.0 — how confident you are in your judgment
- reason: string — one short sentence explaining your reasoning

Output valid JSON only, no markdown wrapping:"""


class EvidenceDetector:
    """Objective evidence detector — no keyword semantics."""

    def __init__(self, llm=None):
        self.llm = llm

    def detect(self, user_input: str, has_images: bool = False) -> EvidenceAnalysis:
        """Return evidence requirements.

        Only has_images is deterministic. All other signals are empty —
        Orchestrator derives external/project needs from router workload.
        """
        if has_images:
            return EvidenceAnalysis(
                requirements=EvidenceRequirements(parametric=False, vision=True),
                confidence=0.95,
                signals=[EvidenceSignal(type="vision", strength="high", detail="user provided image")],
                reasons=["image detected → vision required"],
            )

        # No semantic patterns — return empty analysis.
        # Orchestrator will override requirements from router workload.
        return EvidenceAnalysis(
            requirements=EvidenceRequirements(parametric=True),
            confidence=0.0,
            signals=[],
            reasons=[],
        )

    def detect_from_workload(self, workload: str, has_images: bool = False) -> EvidenceAnalysis:
        """Derive evidence analysis from router workload (deterministic mapping)."""
        if has_images:
            return EvidenceAnalysis(
                requirements=EvidenceRequirements(parametric=False, vision=True),
                confidence=0.95,
                signals=[EvidenceSignal(type="vision", strength="high", detail="image")],
                reasons=["vision workload with image"],
            )
        wl = (workload or "general").lower()
        if wl == "research":
            return EvidenceAnalysis(
                requirements=EvidenceRequirements(parametric=True, external=True),
                confidence=0.85,
                signals=[EvidenceSignal(type="temporal", strength="high", detail="research workload")],
                reasons=["research workload → external required"],
            )
        if wl == "code":
            return EvidenceAnalysis(
                requirements=EvidenceRequirements(parametric=True, project=True),
                confidence=0.8,
                signals=[EvidenceSignal(type="project", strength="high", detail="code workload")],
                reasons=["code workload → project context"],
            )
        return EvidenceAnalysis(
            requirements=EvidenceRequirements(parametric=True),
            confidence=0.0,
            signals=[],
            reasons=[],
        )

    # Backward-compat stubs — no keyword matching
    def _detect_temporal(self, text: str) -> list[EvidenceSignal]:
        return []

    def _detect_comparative(self, text: str) -> list[EvidenceSignal]:
        return []

    def _detect_locality(self, text: str) -> list[EvidenceSignal]:
        return []

    def _detect_project(self, text: str) -> list[EvidenceSignal]:
        return []

    def _detect_memory(self, text: str) -> list[EvidenceSignal]:
        return []

    def _detect_dynamic(self, text: str) -> list[EvidenceSignal]:
        return []

    def _compute_confidence(self, signals: list[EvidenceSignal]) -> float:
        if not signals:
            return 0.0
        by_type: dict[str, EvidenceSignal] = {}
        for s in signals:
            weight = _STRENGTH_WEIGHTS.get(s.strength, 0.05)
            existing = by_type.get(s.type)
            existing_weight = _STRENGTH_WEIGHTS.get(existing.strength, 0.0) if existing else 0.0
            if existing is None or weight > existing_weight:
                by_type[s.type] = s
        total = sum(_STRENGTH_WEIGHTS.get(s.strength, 0.05) for s in by_type.values())
        return round(min(total, 1.0), 2)

    def _signals_to_requirements(self, signals: list[EvidenceSignal]) -> EvidenceRequirements:
        if not signals:
            return EvidenceRequirements(parametric=True)
        external_types = {"temporal", "comparative", "locality", "dynamic"}
        any_external = any(s.type in external_types for s in signals)
        any_project = any(s.type == "project" for s in signals)
        any_memory = any(s.type == "memory" for s in signals)
        return EvidenceRequirements(
            parametric=True,
            external=any_external,
            project=any_project,
            memory=any_memory,
        )

    def _signals_to_reasons(self, signals: list[EvidenceSignal]) -> list[str]:
        seen: set[str] = set()
        reasons: list[str] = []
        for s in signals:
            key = f"{s.type}({s.strength})"
            if key not in seen:
                seen.add(key)
                reasons.append(f"{s.type} ({s.strength}): {s.detail}")
        return reasons

    def grounding_reasoner(self, user_input: str) -> Optional[GroundingDecision]:
        if self.llm is None:
            return None
        try:
            raw = self.llm.invoke(_GROUNDING_PROMPT % user_input).strip()
            cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = json.loads(cleaned)
            needs = bool(data.get("needs_grounding", False))
            conf = float(data.get("confidence", 0.5))
            reason = str(data.get("reason", ""))
            return GroundingDecision(
                needs_grounding=needs,
                confidence=min(max(conf, 0.0), 1.0),
                reason=reason,
                source="llm",
            )
        except Exception as e:
            log.warning("GroundingReasoner failed: %s", e)
            return None
