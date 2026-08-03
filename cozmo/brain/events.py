"""Brain domain event vocabulary.

Events are best-effort notifications emitted AFTER state is persisted; they
never describe work that has not completed. Every event follows the same
pattern: one name constant plus one payload dataclass carrying canonical
Brain identifiers only — never storage rows, SQL ids, or persistence details.
Emitted via the Brain's event_bus as ``emit(NAME, **payload.to_payload())``.

Future vocabulary (same pattern, emitted by later phases):
    KnowledgePromoted    -> knowledge.promoted
    ScenarioUpdated      -> scenario.updated
    ProjectUpdated       -> project.updated
    IdentityUpdated      -> identity.updated
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

CONVERSATION_OBSERVED = "conversation.observed"
KNOWLEDGE_EXTRACTED = "knowledge.extracted"


@dataclass(frozen=True)
class ConversationObserved:
    """Emitted after a turn is durably persisted to the conversation store."""

    conversation_id: str
    user: str
    assistant: str
    timestamp: datetime
    tool_outputs: tuple[str, ...] = ()

    def to_payload(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "user": self.user,
            "assistant": self.assistant,
            "timestamp": self.timestamp.isoformat(),
            "tool_outputs": list(self.tool_outputs),
        }


@dataclass(frozen=True)
class KnowledgeExtracted:
    """Emitted after extracted knowledge is durably written.

    Carries canonical Brain identifiers only — never storage rows or ids.
    """

    knowledge_ids: tuple[str, ...]
    conversation_id: str
    scenario_id: str
    summary: str = ""

    def to_payload(self) -> dict:
        return {
            "knowledge_ids": list(self.knowledge_ids),
            "conversation_id": self.conversation_id,
            "scenario_id": self.scenario_id,
            "summary": self.summary,
        }
