"""Assistant timeline — user-facing record of Brain activity (Milestone 4).

Additive, passive bridge: TimelineService subscribes to the Brain's own event
bus and persists user-facing TimelineEntries. No Brain internals are touched.
"""

from .timeline_service import (
    CONVERSATION_OBSERVED,
    KNOWLEDGE_EXTRACTED,
    KNOWLEDGE_PROMOTED,
    JOB_CREATED,
    JOB_STARTED,
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_CHECKPOINTED,
    JOB_INTERRUPTED,
    SURFACED_EVENTS,
    TimelineService,
    build_knowledge_overview,
)
from .timeline_store import TimelineStore

__all__ = [
    "CONVERSATION_OBSERVED",
    "KNOWLEDGE_EXTRACTED",
    "KNOWLEDGE_PROMOTED",
    "JOB_CREATED",
    "JOB_STARTED",
    "JOB_COMPLETED",
    "JOB_FAILED",
    "JOB_CHECKPOINTED",
    "JOB_INTERRUPTED",
    "SURFACED_EVENTS",
    "TimelineService",
    "TimelineStore",
    "build_knowledge_overview",
]