"""Storage protocols — the contract between the Brain and persistence.

Architecture Rule #1: the Brain owns knowledge; storage never owns knowledge.
SQLite, LanceDB, Markdown, JSON — all persistence mechanisms are
implementation details behind these interfaces. The Brain (and its layers)
depend on these protocols, never on a concrete backend.

Phase A defines the contracts only; concrete adapters arrive with the
storage migration phases.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

from ..types import (
    ConversationRecord,
    EdgeKind,
    KnowledgeForm,
    KnowledgeItem,
    KnowledgeStatus,
    Relationship,
    Scenario,
    ScenarioStatus,
    Turn,
)


class KnowledgeStore(Protocol):
    """Persistence for knowledge items — the single contract VectorStore implements.

    The layer depends on this protocol only. It returns flat rows (
    ``list[dict]`` via ``add_many``/``query``/``list_all``) so the layer decides
    the object boundary (KnowledgeHit); storage never owns domain objects.
    """

    def add(self, item: KnowledgeItem, source_kind: str = "extraction") -> str: ...

    def add_many(
        self, items: list[KnowledgeItem], source_kind: str = "extraction"
    ) -> list[str]: ...

    def query(
        self,
        text: str,
        k: int = 5,
        distance_threshold: Optional[float] = 0.5,
        scenario_id: Optional[str] = None,
        source_kind: Optional[str] = None,
        forms: Optional[tuple[KnowledgeForm, ...] | list[KnowledgeForm]] = None,
        tags: Optional[tuple[str, ...] | list[str]] = None,
    ) -> list[dict]: ...

    def get(self, item_id: str) -> Optional[dict]: ...

    def delete(self, item_id: str) -> bool: ...

    def count(self) -> int: ...

    def list_all(self, limit: int = 100) -> list[dict]: ...

    def update_status(self, item_id: str, status: KnowledgeStatus) -> bool: ...

    def update_last_seen(self, item_id: str, last_seen_at: datetime) -> bool: ...

    @classmethod
    def item_from_row(cls, row: dict) -> KnowledgeItem: ...


class MarkdownStore(Protocol):
    """Persistence for the OKF Markdown mirror of Brain knowledge.

    Markdown is a canonical human-readable durable layer (A.4), not a derived
    index. The Brain writes through to it on learn/correct/extract and reads
    it back during reconciliation. Identity is the frontmatter ``id``; the
    deterministic content ``identity`` key keeps synchronization idempotent.
    """

    def write_item(
        self, item: KnowledgeItem, *, source_kind: str = "explicit"
    ) -> tuple[str, bool]: ...

    def parse(self, path: str | Path) -> tuple[dict, str]: ...

    def list_files(self) -> tuple[Path, ...]: ...

    def find_for_id(self, item_id: str) -> Optional[Path]: ...

    def update_status(self, item_id: str, status: KnowledgeStatus) -> bool: ...

    def read_item(self, path: str | Path) -> Optional[KnowledgeItem]: ...


class ScenarioStore(Protocol):
    """Persistence for scenarios."""

    def create(self, scenario: Scenario) -> None: ...

    def get(self, scenario_id: str) -> Optional[Scenario]: ...

    def update(self, scenario: Scenario) -> None: ...

    def set_status(self, scenario_id: str, status: ScenarioStatus) -> None: ...

    def list(self, limit: int = 100) -> tuple[Scenario, ...]: ...

    def count(self) -> int: ...


class ConversationStore(Protocol):
    """Persistence for raw turns and conversation records."""

    def append(self, turn: Turn, conversation_id: str) -> None: ...

    def set_scenario_id(self, conversation_id: str, scenario_id: str) -> None: ...

    def get(self, conversation_id: str) -> Optional[ConversationRecord]: ...

    def turns(self, conversation_id: str, *, limit: Optional[int] = None) -> tuple[Turn, ...]: ...


class RelationshipStore(Protocol):
    """Persistence for typed cross-layer edges."""

    def add(self, relationship: Relationship) -> None: ...

    def add_many(self, relationships: list[Relationship]) -> None: ...

    def outgoing(
        self, source_id: str, *, kind: Optional[EdgeKind] = None
    ) -> tuple[Relationship, ...]: ...

    def incoming(
        self, target_id: str, *, kind: Optional[EdgeKind] = None
    ) -> tuple[Relationship, ...]: ...
