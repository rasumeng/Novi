"""
ConversationStateStore — compact semantic state per conversation.

Minimal, internal. State is router output, stored keyed by conversation_id
so ambiguous follow-ups can be resolved. History itself stays in ExecutionContext.history;
this store only keeps the RouterState compact snapshot.

Persistence: in-memory + optional spill to Task.metadata.router_state.
No new directory/files — lifecycle tied to existing TaskStore/conversation.

Priority for router input:
 1. Current user message (verbatim, never truncated)
 2. Conversation state (this store)
 3. Small recent history (truncated)
"""

from __future__ import annotations

from typing import Optional

from .router import RouterState


class ConversationStateStore:
    """In-memory state per conversation_id."""

    def __init__(self):
        self._states: dict[str, RouterState] = {}

    def get(self, conversation_id: str) -> Optional[RouterState]:
        if not conversation_id:
            return None
        return self._states.get(conversation_id)

    def set(self, conversation_id: str, state: RouterState) -> None:
        if not conversation_id or state is None:
            return
        self._states[conversation_id] = state

    def update_from_decision(self, conversation_id: str, decision) -> RouterState:
        """Persist decision.state under conversation_id. Returns stored state."""
        if not conversation_id or decision is None:
            return decision.state if decision else RouterState()
        self._states[conversation_id] = decision.state
        return decision.state

    def clear(self, conversation_id: str) -> None:
        self._states.pop(conversation_id, None)
