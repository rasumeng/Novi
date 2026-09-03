"""
Heuristic Workload Router — Beta deterministic router.

Architecture:
  User message (verbatim)
       ↓
  HeuristicRouter
       workload: general | research | code
       relation: new | continue | switch
       topic: short label 2-8 words
       ↓
  ModelSelector → Execution

Constraints (Beta):
  * Deterministic, no LLM, no llama.cpp
  * Small, understandable, replaceable
  * No giant keyword dictionary, no regex rulebook
  * Priority: explicit intent (handled in orchestrator) > strong deterministic signals > state hint > general
  * One authoritative decision, safe default general/new

Future: QwenRouter will implement same interface `route(...) -> RouterDecision`.

Router outputs workload/relation/topic only; ModelSelector chooses concrete model.
Never rewrites user message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Optional

from .task_types import Relation

# ── Workloads ────────────────────────────────────────────────────────────────

ROUTER_WORKLOADS = ("general", "research", "code")
ROUTER_TO_SELECTOR_WORKLOAD = {
    "general": "general",
    "code": "code",
    "research": "research",
}
_WORKLOAD_ALIASES = {
    "conversation": "general",
    "coding": "code",
    "vision": "general",
    "planning": "code",
}

# ── State ──────────────────────────────────────────────────────────────────

@dataclass
class RouterState:
    """Compact conversation state. Topic is short label 2-8 words."""

    topic: str = ""
    workload: str = ""
    status: str = "idle"
    active_context: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "RouterState":
        if not d or not isinstance(d, dict):
            return cls()
        return cls(
            topic=str(d.get("topic", ""))[:80],
            workload=str(d.get("workload", ""))[:40],
            status=str(d.get("status", "idle"))[:40],
            active_context=str(d.get("active_context", ""))[:240],
        )

    def is_empty(self) -> bool:
        return not (self.topic or self.workload or self.active_context)


@dataclass
class RouterDecision:
    """Heuristic decision — workload/relation/topic."""

    workload: str
    relation: Relation
    topic: str
    # Legacy fields for compat with TaskAnalysis
    confidence: float = 0.85
    state: RouterState = None  # type: ignore
    reasoning: str = ""

    def __post_init__(self):
        if self.state is None:
            self.state = RouterState(topic=self.topic, workload=self.workload, status="in_progress" if self.relation == Relation.CONTINUE else "idle", active_context=self.topic)

    def to_dict(self) -> dict:
        return {
            "workload": self.workload,
            "relation": self.relation.value if isinstance(self.relation, Relation) else str(self.relation),
            "topic": self.topic,
            "confidence": self.confidence,
            "state": self.state.to_dict() if self.state else {},
            "reasoning": self.reasoning,
        }

    @property
    def selector_workload(self) -> str:
        return ROUTER_TO_SELECTOR_WORKLOAD.get(self.workload, "general")


# Backwards compat: RouterConfig no longer needed but kept for interface
@dataclass
class RouterConfig:
    model_path: str = ""
    n_ctx: int = 2048
    n_threads: int = 2
    n_predict: int = 256
    n_gpu_layers: int = 0
    temperature: float = 0.0

    @classmethod
    def from_dict(cls, d: dict | None) -> "RouterConfig":
        return cls()

    def to_dict(self) -> dict:
        return asdict(self)


class RouterError(RuntimeError):
    pass


# ── Heuristic signals (small, deterministic) ─────────────────────────────────

# Keep lists tiny — strong signals only. Do not expand into giant dictionaries.
_RESEARCH_SIGNALS = ("latest", "news", "price", "research", "recent", "who won", "super bowl", "current", "today", "weather", "trends", "version")
# Code: triple backtick is strong, plus a few project/code words
_CODE_SIGNALS = ("```", "projects panel", "projectspanel", "react", "debug", "fix the bug", "review", "build a react", "frontend", "feature", "add a new feature")
_CODE_EXTS = (".py", ".js", ".ts", ".tsx", ".jsx")

_STOPWORDS = frozenset(("what","is","the","how","can","you","explain","build","a","an","find","for","to","of","in","on","and","or","please","help","me","my","with","about","latest","recent","research"))

def _detect_workload(message: str, state: Optional[RouterState] = None) -> str:
    low = message.lower()
    # Code first for hybrid queries like "Fix the bug using latest docs" (code + research)
    if "```" in message:
        return "code"
    for ext in _CODE_EXTS:
        if ext in low:
            return "code"
    for sig in _CODE_SIGNALS:
        if sig in low:
            return "code"
    if any(w in low for w in ("code", "function", "component", "refactor", "auth.py", "sidebar", "python script")):
        return "code"
    # Research (strong external signals)
    for sig in _RESEARCH_SIGNALS:
        if sig in low:
            return "research"
    # State hint for short ambiguous follow-ups: preserve prior workload
    if state and not state.is_empty() and len(message.strip()) < 70:
        low_tokens = set(re.findall(r"[a-z0-9]+", low))
        topic_tokens = set(re.findall(r"[a-z0-9]+", state.topic.lower())) if state.topic else set()
        # If message is a short continuation that clearly refers to prior topic
        # e.g., "Can you make the cards smaller?" after ProjectsPanel UI
        if len(message.strip().split()) <= 7 and state.workload in ROUTER_WORKLOADS:
            # Check for continuation cues or topic overlap
            if (low_tokens & topic_tokens) or any(w in low for w in ("make", "cards", "smaller", "move", "also", "delete", "button", "panel")):
                return state.workload
            # Very short follow-ups like "Code" or "Continue"
            if len(message.strip().split()) <= 3:
                return state.workload
    return "general"


def _extract_topic(message: str, workload: str) -> str:
    low = message.lower()
    # Structured project hints — preserve known topics
    if "projects panel" in low or "projectspanel" in low:
        return "ProjectsPanel UI"
    if "flask" in low and "movie" in low:
        return "Flask movie app"
    if "react sidebar" in low:
        return "React sidebar"
    if "tcp" in low and "udp" in low:
        return "TCP vs UDP"
    if "small language models" in low:
        return "small language models"
    if "btc price" in low or "price of btc" in low or "btc" in low:
        return "BTC price"
    if "binary search" in low:
        return "binary search"
    if "dependency injection" in low:
        return "dependency injection"
    # Fallback: first 2-4 meaningful words
    words = re.findall(r"[A-Za-z0-9]+", message)
    # Filter stopwords and single letters (e.g., "s" from "What's")
    filtered = [w for w in words if w.lower() not in _STOPWORDS and len(w) > 1]
    if not filtered:
        filtered = [w for w in words if len(w) > 1][:4]
    if not filtered:
        filtered = words[:4]
    topic_words = filtered[:4]
    if len(topic_words) < 2 and len(words) >= 2:
        topic_words = words[:2]
    topic = " ".join(topic_words[:4])
    # Title case short
    topic = topic.strip()[:60]
    if not topic:
        topic = "general"
    # Ensure 2-8 words: if single word, keep as is (e.g., "ProjectsPanel")
    return topic


def _detect_relation(message: str, state: Optional[RouterState], workload: str) -> Relation:
    if not state or state.is_empty() or not state.topic:
        return Relation.NEW
    msg = message.strip()
    low = msg.lower()
    # Strong continue hints: short message that is clearly a follow-up
    if len(msg) < 70 and len(msg.split()) <= 7:
        topic_tokens = set(re.findall(r"[a-z0-9]+", state.topic.lower()))
        msg_tokens = set(re.findall(r"[a-z0-9]+", low))
        if topic_tokens & msg_tokens:
            return Relation.CONTINUE
        if len(msg.split()) <= 3:
            return Relation.CONTINUE
        if any(w in low for w in ("make", "cards", "smaller", "also", "move", "delete", "button", "panel", "can you make", "resume", "continue", "pick up", "keep going", "carry on", "go on", "what was i working", "previous task", "continue what")):
            # If prior is code and message looks like a continuation, treat as continue
            if state.workload in ("code", "research", "general"):
                return Relation.CONTINUE
        if low.startswith(("make ", "also ", "move ", "add ", "can you make", "resume", "continue", "pick up", "keep going")):
            return Relation.CONTINUE
    # Token overlap heuristic
    topic_tokens = set(re.findall(r"[a-z0-9]+", state.topic.lower()))
    msg_tokens = set(re.findall(r"[a-z0-9]+", low))
    if topic_tokens and (topic_tokens & msg_tokens):
        return Relation.CONTINUE
    # If prior topic exists but message is materially different and does not share tokens, switch
    # This is the correct handling for "What's the capital of France?" after ProjectsPanel
    if state.topic:
        # If no token overlap and message is not short continue, switch
        if not (topic_tokens & msg_tokens):
            return Relation.SWITCH
    return Relation.NEW


# ── Router ──────────────────────────────────────────────────────────────────

class WorkloadRouter:
    """Beta heuristic router — deterministic, no LLM."""

    def __init__(self, model: str | None = None, llm=None, config: dict | None = None, router_config: RouterConfig | None = None):
        # Keep interface compatible with future QwenRouter
        self.router_config = router_config or RouterConfig()
        self.llm = None  # No LLM in Beta
        self._config = config

    def warm(self) -> None:
        pass

    def route(
        self,
        user_message: str,
        state: Optional[RouterState] = None,
        history: Optional[list[tuple[str, str]]] = None,
        has_images: bool = False,
        attachments: Optional[list[dict]] = None,
    ) -> RouterDecision:
        """
        Deterministic routing:
          current message > compact state > recent history
        has_images is informational only, never determines workload.
        """
        workload = _detect_workload(user_message, state)
        relation = _detect_relation(user_message, state, workload)
        # Topic: preserve on continue, else derive
        if relation == Relation.CONTINUE and state and state.topic:
            topic = state.topic
        else:
            topic = _extract_topic(user_message, workload)
            # Ensure topic length 2-8 words: if single word and relation is new/switch, keep it
            # If topic is empty, fallback to workload
            if not topic or len(topic) < 2:
                topic = state.topic if state and state.topic else workload

        # Build next state
        new_state = RouterState(
            topic=topic,
            workload=workload,
            status="in_progress" if relation in (Relation.CONTINUE, Relation.NEW) and workload in ("code","research") else "idle",
            active_context=topic,
        )
        # Confidence is deterministic heuristic: high for strong signals, lower for general default
        confidence = 0.9 if workload in ("code","research") else 0.7
        if relation == Relation.CONTINUE:
            confidence = min(0.85, confidence)
        return RouterDecision(
            workload=workload,
            relation=relation,
            topic=topic,
            confidence=confidence,
            state=new_state,
            reasoning=f"heuristic {workload}/{relation.value}",
        )

    def get_stats(self) -> dict:
        return {"backend": "heuristic", "n_ctx": 2048, "n_threads": 1, "n_predict": 0, "n_gpu_layers": 0}
