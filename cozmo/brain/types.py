"""Brain knowledge model and context object types.

The knowledge axis is **form**, not kind. A preference, a lesson, and a fact
are the same structural object (KnowledgeItem); only soft tags differ.
Identity, scenarios, projects, and conversations are context objects that
organize knowledge items — they are not themselves knowledge items.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class KnowledgeForm(str, Enum):
    """Shape of a knowledge item — the structural axis that matters."""

    ATOMIC = "atomic"
    COMPOSITE = "composite"
    EPISODIC = "episodic"


class KnowledgeStatus(str, Enum):
    """Confidence lifecycle shared by knowledge and identity."""

    CANDIDATE = "candidate"
    CORROBORATED = "corroborated"
    VERIFIED = "verified"
    SUPERSEDED = "superseded"


class ScenarioStatus(str, Enum):
    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class EdgeKind(str, Enum):
    """Typed cross-layer relationships. Provenance is the derived_from edge."""

    DERIVED_FROM = "derived_from"
    OBSERVED_IN = "observed_in"
    SUPERSEDES = "supersedes"
    REFERENCES = "references"
    CONFLICTS_WITH = "conflicts_with"
    CONTAINS = "contains"


@dataclass
class KnowledgeItem:
    """A single piece of knowledge, uniform across domains.

    ``scenario_id`` is an ownership column: traversing the hierarchy down is a
    column lookup, not an edge scan.
    """

    id: str
    form: KnowledgeForm
    content: str
    confidence: float
    status: KnowledgeStatus = KnowledgeStatus.CANDIDATE
    tags: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    scenario_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    embedding: Optional[list[float]] = None


@dataclass
class Scenario:
    """First-class reasoning context — why a conversation happened."""

    id: str
    name: str
    purpose: str
    project_id: Optional[str]
    status: ScenarioStatus = ScenarioStatus.CREATED
    goal: str = ""
    summary: str = ""
    participants: tuple[str, ...] = ()
    started_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None


@dataclass
class Project:
    """A project organizes scenarios and knowledge."""

    id: str
    name: str
    root: Optional[str] = None
    summary: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class IdentityEntry:
    """Accumulated, confidence-weighted evidence about the user.

    Change is a ``supersedes`` edge, never an overwrite.
    """

    id: str
    content: str
    status: KnowledgeStatus = KnowledgeStatus.CANDIDATE
    corroborations: int = 0
    confirmed: bool = False
    tags: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    superseded_by: Optional[str] = None


@dataclass
class ConversationRecord:
    """Context object for a conversation thread."""

    id: str
    scenario_id: Optional[str] = None
    project_id: Optional[str] = None
    title: str = ""
    turn_count: int = 0
    started_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class Relationship:
    """A typed edge between any two brain objects."""

    source_id: str
    target_id: str
    kind: EdgeKind
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class Turn:
    """Raw experience captured by observe(): one user/assistant exchange.

    ``conversation_id`` is a canonical Brain identifier. When absent, the
    Brain assigns one; the store never generates identifiers.
    """

    user: str
    assistant: str
    timestamp: datetime = field(default_factory=datetime.now)
    tool_outputs: tuple[str, ...] = ()
    conversation_id: Optional[str] = None


@dataclass
class QueryContext:
    """Constraints for recall: layered anchor + result tuning."""

    project_id: Optional[str] = None
    scenario_id: Optional[str] = None
    top_k: int = 5
    distance_threshold: Optional[float] = 0.5
    memory_types: tuple[str, ...] = ()


@dataclass
class RecallItem:
    """One retrieved item, source-tagged for transparent provenance."""

    text: str
    score: float = 0.0
    source: str = "memory"
    metadata: dict = field(default_factory=dict)


@dataclass
class RecallResult:
    query: str
    items: tuple[RecallItem, ...] = ()
    metrics: dict = field(default_factory=dict)


@dataclass
class ContextResolution:
    """Outcome of resolve(): which project + scenario a query belongs to."""

    project_id: Optional[str] = None
    scenario_id: Optional[str] = None
    confidence: float = 0.0
    method: str = ""


@dataclass
class ReflectionReport:
    """Outcome of a reflect() consolidation pass."""

    merges: int = 0
