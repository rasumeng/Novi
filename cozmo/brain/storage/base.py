"""Storage protocols — the contract between the Brain and persistence.

Architecture Rule #1: the Brain owns knowledge; storage never owns knowledge.
SQLite, LanceDB, Markdown, JSON — all persistence mechanisms are
implementation details behind these interfaces. The Brain (and its layers)
depend on these protocols, never on a concrete backend.

Phase A defines the contracts only; concrete adapters arrive with the
storage migration phases.
"""

from __future__ import annotations

from typing import Optional, Protocol

from ..types import (
    ConversationRecord,
    EdgeKind,
    IdentityEntry,
    KnowledgeForm,
    KnowledgeItem,
    KnowledgeStatus,
    Project,
    Relationship,
    Scenario,
    ScenarioStatus,
    Turn,
)


class KnowledgeStore(Protocol):
    """Persistence for knowledge items."""

    def add(self, item: KnowledgeItem) -> str: ...

    def add_many(self, items: list[KnowledgeItem]) -> list[str]: ...

    def query(
        self,
        text: str,
        k: int = 5,
        distance_threshold: Optional[float] = 0.5,
        tags: Optional[tuple[str, ...]] = None,
        forms: Optional[tuple[KnowledgeForm, ...]] = None,
    ) -> list[dict]: ...

    def get(self, item_id: str) -> Optional[dict]: ...

    def delete(self, item_id: str) -> bool: ...

    def count(self) -> int: ...


class ScenarioStore(Protocol):
    """Persistence for scenarios."""

    def create(self, scenario: Scenario) -> None: ...

    def get(self, scenario_id: str) -> Optional[Scenario]: ...

    def update(self, scenario: Scenario) -> None: ...

    def set_status(self, scenario_id: str, status: ScenarioStatus) -> None: ...

    def list(self, limit: int = 100) -> tuple[Scenario, ...]: ...

    def count(self) -> int: ...


class ProjectStore(Protocol):
    """Persistence for projects."""

    def put(self, project: Project) -> None: ...

    def get(self, project_id: str) -> Optional[Project]: ...

    def list(self) -> tuple[Project, ...]: ...


class IdentityStore(Protocol):
    """Persistence for accumulated identity evidence."""

    def put(self, entry: IdentityEntry) -> None: ...

    def get(self, entry_id: str) -> Optional[IdentityEntry]: ...

    def list(self, status: Optional[KnowledgeStatus] = None) -> tuple[IdentityEntry, ...]: ...


class ConversationStore(Protocol):
    """Persistence for raw turns and conversation records."""

    def append(self, turn: Turn, conversation_id: str) -> None: ...

    def set_scenario_id(self, conversation_id: str, scenario_id: str) -> None: ...

    def get(self, conversation_id: str) -> Optional[ConversationRecord]: ...

    def turns(self, conversation_id: str, *, limit: Optional[int] = None) -> tuple[Turn, ...]: ...


class RelationshipStore(Protocol):
    """Persistence for typed cross-layer edges."""

    def add(self, relationship: Relationship) -> None: ...

    def outgoing(
        self, source_id: str, *, kind: Optional[EdgeKind] = None
    ) -> tuple[Relationship, ...]: ...

    def incoming(
        self, target_id: str, *, kind: Optional[EdgeKind] = None
    ) -> tuple[Relationship, ...]: ...
