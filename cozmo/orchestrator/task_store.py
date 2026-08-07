"""TaskStore — persistent task state (Milestone 5, Phase 1).

Tasks are the universal currency: every user request at the orchestrator
boundary creates or loads a Task. The store persists Task state as JSON files
so it survives restarts.

Mirrors the JobStore pattern (``cozmo/jobs/persistence.py``): one JSON file per
task under ``~/.cozmo/taskstore/``. Thread-safe behind a lock. This module owns
only Task persistence — it performs no planning and no execution.

Planning/checkpointing/resume are intentionally out of scope for Phase 1.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from .task_types import (
    ExecutionEntry,
    ExecutionHistory,
    Goal,
    IntentType,
    Task,
    TaskStatus,
)

log = logging.getLogger("cozmo.orchestrator.tasks")

TASKS_DIR = Path.home() / ".cozmo" / "taskstore"

# Statuses that render a Task no longer reusable for an active conversation.
_TERMINAL = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.ERROR,
        TaskStatus.CANCELLED,
        TaskStatus.ARCHIVED,
    }
)


def _now() -> str:
    return datetime.now().isoformat()


def _goal_to_dict(goal: Optional[Goal]) -> Optional[dict]:
    if goal is None:
        return None
    return {
        "id": goal.id,
        "text": goal.text,
        "intent": goal.intent.value,
        "extracted_from": goal.extracted_from,
        "verified": goal.verified,
        "refined": goal.refined,
        "confidence": goal.confidence,
    }


def _goal_from_dict(d: Optional[dict]) -> Optional[Goal]:
    if not d:
        return None
    return Goal(
        id=d.get("id", ""),
        text=d.get("text", ""),
        intent=IntentType(d.get("intent", "conversation")),
        extracted_from=d.get("extracted_from", ""),
        verified=d.get("verified", False),
        refined=d.get("refined", False),
        confidence=d.get("confidence", 1.0),
    )


def _task_to_dict(task: Task) -> dict:
    from ..planner.models import Plan

    plan_data = task.plan.to_dict() if isinstance(task.plan, Plan) else None
    return {
        "id": task.id,
        "conversation_id": task.conversation_id,
        "raw_goal": task.raw_goal,
        "status": task.status.value,
        "goal": _goal_to_dict(task.goal),
        "plan": plan_data,
        "execution_history": [
            {
                "job_id": e.job_id,
                "reason": e.reason,
                "parent_job_id": e.parent_job_id,
                "timestamp": e.timestamp,
            }
            for e in task.execution_history.entries
        ],
        "result": task.result,
        "error": task.error,
        "parent_id": task.parent_id,
        "priority": task.priority,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "metadata": task.metadata,
    }


def _task_from_dict(data: dict) -> Task:
    from ..planner.models import Plan

    history = ExecutionHistory()
    history.entries = [
        ExecutionEntry(
            job_id=e["job_id"],
            reason=e.get("reason", "initial"),
            parent_job_id=e.get("parent_job_id"),
            timestamp=e.get("timestamp", ""),
        )
        for e in data.get("execution_history", [])
    ]
    plan = None
    if data.get("plan"):
        try:
            plan = Plan.from_dict(data["plan"])
        except Exception:
            plan = None
    return Task(
        id=data.get("id", ""),
        conversation_id=data.get("conversation_id", ""),
        raw_goal=data.get("raw_goal", ""),
        status=TaskStatus(data.get("status", "new")),
        goal=_goal_from_dict(data.get("goal")),
        plan=plan,
        execution_history=history,
        result=data.get("result", ""),
        error=data.get("error", ""),
        parent_id=data.get("parent_id"),
        priority=data.get("priority", 3),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
        metadata=data.get("metadata", {}),
    )


class TaskStore:
    """Thread-safe JSON persistence for Tasks."""

    def __init__(self, persist_dir: str | Path | None = None):
        self._dir = Path(persist_dir) if persist_dir else TASKS_DIR
        self._lock = threading.RLock()

    def _path(self, task_id: str) -> Path:
        return self._dir / f"{task_id}.json"

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        return f"task-{datetime.now().strftime('%y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"

    # ── CRUD ────────────────────────────────────────────────────────────

    def save(self, task: Task) -> bool:
        """Persist a task to disk."""
        self._ensure_dir()
        with self._lock:
            try:
                self._path(task.id).write_text(
                    json.dumps(_task_to_dict(task), indent=2, default=str),
                    encoding="utf-8",
                )
                return True
            except Exception as e:
                log.warning("failed to save task %s: %s", task.id, e)
                return False

    def load(self, task_id: str) -> Optional[Task]:
        """Load a task from disk, or None if absent/corrupt."""
        path = self._path(task_id)
        if not path.exists():
            return None
        with self._lock:
            try:
                return _task_from_dict(json.loads(path.read_text("utf-8")))
            except Exception as e:
                log.warning("failed to load task %s: %s", task_id, e)
                return None

    def get(self, task_id: str) -> Optional[Task]:
        return self.load(task_id)

    def update(self, task: Task) -> bool:
        """Persist an updated task."""
        task.updated_at = _now()
        return self.save(task)

    def delete(self, task_id: str) -> bool:
        with self._lock:
            try:
                path = self._path(task_id)
                if path.exists():
                    path.unlink()
                return True
            except Exception as e:
                log.warning("failed to delete task %s: %s", task_id, e)
                return False

    def list_ids(self) -> list[str]:
        self._ensure_dir()
        with self._lock:
            return sorted(p.stem for p in self._dir.glob("*.json"))

    def list(self) -> list[Task]:
        return [t for t in (self.load(i) for i in self.list_ids()) if t is not None]

    # ── Queries ─────────────────────────────────────────────────────────

    def list_by_conversation(self, conversation_id: str) -> list[Task]:
        return [t for t in self.list() if t.conversation_id == conversation_id]

    def active_by_conversation(self, conversation_id: str) -> Optional[Task]:
        """The most recent non-terminal task for a conversation, if any."""
        tasks = [
            t for t in self.list_by_conversation(conversation_id)
            if t.status not in _TERMINAL
        ]
        if not tasks:
            return None
        return max(tasks, key=lambda t: t.updated_at)

    # ── Lifecycle ───────────────────────────────────────────────────────

    def get_or_create(
        self,
        *,
        conversation_id: str,
        goal_text: str,
        intent: IntentType = IntentType.CONVERSATION,
    ) -> Task:
        """Reuse the active task for a conversation, else create a fresh one.

        A request with an empty ``conversation_id`` always creates a new task
        (there is no thread to reuse).
        """
        with self._lock:
            if conversation_id:
                existing = self.active_by_conversation(conversation_id)
                if existing is not None:
                    return existing
            task = Task(
                id=self.new_id(),
                conversation_id=conversation_id,
                raw_goal=goal_text,
                status=TaskStatus.NEW,
                goal=Goal(text=goal_text, intent=intent),
                created_at=_now(),
                updated_at=_now(),
            )
            self.save(task)
            return task