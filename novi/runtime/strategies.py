"""Execution strategies — additive instructions per task type.

All strategies run on the single user-selected primary model. A strategy
selects behavior (instructions, tools), never a model.

Final instructions = base Novi instructions + strategy fragment + context/tools.
"""

from __future__ import annotations

STRATEGIES = ("chat", "code", "research")

CHAT_STRATEGY = """## Chat Strategy

Use Novi's normal conversational behavior. Answer directly, use tools when
they add value, keep responses concise.
"""

CODE_STRATEGY = """## Code Strategy

- Inspect relevant code before modifying it.
- Understand existing patterns before making changes.
- Prefer minimal, consistent modifications.
- Use file/code search when necessary.
- Validate changes using available tests or tools.
- Do not claim a change is complete without verification when verification is possible.
"""

RESEARCH_STRATEGY = """## Research Strategy

- Break complex questions into information requirements.
- Search for reliable sources.
- Compare and validate information across sources.
- Identify uncertainty or conflicting evidence.
- Perform additional research when necessary.
- Synthesize findings clearly.
- Provide citations when web sources are used.
"""

STRATEGY_PROMPTS = {
    "chat": CHAT_STRATEGY,
    "general": CHAT_STRATEGY,
    "code": CODE_STRATEGY,
    "research": RESEARCH_STRATEGY,
}


def get_strategy_prompt(strategy: str) -> str:
    """Return additive prompt fragment for a strategy (chat fallback)."""
    return STRATEGY_PROMPTS.get(strategy, CHAT_STRATEGY)


def normalize_strategy(value: str) -> str:
    """Normalize workload/strategy/intent aliases to chat|code|research.

    The ``general`` → ``chat`` mapping exists for persisted pre-beta router
    state only. Canonical values are chat | code | research.
    """
    v = (value or "").strip().lower()
    mapping = {
        "general": "chat", "conversation": "chat", "chat": "chat",
        "code": "code", "coding": "code",
        "research": "research", "deep": "research", "reasoning": "research",
        "planning": "code", "vision": "chat", "autonomous": "code",
    }
    return mapping.get(v, "chat")
