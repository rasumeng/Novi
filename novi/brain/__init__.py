"""Novi Brain — knowledge-centric architecture seam (Phase A)."""

from .brain import Brain, get_brain, set_brain
from .types import (
    ContextResolution,
    ConversationRecord,
    EdgeKind,
    KnowledgeForm,
    KnowledgeItem,
    KnowledgeStatus,
    QueryContext,
    RecallItem,
    RecallResult,
    ReflectionReport,
    Relationship,
    Scenario,
    ScenarioStatus,
    Turn,
)

__all__ = [
    "Brain",
    "get_brain",
    "set_brain",
    "ContextResolution",
    "ConversationRecord",
    "EdgeKind",
    "KnowledgeForm",
    "KnowledgeItem",
    "KnowledgeStatus",
    "QueryContext",
    "RecallItem",
    "RecallResult",
    "ReflectionReport",
    "Relationship",
    "Scenario",
    "ScenarioStatus",
    "Turn",
]
