"""
EvidenceDetector — determines what information sources are needed.

Separates intent (what user wants) from information requirements
(where knowledge comes from). Uses heuristics first, LLM fallback
for ambiguous cases.

Architecture:
  user_input → [heuristic signals] → EvidenceAnalysis
             → [LLM fallback]      → EvidenceAnalysis
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from ..orchestrator.task_types import EvidenceAnalysis, EvidenceRequirements, EvidenceSignal, GroundingDecision

log = logging.getLogger("cozmo.evidence")

_STRENGTH_WEIGHTS = {
    "high": 0.70,
    "medium": 0.40,
    "low": 0.20,
}

_TEMPORAL_PATTERNS: list[tuple[str, str]] = [
    (r"\btoday\b", "high"),
    (r"\blatest\b", "high"),
    (r"\bcurrent\b", "high"),
    (r"\brecent\b", "high"),
    (r"\b(this|next|last)\s+(week|month|year)\b", "high"),
    (r"\bupcoming\b", "high"),
    (r"\bbreaking\b", "high"),
    (r"\bwho\s+won\b", "high"),
    (r"\bscore\b", "high"),
    (r"\bnow\b", "medium"),
    (r"\bnext\b", "medium"),
    (r"\b202[0-9]\b", "medium"),
]

_COMPARATIVE_PATTERNS: list[tuple[str, str]] = [
    (r"\bbest\b", "medium"),
    (r"\btop\b", "medium"),
    (r"\brecommended?\b", "medium"),
    (r"\bworth\s+it\b", "medium"),
    (r"\bmeta\b", "medium"),
    (r"\btier\b", "medium"),
    (r"\bcomparison\b", "medium"),
    (r"\breview\b", "medium"),
    (r"\bbetter\b", "low"),
    (r"\b(prefer|recommend|suggest)\b", "medium"),
    (r"\bvs\b", "low"),
]

_LOCALITY_PATTERNS: list[tuple[str, str]] = [
    (r"\bnear\s+me\b", "high"),
    (r"\bnearby\b", "high"),
    (r"\bclose(?:st)?\b", "medium"),
    (r"\bin\s+my\s+area\b", "high"),
    (r"\blocal\b", "medium"),
]

_PROJECT_PATTERNS: list[tuple[str, str]] = [
    (r"\b\w+\.(py|js|ts|rs|go|java|cpp|c|h|rb|php|swift|kt|scala)\b", "high"),
    (r"\bfix\s+(the\s+)?(bug|issue|problem)\b", "medium"),
    (r"\brefactor\b", "medium"),
    (r"\b(refactor|implement|write|build|update)\s+", "medium"),
    (r"\b(add|create|implement)\b.+\b(feature|functionality|test|endpoint|route)\b", "medium"),
    (r"\b(readme|package\.json|cargo\.toml|pyproject\.toml|tsconfig|compose\.yml)\b", "high"),
    (r"\b(\.\/|\.\.\/)", "medium"),
]

_MEMORY_PATTERNS: list[tuple[str, str]] = [
    (r"\bremember\b", "high"),
    (r"\bprevious(ly)?\b", "high"),
    (r"\blast\s+time\b", "high"),
    (r"\byesterday\b", "high"),
    (r"\bearlier\b", "medium"),
    (r"\bour\s+conversation\b", "high"),
    (r"\bwhat\s+did\s+(we|i|you)\s+(decide|talk|discuss|say|agree)\b", "high"),
    (r"\bas\s+i\s+mentioned\b", "medium"),
    (r"\bas\s+we\s+discussed\b", "medium"),
    (r"\bgoing\s+back\s+to\b", "medium"),
    (r"\b(we|you|i)\s+agreed\b", "medium"),
    (r"\b(we|you|i)\s+decided\b", "medium"),
]

_DYNAMIC_PATTERNS: list[tuple[str, str]] = [
    (r"\b(best|top)\b.+\b(build|loadout|spec|class|comp|meta|setup)\b", "high"),
    (r"\b(best|top)\b.+\b(gpu|cpu|laptop|phone|headphone|monitor|keyboard|mouse)\b", "high"),
    (r"\b(latest|newest)\s+\w+(release|version|update|patch|model)\b", "high"),
    (r"\b(price|cost|how\s+much)\s+(of|is|for|does)\b", "high"),
    (r"\b(tier\s*list|ranking|leaderboard)\b", "high"),
    (r"\b(best|top|worst)\b.+\b(game|app|software|tool|service|framework|library)\b", "medium"),
    (r"\bworth\s+(it|buying|purchasing|getting)\b", "high"),
    (r"\b(loadout|build|spec|class|meta)\s+(for|in|guide)\b", "medium"),
    (r"\b(buy|purchase|upgrade)\s+(an?|the|a\s+new)\s+\w*(gpu|cpu|laptop|phone|gpu|card|model|version)\b", "high"),
    (r"\bshould\s+(i|we)\s+(buy|purchase|upgrade|get)\b", "high"),
    (r"\bshould\s+(i|we)\s+(summon|pull|roll|draw|get)\b", "high"),
    (r"\b(next|upcoming|new)\s+(character|hero|unit|weapon|champion|patch|update|release|season)\b", "high"),
    (r"\b(character|hero|champion|unit)\s+(tier\s*list|ranking|rating|meta)\b", "high"),
]

_GROUNDING_PROMPT = """You are a grounding judge. Determine if answering this question requires current information that the model may not know.

