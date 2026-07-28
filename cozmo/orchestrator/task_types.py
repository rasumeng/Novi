"""
Task types — the primary objects in the Cozmo system.

Task is the universal currency. Every user request creates one.
Goal is what to accomplish (extracted from user message, resolved via memory).
Job is an execution instance of a Task.

Architecture:
  Conversation → Message → Task → ExecutionHistory → [Job₁, Job₂, ...]
                                                          │
                                                    Engine.execute()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..runtime.retrieval_policy import RetrievalPlan


def _default_retrieval_plan():
    from ..runtime.retrieval_policy import RetrievalPlan
    return RetrievalPlan()


class TaskStatus(str, Enum):
    NEW = "new"
    TRIAGING = "triaging"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    QUEUED = "queued"
    EXECUTING = "executing"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


@dataclass
class EvidenceRequirements:
    """What information sources are needed to answer this task."""
    parametric: bool = True
    external: bool = False
    project: bool = False
    memory: bool = False
    vision: bool = False


@dataclass
class EvidenceSignal:
    """One signal detected by EvidenceDetector — what triggered and how strong."""
    type: str
    strength: str
    detail: str = ""


@dataclass
class GroundingDecision:
    """Whether the system should proactively fetch external evidence.

    Single source of truth for the grounding decision.
    Populated by Orchestrator._resolve_grounding().
    """
    needs_grounding: bool = False
    confidence: float = 0.0
    reason: str = ""
    source: str = ""  # "keyword" | "heuristic" | "llm" | "none"


@dataclass
class EvidenceAnalysis:
    """Result of evidence detection — requirements + confidence + reasoning."""
    requirements: EvidenceRequirements = field(default_factory=EvidenceRequirements)
    confidence: float = 0.0
    signals: list[EvidenceSignal] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def needs_external(self) -> bool:
        signal_types = {s.type for s in self.signals}
        return bool(signal_types & {"temporal", "comparative", "dynamic"})

    @property
    def needs_memory(self) -> bool:
        signal_types = {s.type for s in self.signals}
        return bool(signal_types & {"memory", "temporal"})

    @property
    def needs_project(self) -> bool:
        return any(s.type == "project" for s in self.signals)

    @property
    def needs_vision(self) -> bool:
        return self.requirements.vision


class IntentType(str, Enum):
    CONVERSATION = "conversation"
    RESEARCH = "research"
    CODING = "coding"
    PLANNING = "planning"
    AUTONOMOUS = "autonomous"
    VISION = "vision"
    CONTINUATION = "continuation"


class ExecutionStrategy(str, Enum):
    RESPOND = "respond"
    RESEARCH = "research"
    EXECUTE = "execute"
    PLANNED = "planned"
    AUTONOMOUS = "autonomous"


@dataclass
class TaskAnalysis:
    """Complete analysis of a user task. Single object from the analysis pipeline.

    Bundles intent, evidence, complexity, capabilities, strategy, confidence,
    grounding, and retrieval plan. The runtime consumes this directly.
    """
    intent: IntentType = IntentType.CONVERSATION
    evidence: EvidenceAnalysis = field(default_factory=EvidenceAnalysis)
    complexity: 'ComplexityScore' = field(default_factory=lambda: ComplexityScore())
    capabilities: list[str] = field(default_factory=list)
    strategy: ExecutionStrategy = ExecutionStrategy.RESPOND
    confidence: float = 1.0
    grounding: GroundingDecision = field(default_factory=GroundingDecision)
    retrieval_plan: 'RetrievalPlan' = field(default_factory=_default_retrieval_plan)


@dataclass
class Goal:
    """What to accomplish. Resolved from user message via memory for continuations."""

    id: str = ""
    text: str = ""
    intent: IntentType = IntentType.CONVERSATION
    extracted_from: str = ""
    verified: bool = False
    refined: bool = False
    confidence: float = 1.0


@dataclass
class TaskProfile:
    """Intent classification result — determines capabilities, tools, strategy."""

    intent: IntentType = IntentType.CONVERSATION
    capabilities_needed: list[str] = field(default_factory=list)
    needs_planning: bool = False
    planning_level: int = 0
    model_capability: str = "chat"
    temperature: float = 0.6
    confidence: float = 1.0


@dataclass
class ComplexityScore:
    """Estimated complexity of a task."""

    score: int = 1
    plan_level: int = 0
    max_steps: int = 3
    estimated_tokens: int = 1000
    model_minimum: str = "chat"


@dataclass
class ExecutionPlan:
    """What the orchestrator produces — a complete plan for execution."""

    task_id: str = ""
    goal: Goal = field(default_factory=Goal)
    strategy: ExecutionStrategy = ExecutionStrategy.RESPOND
    capabilities: list = field(default_factory=list)
    tools: list = field(default_factory=list)
    model_spec: dict = field(default_factory=dict)
    system_prompt: str = ""
    messages: list = field(default_factory=list)
    plan: Optional[object] = None
    context: dict = field(default_factory=dict)
    max_steps: int = 10
    temperature: float = 0.6
    requires_approval: bool = False


@dataclass
class ExecutionEntry:
    """One entry in a Task's execution history."""

    job_id: str
    reason: str = "initial"
    parent_job_id: Optional[str] = None
    timestamp: str = ""


class ExecutionHistory:
    """Ordered record of every execution attempt for a Task."""

    def __init__(self):
        self.entries: list[ExecutionEntry] = []

    @property
    def last_job_id(self) -> Optional[str]:
        return self.entries[-1].job_id if self.entries else None

    @property
    def all_job_ids(self) -> list[str]:
        return [e.job_id for e in self.entries]

    def add(self, job_id: str, reason: str = "initial",
            parent_job_id: Optional[str] = None):
        self.entries.append(ExecutionEntry(
            job_id=job_id,
            reason=reason,
            parent_job_id=parent_job_id,
            timestamp=datetime.now().isoformat(),
        ))

    def count(self) -> int:
        return len(self.entries)

    def to_dict(self) -> list[dict]:
        return [
            {"job_id": e.job_id, "reason": e.reason,
             "parent_job_id": e.parent_job_id, "timestamp": e.timestamp}
            for e in self.entries
        ]


@dataclass
class Task:
    """Universal currency — every user request creates one.

    A Task holds the goal, plan, execution history, and results.
    It is persisted and referenced for continuation, branching, and audit.
    """

    id: str
    conversation_id: str = ""
    raw_goal: str = ""
    status: TaskStatus = TaskStatus.NEW
    goal: Optional[Goal] = None
    profile: Optional[TaskProfile] = None
    plan: Optional[object] = None
    execution_history: ExecutionHistory = field(default_factory=ExecutionHistory)
    result: str = ""
    error: str = ""
    parent_id: Optional[str] = None
    priority: int = 3
    created_at: str = ""
    updated_at: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        now = datetime.now().isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def is_active(self) -> bool:
        return self.status in (
            TaskStatus.TRIAGING, TaskStatus.PLANNING,
            TaskStatus.AWAITING_APPROVAL, TaskStatus.QUEUED,
            TaskStatus.EXECUTING, TaskStatus.PAUSED,
        )

    def can_continue(self) -> bool:
        return self.status in (
            TaskStatus.PAUSED, TaskStatus.COMPLETED, TaskStatus.ERROR,
        )
