"""
TimelineService — subscribes to Brain events and turns them into user-facing
TimelineEntries.

This is a passive consumer (Architecture Rule: events describe what happened).
It never emits into the Brain bus, never controls execution, and only reads
the payloads the Brain already published. Each surfaced event becomes:

    - a persisted TimelineEntry (via TimelineStore), and
    - a WebSocket ``assistant_event`` (via an optional ``on_entry`` callback
      wired by the WebUI server layer).

Only three Brain events are surfaced — no new Brain events are introduced:

    conversation.observed -> "Conversation logged"
    knowledge.extracted  -> "Memory updated"
    knowledge.promoted   -> "Knowledge refined"

The output schema is user-facing only: kind/title/detail/timestamp plus a
per-row instance id. No internal ids, scores, embeddings, distances, or
storage paths are included.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from .timeline_store import TimelineStore

log = logging.getLogger("cozmo.timeline.service")

CONVERSATION_OBSERVED = "conversation.observed"
KNOWLEDGE_EXTRACTED = "knowledge.extracted"
KNOWLEDGE_PROMOTED = "knowledge.promoted"

# Job lifecycle (Milestone 5 Phase 4E) — presentation-only projection.
JOB_CREATED = "job.created"
JOB_STARTED = "job.started"
JOB_COMPLETED = "job.completed"
JOB_FAILED = "job.failed"
JOB_CHECKPOINTED = "job.checkpointed"
JOB_INTERRUPTED = "job.interrupted"

SURFACED_EVENTS = {
    CONVERSATION_OBSERVED, KNOWLEDGE_EXTRACTED, KNOWLEDGE_PROMOTED,
    JOB_CREATED, JOB_STARTED, JOB_COMPLETED, JOB_FAILED,
    JOB_CHECKPOINTED, JOB_INTERRUPTED,
}

_JOB_LIFECYCLE_TITLES = {
    JOB_CREATED: "Task scheduled",
    JOB_STARTED: "Execution started",
    JOB_COMPLETED: "Execution completed",
    JOB_FAILED: "Execution failed",
    JOB_CHECKPOINTED: "Progress saved",
    JOB_INTERRUPTED: "Execution interrupted",
}

_DETAIL_TRUNCATE = 200


def _truncate(text: str, n: int = _DETAIL_TRUNCATE) -> str:
    text = (text or "").strip()
    if len(text) > n:
        return text[: n - 1].rstrip() + "…"
    return text


def _entry_for(event) -> dict:
    """Translate a Brain bus event into a user-facing timeline entry dict."""
    t = event.type
    data = event.data
    timestamp = event.timestamp or ""
    if t == CONVERSATION_OBSERVED:
        detail = _truncate(data.get("user", ""))
        if not detail:
            detail = "Conversation activity recorded."
        return {
            "kind": t,
            "title": "Conversation logged",
            "detail": detail,
            "timestamp": timestamp,
            # A conversation is a user-facing app entity (not an internal /
            # vector id) — carried so timeline rows can deep-link to the thread.
            "conversation_id": (data.get("conversation_id") or ""),
        }
    if t == KNOWLEDGE_EXTRACTED:
        detail = _truncate(data.get("summary", ""))
        if not detail:
            detail = "Cozmo captured new details from a conversation."
        return {
            "kind": t,
            "title": "Memory updated",
            "detail": detail,
            "timestamp": timestamp,
        }
    if t == KNOWLEDGE_PROMOTED:
        promotions = int(data.get("promotions") or 0)
        superseded = int(data.get("superseded") or 0)
        corroborated = int(data.get("corroborated") or 0)
        parts = []
        if promotions:
            parts.append(f"promoted {promotions}")
        if corroborated:
            parts.append(f"corroborated {corroborated}")
        if superseded:
            parts.append(f"superseded {superseded}")
        if not parts:
            parts.append("refined")
        detail = f"Cozmo {', '.join(parts)} knowledge item{'s' if sum([promotions, corroborated, superseded]) != 1 else ''}."
        return {
            "kind": t,
            "title": "Knowledge refined",
            "detail": detail,
            "timestamp": timestamp,
        }
    if t in (JOB_CREATED, JOB_STARTED, JOB_COMPLETED, JOB_FAILED,
             JOB_CHECKPOINTED, JOB_INTERRUPTED):
        return {
            "kind": t,
            "title": _JOB_LIFECYCLE_TITLES.get(t, "Execution"),
            "detail": _job_detail(t, data),
            "timestamp": timestamp,
            "task_id": (data.get("task_id") or ""),
            "job_id": (data.get("job_id") or ""),
        }
    return {
        "kind": t,
        "title": "Assistant activity",
        "detail": _truncate(str(data.get("detail", ""))),
        "timestamp": timestamp,
    }


def _job_detail(t: str, data: dict) -> str:
    step = data.get("step")
    result = data.get("result") or data.get("error") or ""
    task_id = data.get("task_id", "") or ""
    if task_id:
        prefix = f"Task {task_id}."
    else:
        prefix = ""
    if step is not None:
        return f"{prefix} Checkpoint at step {step}."
    if result:
        return f"{prefix} {_truncate(str(result))}"
    return prefix.rstrip(".") or "Execution activity."


class TimelineService:
    """Aggregates Brain events into a persistent, user-facing timeline."""

    def __init__(
        self,
        event_bus,
        store: Optional[TimelineStore] = None,
        on_entry: Optional[Callable[[dict], None]] = None,
    ):
        self._bus = event_bus
        self._store = store or TimelineStore()
        self._on_entry = on_entry
        self._lock = threading.Lock()
        self._subscribed = False

    @property
    def store(self) -> TimelineStore:
        return self._store

    def start(self) -> "TimelineService":
        """Subscribe to the Brain event bus (idempotent)."""
        with self._lock:
            if not self._subscribed:
                self._bus.on_any(self._on_event)
                self._subscribed = True
        return self

    def handle_event(self, event) -> Optional[dict]:
        """Process a single bus event. Public for testability."""
        if event.type not in SURFACED_EVENTS:
            return None
        entry = self._entry_for(event)
        stored = self._store.append(entry)
        if self._on_entry is not None:
            try:
                self._on_entry(stored)
            except Exception:
                log.warning("timeline on_entry callback failed", exc_info=True)
        return stored

    def _entry_for(self, event) -> dict:
        return _entry_for(event)

    def _on_event(self, event) -> None:
        try:
            self.handle_event(event)
        except Exception:
            log.warning("timeline failed to process event %s", getattr(event, "type", "?"), exc_info=True)

    def recent(self, limit: int = 200) -> list[dict]:
        return self._store.list(limit=limit)


def build_knowledge_overview(brain) -> dict:
    """User-shaped "What Cozmo knows" view from the Brain's projection.

    Reads ``brain.inspect_memory()`` and keeps only what a user can
    understand: category, friendly label, statement content, and an evidence
    description. Never returns ids, scores, distances, or storage paths.
    """
    if brain is None:
        return {"categories": [], "total": 0, "updated": ""}
    view = brain.inspect_memory() or {}
    categories = view.get("categories", {}) or {}
    if not isinstance(categories, dict):
        categories = {}
    now = view.get("items") or ()
    updated = ""
    if now:
        updated = _latest_timestamp(now)

    rows = []
    total = 0
    for key in sorted(categories.keys()):
        entries = []
        for e in categories[key]:
            content = str(e.get("content", "")).strip()
            if not content:
                continue
            entries.append({
                "content": content,
                "evidence": _evidence_description(e.get("evidence", "")),
            })
        if entries:
            total += len(entries)
            rows.append({
                "category": key,
                "label": _CATEGORY_LABELS.get(key, key.replace("_", " ").title()),
                "entries": entries,
            })
    return {"categories": rows, "total": total, "updated": updated}


_CATEGORY_LABELS = {
    "preference": "Preferences",
    "goal": "Goals",
    "skill": "Abilities",
    "project": "Projects",
    "event": "Events",
    "relationship": "Relationships",
    "identity": "About you",
}

_EVIDENCE_DESCRIPTIONS = {
    "verified": "Confirmed by repeated agreement",
    "corroborated": "Supported by multiple mentions",
    "candidate": "Not yet confirmed",
}


def _evidence_description(evidence: str) -> str:
    return _EVIDENCE_DESCRIPTIONS.get(evidence, evidence or "Not yet confirmed")


def _latest_timestamp(items) -> str:
    stamps = [i.get("last_seen_at") or i.get("created_at") for i in items if i]
    stamps = [s for s in stamps if s]
    if not stamps:
        return ""
    return max(stamps)