"""Reasoning — knowledge extraction from conversation turns (pure).

Phase C moves the FactExtractor core here and makes it chat-capable. The tier
operates entirely on Brain objects (Turn in, ExtractedClaim/ExtractionResult
out) and is ignorant of storage. All persistence happens in layers/Brain.

Hooks are injected callables so the module stays provider-agnostic:
  - classifier: per-sentence (confidence, tags) for a batch, or None to fall
    back to deterministic heuristics.
  - llm: free-form summarize/title, or None for heuristic fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

from ..types import Turn

# No storage imports here — the architecture guard enforces it.

log = __import__("logging").getLogger("cozmo.brain.reasoning.extraction")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z0-9]{3,}")
_PUNCT = re.compile(r"[^a-z0-9\s]")

# Light stopword list sufficient for deterministic relevance heuristics.
_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "have", "they",
    "you", "your", "not", "are", "was", "will", "would", "should", "could",
    "does", "is", "in", "of", "on", "at", "what", "how", "why", "which",
    "there", "their", "them", "then", "than", "was", "were", "been",
}

_TOOL_LINE_CAP = 400
_SUMMARY_PROMPT = (
    "Condense the following conversation into 2-3 sentences. Capture key facts, "
    "user preferences, and actionable items. Do not include greetings or small talk.\n\n"
    "Conversation:\n{text}\n\nSummary:"
)
_TITLE_PROMPT = "Give a short title (max 8 words) for this conversation:\n{text}\n\nTitle:"

# Tag keywords, mirroring the legacy _classify categories.
_TAG_MARKERS = (
    ("preference", ("prefer", "like", "dislike", "favorite", "love", "hate", "likes")),
    ("project", ("project", "repository", "codebase", "file", "directory", "build")),
    ("learning", ("learn", "understand", "know", "concept", "how to", "learnt")),
    ("reference", ("reference", "document", "guide", "manual", "spec")),
    ("fact", ("fact", "remember", "important", "note", "uses", "version")),
    ("tool", ("command", "script", "error", "failed", "fixed", "installed")),
)


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS}


def _normalize(text: str) -> str:
    return " ".join(_PUNCT.sub("", text.lower()).split())


def _sentences(text: str, min_len: int = 20) -> list[str]:
    return [
        s.strip()
        for s in _SENTENCE_SPLIT.split(text.strip())
        if len(s.strip()) >= min_len
    ]


def _heuristic_confidence(sentence: str) -> float:
    """Deterministic salience: content words + factual markers.

    Returns a floor so content-bearing turns never extract empty.
    """
    if not _words(sentence):
        return 0.0
    length = len(sentence)
    conf = 0.35
    if length >= 60:
        conf += 0.15
    elif length >= 35:
        conf += 0.08
    low = sentence.lower()
    if any(m in low for m in ("prefers", "uses", "learned", "fixed", "installed", "version", "is a")):
        conf += 0.1
    if any(m in low for m in ("user", "i prefer", "i like", "i use", "we use", "the project")):
        conf += 0.1
    return min(0.85, conf)


def _tag_sentence(sentence: str) -> tuple[str, ...]:
    low = sentence.lower()
    tags = []
    for tag, markers in _TAG_MARKERS:
        if any(m in low for m in markers):
            tags.append(tag)
    return tuple(tags)


def _dedup(claims: list["ExtractedClaim"]) -> list["ExtractedClaim"]:
    """Drop exact/near duplicates *within one batch*.

    Batch-scoped only: cross-batch consolidation (a claim restating an existing
    corpus item corroborates it instead of inserting a sibling) lives in the
    KnowledgeLayer, not here.
    """
    seen: dict[str, ExtractedClaim] = {}
    for claim in claims:
        key = _normalize(claim.statement)
        if not key:
            continue
        existing = seen.get(key)
        if existing is None:
            seen[key] = claim
        elif claim.confidence > existing.confidence:
            seen[key] = claim
    return list(seen.values())


@dataclass(frozen=True)
class ExtractedClaim:
    """One candidate atomic knowledge item extracted from a turn batch."""

    statement: str
    confidence: float
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractionResult:
    """Pure output of extraction; persistence is the Brain's job."""

    claims: tuple[ExtractedClaim, ...] = ()
    summary: str = ""
    name: str = ""
    fallback: bool = True