Question: %s

Respond with a JSON object with these fields:
- needs_grounding: boolean — true if answer depends on changeable info (news, releases, prices, events, rankings, patches, meta)
- confidence: float 0.0 to 1.0 — how confident you are in your judgment
- reason: string — one short sentence explaining your reasoning

Examples:
- "Who is the next Wuthering Waves character?" -> {"needs_grounding": true, "confidence": 0.85, "reason": "depends on upcoming game character release"}
- "What is recursion?" -> {"needs_grounding": false, "confidence": 0.95, "reason": "timeless computer science concept"}
- "Best PvE build in Shindo Life?" -> {"needs_grounding": true, "confidence": 0.75, "reason": "builds change with patches and meta shifts"}
- "Explain Python decorators" -> {"needs_grounding": false, "confidence": 0.95, "reason": "stable language feature"}
- "Research Python history" -> {"needs_grounding": true, "confidence": 0.90, "reason": "explicit research request"}

Output valid JSON only, no markdown wrapping:"""


class EvidenceDetector:
    """Detects what evidence sources a task requires.

    Modular by signal type — each _detect_* method returns list[EvidenceSignal].
    Easy to add new signal types (database, docs, API) later.
    """

    def __init__(self, llm=None):
        self.llm = llm

    def detect(self, user_input: str, has_images: bool = False) -> EvidenceAnalysis:
        """Analyze user input and return evidence requirements.

        Pure signal-based detection. No LLM fallback — Orchestrator owns
        the grounding decision via _resolve_grounding().
        """
        if has_images:
            return EvidenceAnalysis(
                requirements=EvidenceRequirements(parametric=False, vision=True),
                confidence=0.95,
                signals=[EvidenceSignal(type="vision", strength="high", detail="user provided image")],
                reasons=["image detected → vision required"],
            )

        text = user_input.lower()
        signals: list[EvidenceSignal] = []

        signals.extend(self._detect_temporal(text))
        signals.extend(self._detect_comparative(text))
        signals.extend(self._detect_locality(text))
        signals.extend(self._detect_project(text))
        signals.extend(self._detect_memory(text))
        signals.extend(self._detect_dynamic(text))

        confidence = self._compute_confidence(signals)
        requirements = self._signals_to_requirements(signals)
        reasons = self._signals_to_reasons(signals)

        log.debug("Evidence signals: %s", [f"{s.type}({s.strength}): {s.detail}" for s in signals])
        log.debug("Evidence requirements: parametric=%s external=%s project=%s memory=%s vision=%s",
                  requirements.parametric, requirements.external,
                  requirements.project, requirements.memory, requirements.vision)
        log.debug("Evidence confidence: %.2f", confidence)

        return EvidenceAnalysis(
            requirements=requirements,
            confidence=confidence,
            signals=signals,
            reasons=reasons,
        )

    def _detect_temporal(self, text: str) -> list[EvidenceSignal]:
        signals = []
        for pattern, strength in _TEMPORAL_PATTERNS:
            m = re.search(pattern, text)
            if m:
                signals.append(EvidenceSignal(type="temporal", strength=strength, detail=f"matched: '{m.group()}'"))
        return signals

    def _detect_comparative(self, text: str) -> list[EvidenceSignal]:
        signals = []
        for pattern, strength in _COMPARATIVE_PATTERNS:
            m = re.search(pattern, text)
            if m:
                signals.append(EvidenceSignal(type="comparative", strength=strength, detail=f"matched: '{m.group()}'"))
        return signals

    def _detect_locality(self, text: str) -> list[EvidenceSignal]:
        signals = []
        for pattern, strength in _LOCALITY_PATTERNS:
            m = re.search(pattern, text)
            if m:
                signals.append(EvidenceSignal(type="locality", strength=strength, detail=f"matched: '{m.group()}'"))
        return signals

    def _detect_project(self, text: str) -> list[EvidenceSignal]:
        signals = []
        for pattern, strength in _PROJECT_PATTERNS:
            m = re.search(pattern, text)
            if m:
                signals.append(EvidenceSignal(type="project", strength=strength, detail=f"matched: '{m.group()}'"))
        return signals

    def _detect_memory(self, text: str) -> list[EvidenceSignal]:
        signals = []
        for pattern, strength in _MEMORY_PATTERNS:
            m = re.search(pattern, text)
            if m:
                signals.append(EvidenceSignal(type="memory", strength=strength, detail=f"matched: '{m.group()}'"))
        return signals

    def _detect_dynamic(self, text: str) -> list[EvidenceSignal]:
        signals = []
        for pattern, strength in _DYNAMIC_PATTERNS:
            m = re.search(pattern, text)
            if m:
                signals.append(EvidenceSignal(type="dynamic", strength=strength, detail=f"matched: '{m.group()}'"))
        return signals

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
        """LLM-based judgment: does this query need external information?

        Returns structured GroundingDecision or None if LLM unavailable/fails.
        Does NOT make the final decision — Orchestrator owns that via _resolve_grounding().
        """
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
