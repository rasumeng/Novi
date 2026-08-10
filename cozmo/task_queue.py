"""Background task queue for async operations.

Tasks are persisted to disk so they survive restarts.

Ownership (Milestone 5 Phase 5E-2C): the queue is a DISPATCH mechanism only.
The ``Task`` persisted here is queue bookkeeping (id/status/result for the UI);
it is deliberately NOT the Milestone 5 Task — that lives in the TaskStore and
owns intent/plan/history. The worker dispatches each queued prompt through the
ExecutionCoordinator (``cozmo/services.background.run_background``), which
creates the real TaskStore Task/Plan/Job/ExecutionHistory. ``Task.cozmo_task_id``
links the queue record to the resulting TaskStore Task so continuation can
discover it. No Job is ever created against a fake/orphan task id.
"""
import json
import threading
import logging
from datetime import datetime
from pathlib import Path
from enum import Enum

log = logging.getLogger("cozmo.taskqueue")

TASKS_DIR = Path.home() / ".cozmo" / "tasks"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Task:
    def __init__(self, id: str, description: str, prompt: str,
                 agent_type: str = "general", status: TaskStatus = TaskStatus.PENDING,
                 created: str = "", result: str = "", error: str = "",
                 goal: str = "", mode: str = "chat",
                 cozmo_task_id: str = ""):
        self.id = id
        self.description = description
        self.prompt = prompt
        self.agent_type = agent_type
        self.status = status
        self.created = created or datetime.now().isoformat()
        self.result = result
        self.error = error
        self.goal = goal
        self.mode = mode
        self.cozmo_task_id = cozmo_task_id

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "prompt": self.prompt,
            "agent_type": self.agent_type,
            "status": self.status.value,
            "created": self.created,
            "result": self.result,
            "error": self.error,
            "goal": self.goal,
            "mode": self.mode,
            "cozmo_task_id": self.cozmo_task_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(
            id=d["id"],
            description=d.get("description", ""),
            prompt=d.get("prompt", ""),
            agent_type=d.get("agent_type", "general"),
            status=TaskStatus(d.get("status", "pending")),
            created=d.get("created", ""),
            result=d.get("result", ""),
            error=d.get("error", ""),
            goal=d.get("goal", ""),
            mode=d.get("mode", "chat"),
            cozmo_task_id=d.get("cozmo_task_id", ""),
        )


class TaskQueue:
    def __init__(self):
        TASKS_DIR.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, Task] = {}
        self._running: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._on_update = None  # callback fn(task_id, status)
        self._load()

    def set_update_callback(self, cb):
        self._on_update = cb

    def _load(self):
        """Load pending/running tasks from disk."""
        for f in TASKS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                task = Task.from_dict(data)
                if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                    task.status = TaskStatus.PENDING  # re-queue running tasks
                self._tasks[task.id] = task
            except Exception as e:
                log.warning("Failed to load task %s: %s", f.name, e)

    def _save(self, task: Task):
        """Persist task to disk."""
        path = TASKS_DIR / f"{task.id}.json"
        path.write_text(json.dumps(task.to_dict(), indent=2), encoding="utf-8")
        if self._on_update:
            try:
                self._on_update(task.id, task.status)
            except Exception:
                pass

    def add(self, description: str, prompt: str, agent_type: str = "general",
            goal: str = "", mode: str = "chat") -> Task:
        """Add a new task to the queue."""
        import uuid
        task = Task(
            id=str(uuid.uuid4())[:8],
            description=description,
            prompt=prompt,
            agent_type=agent_type,
            goal=goal,
            mode=mode,
        )
        with self._lock:
            self._tasks[task.id] = task
            self._save(task)
        return task

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self, status: TaskStatus | None = None) -> list[Task]:
        with self._lock:
            tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        tasks.sort(key=lambda t: t.created, reverse=True)
        return tasks

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
                task.status = TaskStatus.CANCELLED
            self._save(task)
        return True

    def run_task(self, task: Task, runner):
        """Run a task in a background thread.

        runner: callable(task) -> str  -- executes the prompt through the
        ExecutionCoordinator (dispatch stays here; Task/Job lifecycle lives in
        the coordinator). The returned text is stored as the queue task result.
        """
        with self._lock:
            if task.status == TaskStatus.RUNNING:
                return
            task.status = TaskStatus.RUNNING
            self._save(task)

        def _worker():
            try:
                result = runner(task)
                text = result if isinstance(result, str) else str(result)
                with self._lock:
                    task.result = text
                    task.status = TaskStatus.COMPLETED
                    self._save(task)
            except Exception as e:
                with self._lock:
                    task.error = str(e)
                    task.status = TaskStatus.FAILED
                    self._save(task)

        t = threading.Thread(target=_worker, daemon=True)
        with self._lock:
            self._running[task.id] = t
        t.start()

    def cleanup(self, max_completed: int = 50):
        """Remove old completed tasks from disk."""
        with self._lock:
            completed = self.list_tasks(TaskStatus.COMPLETED)
            for task in completed[max_completed:]:
                path = TASKS_DIR / f"{task.id}.json"
                if path.exists():
                    path.unlink()
                del self._tasks[task.id]
