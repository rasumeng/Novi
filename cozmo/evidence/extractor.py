"""FactExtractor — structured fact extraction with confidence per fact.

Small-model friendly: candidate facts are produced deterministically (sentence
splitting), then a classification hook scores each candidate for relevance.
Preferring classification over free-form generation keeps local 3B-8B models
reliable. Without a hook, deterministic query-overlap heuristics provide the
confidence signal. If nothing clears the confidence floor, ``fallback=True``
signals consumers to use raw text instead.
"""

from __future__ import annotations

import re
from typing import Callable

from .context import Fact

log = __import__("logging").getLogger("cozmo.evidence.extractor")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z0-9]{3,}")
_PUNCT = re.compile(r"[^a-z0-9\s]")

# Light stopword list sufficient for query-overlap relevance.
_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "have", "they",
    "you", "your", "not", "are", "was", "will", "would", "should", "could",
    "does", "is", "in", "of", "on", "at", "what", "how", "why", "which",
}


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS}


def _heuristic_confidence(sentence: str, query: str) -> float:
    """Deterministic relevance: fraction of query terms present in sentence.

    Returns a floor of 0.35 when the query has no significant terms, so the
    extractor is never entirely empty for content-bearing text.
    """
    s_terms = _words(sentence)
    q_terms = _words(query)
    if not q_terms:
        return 0.35
    overlap = len(s_terms & q_terms) / len(q_terms)
    return min(1.0, 0.3 + 0.7 * overlap)


def _normalize(statement: str) -> str:
    return _PUNCT.sub("", statement.lower()).strip()


class FactExtractor:
    def __init__(
        self,
        min_confidence: float = 0.5,
        max_facts: int = 20,
        extractor: Callable | None = None,
    ):
        self._min_confidence = min_confidence
        self._max_facts = max_facts
        self._extractor = extractor

    def extract(self, text: str, query: str = "") -> tuple[tuple[Fact, ...], bool]:
        """Extract facts from ``text``. Returns (facts, fallback).

        ``fallback`` is True when no fact cleared the confidence floor — the
        caller should fall back to raw text.
        """
        if not text or not text.strip():
            return (), True

        sentences = [
            s.strip()
            for s in _SENTENCE_SPLIT.split(text.strip())
            if len(s.strip()) >= 25
        ]
        if not sentences:
            return (), True

        confidences = self._classify(sentences, query)
        facts = []
        for i, sentence in enumerate(sentences):
            confidence, category = confidences[i]
            if confidence >= self._min_confidence:
                facts.append(
                    Fact(
                        statement=sentence,
                        confidence=round(float(confidence), 3),
                        category=str(category),
                    )
                )

        facts = self._dedup(facts)
        if not facts:
            return (), True
        return tuple(facts[: self._max_facts]), False

    def merge_facts(self, facts: list[Fact]) -> tuple[Fact, ...]:
        """Merge facts across extraction runs (per-source attribution).

        Near-identical statements are collapsed with unioned source refs.
        """
        if not facts:
            return ()
        return tuple(self._dedup(facts))

    # ── classification ─────────────────────────────────────────────────

    def _classify(self, sentences: list[str], query: str) -> list[tuple[float, str]]:
        """Per-sentence (confidence, category). Hook first, heuristics fallback."""
        if self._extractor is not None:
            try:
                out = self._extractor(sentences, query)
                normalized = self._normalize_classifier(out, len(sentences))
                if normalized is not None:
                    return normalized
            except Exception as e:  # noqa: BLE001 — never break extraction on hook failure
                log.warning("fact extractor hook failed, using heuristics: %s", e)
        return [
            (_heuristic_confidence(s, query), "fact") for s in sentences
        ]

    @staticmethod
    def _normalize_classifier(
        out, expected: int
    ) -> list[tuple[float, str]] | None:
        if not isinstance(out, list) or len(out) != expected:
            return None
        result = []
        for item in out:
            if isinstance(item, (int, float)):
                result.append((float(item), "fact"))
            elif (
                isinstance(item, (tuple, list))
                and len(item) == 2
                and isinstance(item[0], (int, float))
            ):
                result.append((float(item[0]), str(item[1])))
            else:
                return None
        return result

    # ── dedup ───────────────────────────────────────────────────────────

    @staticmethod
    def _dedup(facts: list[Fact]) -> list[Fact]:
        """Merge near-identical statements, union sources, keep max confidence."""
        seen: dict[str, Fact] = {}
        for f in facts:
            key = _normalize(f.statement)
            if not key:
                continue
            existing = seen.get(key)
            if existing is None:
                seen[key] = f
                continue
            # Same claim from another source: merge source refs
            merged = Fact(
                statement=existing.statement,
                confidence=max(existing.confidence, f.confidence),
                sources=tuple(dict.fromkeys(existing.sources + f.sources)),
                category=existing.category or f.category,
            )
            seen[key] = merged
        return list(seen.values())
