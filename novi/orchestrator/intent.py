"""
IntentDetector — classifies user input into IntentType.

Replaces core/router.py mode routing with structured intent classification.
Determines intent, extracts goal, and provides confidence scores.

Architecture:
  user_input → keyword_pre_pass? → return RESEARCH
            → LLM classification → return IntentType
            → default → CONVERSATION
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from ..orchestrator.task_types import Goal, IntentType

log = logging.getLogger("novi.intent")

_CONTINUATION_KEYWORDS = [
    "continue",
    "keep going",
    "pick up where",
    "what was i working on",
    "previous task",
    "carry on",
    "carry over",
    "go on",
    "resume",
    "next step",
    "left off",
    "continuation of",
]

_RESEARCH_KEYWORDS = [
    "latest news", "current events", "what's new",
    "weather", "price of", "release date", "upcoming", "schedule",
    "this week", "this month", "today", "right now",
    "who won", "score", "election", "breaking",
]

_CODING_KEYWORDS = [
    "read_file", "write_file", "edit_file",
    "implement", "refactor", "debug",
    ".py", ".js", ".ts", ".rs", ".go", ".java", ".cpp",
    "run_command", "execute_python",
    "npm", "pip", "cargo", "git",
    "import", "def", "class", "function",
]

# Multi-word coding patterns — regex with flexible word separation
_CODING_PATTERNS = [
    r"\bfix\s+(the\s+)?(bug|issue|problem)\b",
    r"\badd\s+(.+\s+)?(feature|functionality|test)\b",
    r"\bwrite\s+(.+\s+)?(test|function|class|script)\b",
]

_ROUTE_PROMPT = """Classify the user's latest request as exactly one word:
- conversation: greetings, small talk, definitions, general Q&A answerable from timeless knowledge
- coding: editing files, debugging, shell commands, anything touching the project
- planning: autonomous multi-step tasks like writing specs, planning architecture, documenting, brainstorming, researching, creating reports or proposals
- research: needs current/external info (news, events, sports, prices, weather, releases, schedules, "today", "latest", "recent", "next", "upcoming")
- vision: image generation, image editing, processing .png/.jpeg/.bmp/.gif/.tiff/.webp

Anything about current events or things that change over time is research, even if phrased vaguely.
When unsure between conversation and research, pick research. When it touches code or files, pick coding.
Multi-step tasks that need planning (writing docs, creating specs, architecture design, research reports) → planning.

Examples:
- "what's the weather in new york" → research
- "latest stock price of apple" → research
- "who won the super bowl" → research
- "edit main.py and fix the bug" → coding
- "what is a monad" → conversation
- "help me brainstorm feature ideas" → planning
- "write a spec for the auth system" → planning
- "plan the architecture for a new feature" → planning
- "fix the typo in README.md" → coding
- "explain how DNS works" → conversation

Recent conversation (for context on vague follow-ups):
{history}

Request: {text}
Answer with one word:"""

_INTENT_MAP = {
    "conversation": IntentType.CONVERSATION,
    "coding": IntentType.CODING,
    "research": IntentType.RESEARCH,
    "planning": IntentType.PLANNING,
    "vision": IntentType.VISION,
    "continuation": IntentType.CONTINUATION,
}


def classify_intent(user_input: str,
                    llm=None,
                    history: Optional[list[tuple[str, str]]] = None,
                    has_images: bool = False) -> IntentType:
    """Classify user input into an IntentType.

    Priority:
    1. has_images → VISION
    2. keyword match → RESEARCH
    3. LLM classification
    4. fallback → CONVERSATION
    """
    if has_images:
        return IntentType.VISION

    query_lower = user_input.lower()
    # Continuation is a high-confidence, low-collision signal — check it before
    # generic coding/research keywords (a bare "continue" must not be routed as
    # coding/research).
    for kw in _CONTINUATION_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', query_lower):
            return IntentType.CONTINUATION
    for kw in _RESEARCH_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', query_lower):
            return IntentType.RESEARCH
    for kw in _CODING_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', query_lower):
            return IntentType.CODING
    for pattern in _CODING_PATTERNS:
        if re.search(pattern, query_lower):
            return IntentType.CODING

    if llm is None:
        return IntentType.CONVERSATION

    recent = ""
    if history:
        recent = "\n".join(
            f"User: {u}\nNovi: {a[:200]}" for u, a in history[-3:]
        ) or "(none)"

    try:
        raw = llm.invoke(
            _ROUTE_PROMPT.format(history=recent, text=user_input)
        ).strip().lower()
        for label, intent in _INTENT_MAP.items():
            if label in raw:
                return intent
    except Exception as e:
        log.warning("intent LLM failed: %s", e)

    return IntentType.CONVERSATION


class IntentDetector:
    """Classifies user input into intent + extracts goal."""

    def __init__(self, llm=None):
        self.llm = llm

    def detect(self, user_input: str,
               history: Optional[list[tuple[str, str]]] = None,
               has_images: bool = False) -> tuple[IntentType, float]:
        """Returns (intent, confidence)."""
        intent = classify_intent(user_input, self.llm, history, has_images)
        query_lower = user_input.lower()
        fast_path = (has_images
                     or any(kw in query_lower for kw in _RESEARCH_KEYWORDS)
                     or any(kw in query_lower for kw in _CODING_KEYWORDS)
                     or any(kw in query_lower for kw in _CONTINUATION_KEYWORDS))
        confidence = 0.9 if fast_path else 0.7
        return intent, confidence


class GoalExtractor:
    """Extracts a Goal from user input and conversation context."""

    def extract(self, user_input: str, history: Optional[list] = None) -> Goal:
        return Goal(
            text=user_input[:500],
            intent=IntentType.CONVERSATION,
            extracted_from=user_input[:200],
        )
