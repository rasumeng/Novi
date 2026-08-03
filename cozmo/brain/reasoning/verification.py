"""Verification — corroboration counting and confirmation detection (pure).

Phase F. The Identity layer is accumulated evidence, not configuration:
a single observation is a ``candidate``; observing the same claim again
``corroborates`` it; an explicit user confirmation ("remember that I...")
``verifies`` it instantly. This module does that reasoning on Brain objects —
no storage imports.
"""

from __future__ import annotations

import re

from ..types import KnowledgeItem

# Explicit-confirmation markers. When a newly acquired claim is phrased as
# one of these, it promotes directly to verified.
_CONFIRM_PATTERNS = re.compile(
    r"(remember( that)? .* i .*|"
    r"i (always|never|hate|love|prefer|like|want|need) .*|"
    r"always (call|use|name|spell|say) .*|"
    r"never (call|use|spell|say) .*|"
    r"make sure to .*|"
    r"from now on .*)",
    re.IGNORECASE,
)

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    {"i", "the", "a", "an", "is", "are", "to", "of", "for", "and", "or",
     "that", "with", "from", "my", "me", "we", "it", "this", "on", "in",
     "be", "have", "has", "do", "does", "at", "as", "by", "so"}
)


def is_confirm(text: str) -> bool:
    """True when the statement is phrased as an explicit user confirmation."""
    return bool(_CONFIRM_PATTERNS.search(text or ""))


def tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall((text or "").lower()) if len(t) > 1 and t not in _STOP}


def corroboration(items: list[KnowledgeItem], index: int) -> int:
    """Number of *other* items that restate the same claim as ``items[index]``.

    Near-duplicate if they share >= 2 content terms and an overlap ratio
    >= 0.5 against the larger token set — the same rule the program-wide
    dedup uses (result_merger._find_duplicate).
    """
    if not items:
        return 0
    base = tokens(items[index].content)
    if not base:
        return 0
    count = 0
    for i, other in enumerate(items):
        if i == index:
            continue
        other_tokens = tokens(other.content)
        if not other_tokens:
            continue
        common = len(base & other_tokens)
        if common < 2:
            continue
        if common / max(len(base), len(other_tokens)) >= 0.5:
            count += 1
    return count