class KnowledgeExtractor:
    """Turn batch → candidate atomic claims + scenario summary + name.

    Args:
        classifier: Optional per-sentence classification hook:
            ``Callable[[list[str]], list[tuple[float, tuple[str, ...]] | None] | None]``.
            Returning None (or a malformed list) falls back to heuristics.
        summarizer: Optional Summarizer for summary/name; None uses heuristics.
        min_confidence: Confidence floor for a claim to be retained.
        max_claims: Upper bound on claims per batch.
    """

    def __init__(
        self,
        classifier: Optional[Callable[[list[str]], Optional[list]]] = None,
        summarizer: Optional["Summarizer"] = None,
        min_confidence: float = 0.4,
        max_claims: int = 20,
    ):
        self._classifier = classifier
        self._summarizer = summarizer
        self._min_confidence = min_confidence
        self._max_claims = max_claims

    def extract(self, turns: tuple[Turn, ...]) -> ExtractionResult:
        if not turns:
            return ExtractionResult()
        text = self._turns_to_text(turns)
        sentences = _sentences(text)
        if not sentences:
            summary = self._summarize(turns, text)
            name = self._title(turns, text)
            return ExtractionResult(summary=summary, name=name, fallback=True)

        confidences = self._classify(sentences)
        claims: list[ExtractedClaim] = []
        for sentence, (conf, tags) in zip(sentences, confidences):
            if conf < self._min_confidence:
                continue
            claims.append(
                ExtractedClaim(
                    statement=sentence,
                    confidence=round(float(conf), 3),
                    tags=tuple(tags) or _tag_sentence(sentence),
                )
            )
        claims = _dedup(claims)
        fallback = not claims
        return ExtractionResult(
            claims=tuple(claims[: self._max_claims]),
            summary=self._summarize(turns, text),
            name=self._title(turns, text),
            fallback=fallback,
        )

    # ── internals ──────────────────────────────────────────────────────

    def _turns_to_text(self, turns: tuple[Turn, ...]) -> str:
        parts = []
        for turn in turns:
            if turn.user:
                parts.append(f"User: {turn.user}")
            if turn.assistant:
                parts.append(f"Cozmo: {turn.assistant}")
            for out in turn.tool_outputs:
                clipped = out if len(out) <= _TOOL_LINE_CAP else out[:_TOOL_LINE_CAP] + "…"
                parts.append(f"Tool: {clipped}")
        return "\n".join(parts)

    def _classify(self, sentences: list[str]) -> list[tuple[float, tuple[str, ...]]]:
        if self._classifier is not None:
            try:
                out = self._classifier(sentences)
                normalized = self._normalize_classifier(out, len(sentences))
                if normalized is not None:
                    return normalized
            except Exception as e:  # noqa: BLE001 — never break extraction on hook failure
                log.warning("classifier hook failed, using heuristics: %s", e)
        return [(_heuristic_confidence(s), _tag_sentence(s)) for s in sentences]

    @staticmethod
    def _normalize_classifier(out, expected: int):
        if not isinstance(out, list) or len(out) != expected:
            return None
        result = []
        for item in out:
            if isinstance(item, (int, float)):
                result.append((float(item), ()))
            elif (
                isinstance(item, (tuple, list))
                and len(item) == 2
                and isinstance(item[0], (int, float))
            ):
                tags = item[1]
                if isinstance(tags, str):
                    tags = (tags,)
                if not isinstance(tags, (tuple, list)):
                    return None
                result.append((float(item[0]), tuple(str(t) for t in tags)))
            else:
                return None
        return result

    def _summarize(self, turns: tuple[Turn, ...], text: str) -> str:
        if self._summarizer is not None:
            try:
                summary = self._summarizer.summarize(turns)
                if summary:
                    return summary.strip()
            except Exception as e:  # noqa: BLE001
                log.warning("summarizer failed, using heuristics: %s", e)
        return self._heuristic_summary(text)

    def _title(self, turns: tuple[Turn, ...], text: str) -> str:
        if self._summarizer is not None:
            try:
                title = self._summarizer.title(turns)
                if title:
                    return title.strip()[:80]
            except Exception as e:  # noqa: BLE001
                log.warning("summarizer title failed, using heuristics: %s", e)
        sentences = _sentences(text, min_len=1)
        if not sentences:
            return ""
        return sentences[0][:60].rstrip(".")

    @staticmethod
    def _heuristic_summary(text: str) -> str:
        sentences = [s for s in _sentences(text, min_len=20) if _words(s)]
        sentences.sort(key=lambda s: (len(s), sum(len(w) for w in _words(s))), reverse=True)
        top = sentences[:3]
        if not top:
            return text.strip()[:200]
        return " ".join(top)


class LayerClassifier:
    """Tags text with knowledge layer + soft tags (LLM-assisted, heuristic fallback)."""

    def __init__(self, hook: Optional[Callable[[str], tuple[str, ...] | None]] = None):
        self._hook = hook

    def classify(self, text: str) -> tuple[tuple[str, ...], str]:
        """Returns (tags, target_layer)."""
        tags = ()
        if self._hook is not None:
            try:
                out = self._hook(text)
                if isinstance(out, (tuple, list)) and out:
                    tags = tuple(str(t) for t in out)
            except Exception as e:  # noqa: BLE001
                log.warning("layer classifier hook failed, using heuristics: %s", e)
        if not tags:
            tags = _tag_sentence(text)
        if "preference" in tags or "goal" in tags:
            layer = "identity"
        elif "project" in tags or "scenario" in tags:
            layer = "scenario"
        else:
            layer = "knowledge"
        return tags, layer


class Summarizer:
    """LLM-backed summarizer with deterministic fallback (no LLM → heuristics)."""

    def __init__(self, llm: Optional[Callable[[str], str]] = None):
        self._llm = llm

    def summarize(self, turns: tuple[Turn, ...]) -> str:
        if self._llm is None:
            return ""
        text = _turns_plain(turns)
        try:
            out = self._llm(_SUMMARY_PROMPT.format(text=text))
            if out and not out.lower().startswith("error"):
                return out.strip()
        except Exception as e:  # noqa: BLE001
            log.warning("summarize failed: %s", e)
        return ""

    def title(self, turns: tuple[Turn, ...]) -> str:
        if self._llm is None:
            return ""
        text = _turns_plain(turns)
        try:
            out = self._llm(_TITLE_PROMPT.format(text=text))
            if out and not out.lower().startswith("error"):
                return out.strip()
        except Exception as e:  # noqa: BLE001
            log.warning("title failed: %s", e)
        return ""


def _turns_plain(turns: tuple[Turn, ...]) -> str:
    parts = []
    for turn in turns:
        if turn.user:
            parts.append(f"User: {turn.user}")
        if turn.assistant:
            parts.append(f"Cozmo: {turn.assistant}")
    return "\n".join(parts)
