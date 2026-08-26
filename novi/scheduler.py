"""
Simple cron-like scheduler for background agent runs.

Schedules are persisted to ~/.novi/schedules.json.
A daemon thread checks every 30s for due tasks and fires on_trigger.
"""
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

log = logging.getLogger("novi.scheduler")

from .paths import home as app_home

SCHEDULES_DIR = app_home()
SCHEDULES_PATH = SCHEDULES_DIR / "schedules.json"

INTERVAL_SECS = 30


class ScheduledRun:
    def __init__(self, id: str, goal: str, description: str,
                 interval_minutes: int = 0, next_run: str = "",
                 enabled: bool = True, created: str = "",
                 last_run: str = ""):
        self.id = id
        self.goal = goal
        self.description = description or goal[:60]
        self.interval_minutes = interval_minutes
        self.next_run = next_run or datetime.now().isoformat()
        self.enabled = enabled
        self.created = created or datetime.now().isoformat()
        self.last_run = last_run

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal": self.goal,
            "description": self.description,
            "interval_minutes": self.interval_minutes,
            "next_run": self.next_run,
            "enabled": self.enabled,
            "created": self.created,
            "last_run": self.last_run,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScheduledRun":
        return cls(
            id=d["id"],
            goal=d.get("goal", ""),
            description=d.get("description", ""),
            interval_minutes=d.get("interval_minutes", 0),
            next_run=d.get("next_run", ""),
            enabled=d.get("enabled", True),
            created=d.get("created", ""),
            last_run=d.get("last_run", ""),
        )


class Scheduler:
    """Daemon thread scheduler. Checks every 30s for tasks ready to run."""

    def __init__(self):
        self._schedules: dict[str, ScheduledRun] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.on_trigger: Callable | None = None  # fn(ScheduledRun) -> None
        self._load()

    def _path(self) -> Path:
        SCHEDULES_DIR.mkdir(parents=True, exist_ok=True)
        return SCHEDULES_PATH

    def _load(self):
        path = self._path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text("utf-8"))
            for item in data.get("schedules", []):
                s = ScheduledRun.from_dict(item)
                self._schedules[s.id] = s
        except Exception as e:
            log.warning("Failed to load schedules: %s", e)

    def _save(self):
        path = self._path()
        data = {"schedules": [s.to_dict() for s in self._schedules.values()]}
        path.write_text(json.dumps(data, indent=2), "utf-8")

    def add(self, goal: str, description: str = "",
            interval_minutes: int = 0) -> ScheduledRun:
        s = ScheduledRun(
            id=str(uuid.uuid4())[:8],
            goal=goal,
            description=description,
            interval_minutes=interval_minutes,
        )
        with self._lock:
            self._schedules[s.id] = s
            self._save()
        return s

    def remove(self, schedule_id: str) -> bool:
        with self._lock:
            if schedule_id not in self._schedules:
                return False
            del self._schedules[schedule_id]
            self._save()
        return True

    def toggle(self, schedule_id: str) -> bool:
        with self._lock:
            s = self._schedules.get(schedule_id)
            if not s:
                return False
            s.enabled = not s.enabled
            self._save()
        return True

    def list(self) -> list[ScheduledRun]:
        with self._lock:
            return list(self._schedules.values())

    def get(self, schedule_id: str) -> ScheduledRun | None:
        with self._lock:
            return self._schedules.get(schedule_id)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info("scheduler started (every %ss)", INTERVAL_SECS)

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            now = datetime.now()
            due: list[ScheduledRun] = []
            with self._lock:
                for s in self._schedules.values():
                    if not s.enabled:
                        continue
                    try:
                        nxt = datetime.fromisoformat(s.next_run)
                    except Exception:
                        continue
                    if nxt <= now:
                        due.append(s)

            for s in due:
                try:
                    if self.on_trigger:
                        self.on_trigger(s)
                except Exception as e:
                    log.error("scheduler trigger failed for %s: %s", s.id, e)

                with self._lock:
                    stored = self._schedules.get(s.id)
                    if stored and stored.enabled:
                        stored.last_run = now.isoformat()
                        if stored.interval_minutes > 0:
                            stored.next_run = (now + timedelta(minutes=stored.interval_minutes)).isoformat()
                        else:
                            stored.enabled = False  # one-shot: disable after run
                        self._save()

            self._stop.wait(INTERVAL_SECS)
