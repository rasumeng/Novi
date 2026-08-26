"""ContextCompressor — relevance-based context compression.

Selects the highest-relevance passages from raw source text under a character
budget, prefixed with verified facts. Reports the compression ratio so the
40%+ token-reduction target is measurable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .context import Fact

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z0-9]{3,}")

_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "have", "they",
    "you", "your", "not", "are", "was", "will", "would", "should", "could",
    "does", "is", "in", "of", "on", "at", "what", "how", "why", "which",
}


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS}


@dataclass(frozen=True)
class CompressionResult:
    summary: str
    ratio: float
    original_chars: int
    kept_chars: int


class ContextCompressor:
    def __init__(self, budget_chars: int = 4000):
        self._budget = max(200, budget_chars)

    def compress(
        self,
        raw_text: str,
        facts: tuple[Fact, ...] | list[Fact],
        query: str = "",
    ) -> CompressionResult:
        """Compress ``raw_text`` into a verified summary under the budget."""
        original = raw_text or ""
        original_chars = len(original)

        facts = list(facts)
        q_terms = _terms(query)

        # 1. Verified facts first (highest value per character)
        parts: list[str] = []
        for f in facts:
            parts.append(f"- {f.statement}")
        used = sum(len(p) + 1 for p in parts)

        # 2. Fill remaining budget with top-relevance passages
        if used < self._budget and original:
            passages = self._passages(original)
            scored = []
            for p in passages:
                p_terms = _terms(p)
                score = (
                    len(p_terms & q_terms) / len(q_terms)
                    if q_terms
                    else len(p_terms) / max(1, len(p_terms) + len(_STOPWORDS))
                )
                scored.append((score, p))
            scored.sort(key=lambda x: x[0], reverse=True)

            for score, passage in scored:
                if score <= 0:
                    continue
                room = self._budget - used - 3
                if room <= 0:
                    break
                candidate = passage[:room]
                if not candidate.strip():
                    continue
                parts.append(candidate)
                used += len(candidate) + 1

        summary = "\n".join(parts).strip()
        ratio = 1.0 - (len(summary) / original_chars) if original_chars else 0.0
        return CompressionResult(
            summary=summary,
            ratio=round(max(0.0, ratio), 4),
            original_chars=original_chars,
            kept_chars=len(summary),
        )

    @staticmethod
    def _passages(text: str) -> list[str]:
        return [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]
