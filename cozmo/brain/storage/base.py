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
    KnowledgeItem,
    KnowledgeStatus,
    Project,
    Relationship,
    Scenario,
    Turn,
)


class KnowledgeStore(Protocol):
    """Persistence for knowledge items."""

    def put(self, item: KnowledgeItem) -> None: ...

    def get(self, item_id: str) -> Optional[KnowledgeItem]: ...

    def search(
        self, query: str, k: int = 5, *, scenario_id: Optional[str] = None
    ) -> tuple[KnowledgeItem, ...]: ...

    def delete(self, item_id: str) -> bool: ...

    def count(self) -> int: ...


class ScenarioStore(Protocol):
    """Persistence for scenarios."""

    def put(self, scenario: Scenario) -> None: ...

    def get(self, scenario_id: str) -> Optional[Scenario]: ...

    def list_active(self) -> tuple[Scenario, ...]: ...

    def list_by_project(self, project_id: str) -> tuple[Scenario, ...]: ...


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
