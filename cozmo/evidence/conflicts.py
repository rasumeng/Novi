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
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

_NEGATION = {
    "not", "never", "no", "without", "cannot", "cant", "can't", "doesnt",
    "doesn't", "isnt", "isn't", "wont", "won't", "dont", "don't", "no longer",
}

MAJOR = "MAJOR"
MINOR = "MINOR"

# Year-like values are excluded from numeric disagreement: two sources
# legitimately cite different periods ("2023 revenue" vs "2024 revenue")
# without contradicting each other.
_YEAR_LOW, _YEAR_HIGH = 1900, 2100
# Numeric conflicts demand a tighter subject match than negation conflicts:
# "revenue was $96B" vs "profit was $78B" share boilerplate but describe
# different metrics and must NOT be flagged.
_MIN_NUMERIC_SHARED_TERMS = 3


def _tokens(statement: str) -> set[str]:
    return {w for w in _WORD.findall(statement.lower())}


def _has_negation(tokens: set[str]) -> bool:
    return bool(tokens & _NEGATION)


def _normalize(statement: str) -> str:
    return _PUNCT.sub("", statement.lower()).strip()


def _numbers(statement: str) -> list[float]:
    """All numeric values in a statement, comma-normalized, order kept."""
    out = []
    for m in _NUMBER.findall(statement or ""):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            continue
    return out


def _is_year(value: float) -> bool:
    return value.is_integer() and _YEAR_LOW <= value <= _YEAR_HIGH


class ConflictDetector:
    def __init__(
        self,
        min_shared_terms: int = 2,
        min_overlap_ratio: float = 0.5,
    ):
        self._min_shared_terms = min_shared_terms
        self._min_overlap_ratio = min_overlap_ratio

    def detect(self, facts: tuple[Fact, ...] | list[Fact]) -> tuple[Conflict, ...]:
        """Return contradictions between fact pairs. Empty for no facts.

        Two deterministic contradiction families:
          negation  — near-identical claims with opposite polarity
          numeric   — same-polarity claims about the same subject citing
                      different values for the same period (Phase 8
                      remediation, audit D). Years are ignored so distinct
                      reporting periods never read as contradictions.
        """
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
                severity = MAJOR if len(shared) >= 3 else MINOR
                resolution = self._resolve(a, b)

                if _has_negation(tokens_a) != _has_negation(tokens_b):
                    conflicts.append(
                        Conflict(
                            statements=(a.statement, b.statement),
                            sources=(a.sources + b.sources) or (),
                            severity=severity,
                            resolution=resolution,
                        )
                    )
                    continue

                # Same polarity: only a bounded numeric disagreement can be a
                # contradiction. Conservative gates keep false positives low;
                # the non-numeric skeletons must match EXACTLY, so boilerplate
                # like "revenue was $96B" vs "profit was $78B" (different
                # metrics sharing filler words) is never flagged.
                if len(shared) >= _MIN_NUMERIC_SHARED_TERMS and \
                        self._numeric_disagreement(a.statement, b.statement):
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
    def _numeric_disagreement(stmt_a: str, stmt_b: str) -> bool:
        """Whether two same-subject statements cite conflicting values.

        True only when BOTH hold: the non-numeric token skeletons are exactly
        equal (same claim template — the realistic multi-source case), and
        they cite different non-year numbers for the SAME period (year
        multisets must match or both be absent). Deterministic; no unit
        inference — conservative by construction.
        """
        tokens_a = _tokens(stmt_a)
        tokens_b = _tokens(stmt_b)
        nums_a = _numbers(stmt_a)
        nums_b = _numbers(stmt_b)
        if not nums_a or not nums_b:
            return False
        years_a = sorted(n for n in nums_a if _is_year(n))
        years_b = sorted(n for n in nums_b if _is_year(n))
        if years_a != years_b:
            return False  # different periods, not a contradiction
        vals_a = sorted(n for n in nums_a if not _is_year(n))
        vals_b = sorted(n for n in nums_b if not _is_year(n))
        if not vals_a or not vals_b or vals_a == vals_b:
            return False
        # Strip number tokens from both sides; remaining words must agree
        # exactly (multiset equality).
        def _strip_numbers(tokens: set[str]) -> list[str]:
            return [t for t in sorted(tokens)
                    if not t.replace(",", "").replace(".", "").isdigit()]

        return _strip_numbers(tokens_a) == _strip_numbers(tokens_b)

    @staticmethod
    def _resolve(a: Fact, b: Fact) -> str | None:
        """Resolution: prefer the higher-confidence statement, else None."""
        if a.confidence == b.confidence:
            return None
        winner = a if a.confidence > b.confidence else b
        return f"prefer higher-confidence source: {winner.statement}"
