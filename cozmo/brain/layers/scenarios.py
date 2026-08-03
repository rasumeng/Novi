"""Scenario layer — owns scenarios and their store.

Phase C policy: one scenario per conversation at extraction time. The Brain
coordinates (it owns the conversation store); this layer only manages its own
scenario objects.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from ..reasoning.extraction import ExtractionResult
from ..storage.scenario_store import ScenarioStore
from ..types import Scenario, ScenarioStatus


class ScenarioLayer:
    """Domain manager for scenarios."""

    def __init__(self, store: ScenarioStore):
        self._store = store

    @property
    def store(self) -> ScenarioStore:
        return self._store

    def ensure_for_conversation(
        self, conversation: object, result: ExtractionResult
    ) -> str:
        """Create a scenario for a conversation, or update the existing one.

        ``conversation`` is the ConversationRecord-like object (Brain owns the
        conversation store). Returns the scenario id, always.
        """
        existing_id = getattr(conversation, "scenario_id", None)
        if existing_id:
            scenario = self._store.get(existing_id)
            if scenario is not None:
                self._update_from_extraction(scenario, result)
                return scenario.id
        scenario = Scenario(
            id=f"scn-{uuid4().hex[:12]}",
            name=result.name or "Conversation",
            purpose=result.summary or "",
            project_id=None,
            status=ScenarioStatus.ACTIVE,
            summary=result.summary,
            started_at=datetime.now(),
            updated_at=datetime.now(),
        )
        self._store.create(scenario)
        return scenario.id

    def _update_from_extraction(self, scenario: Scenario, result: ExtractionResult) -> None:
        scenario.summary = result.summary or scenario.summary
        scenario.name = result.name or scenario.name
        scenario.purpose = result.summary or scenario.purpose
        scenario.updated_at = datetime.now()
        self._store.update(scenario)
