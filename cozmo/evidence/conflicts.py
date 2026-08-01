"""ConflictDetector — source disagreement detection and resolution.

Deterministic, conservative by construction: only flags contradictions between
near-identical claims with opposite polarity. Severity tiers (MAJOR/MINOR)
keep false positives low. Resolution prefers the higher-confidence fact.
"""

from __future__ import annotations

import re

from .context import Conflict, Fact

_WORD = re.compile(r"[a-z0-9]{3,}")
_PUNCT = re.compile(r"[^a-z0-9\s]")

_NEGATION = {
    "not", "never", "no", "without", "cannot", "cant", "can't", "doesnt",
    "doesn't", "isnt", "isn't", "wont", "won't", "dont", "don't", "no longer",
}

MAJOR = "MAJOR"
MINOR = "MINOR"


def _tokens(statement: str) -> set[str]:
    return {w for w in _WORD.findall(statement.lower())}


def _has_negation(tokens: set[str]) -> bool:
    return bool(tokens & _NEGATION)


def _normalize(statement: str) -> str:
    return _PUNCT.sub("", statement.lower()).strip()


class ConflictDetector:
    def __init__(
        self,
        min_shared_terms: int = 2,
        min_overlap_ratio: float = 0.5,
    ):
        self._min_shared_terms = min_shared_terms
        self._min_overlap_ratio = min_overlap_ratio

    def detect(self, facts: tuple[Fact, ...] | list[Fact]) -> tuple[Conflict, ...]:
        """Return contradictions between fact pairs. Empty for no facts."""
        facts = list(facts)
        conflicts: list[Conflict] = []
        for i in range(len(facts)):
            for j in range(i + 1, len(facts)):
                a, b = facts[i], facts[j]
                if _normalize(a.statement) == _normalize(b.statement):
                    continue  # duplicate, not a conflict
                tokens_a = _tokens(a.statement)
                tokens_b = _tokens(b.statement)
                shared = tokens_a & tokens_b
                if len(shared) < self._min_shared_terms:
                    continue
                smaller = min(len(tokens_a), len(tokens_b))
                if not smaller or len(shared) / smaller < self._min_overlap_ratio:
                    continue
                if _has_negation(tokens_a) == _has_negation(tokens_b):
                    continue  # same polarity — agreement or unrelated phrasing
                severity = MAJOR if len(shared) >= 3 else MINOR
                resolution = self._resolve(a, b)
                conflicts.append(
                    Conflict(
                        statements=(a.statement, b.statement),
                        sources=(a.sources + b.sources) or (),
                        severity=severity,
                        resolution=resolution,
                    )
                )
        return tuple(conflicts)

    @staticmethod
    def _resolve(a: Fact, b: Fact) -> str | None:
        """Resolution: prefer the higher-confidence statement, else None."""
        if a.confidence == b.confidence:
            return None
        winner = a if a.confidence > b.confidence else b
        return f"prefer higher-confidence source: {winner.statement}"
