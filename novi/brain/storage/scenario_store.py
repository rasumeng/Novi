"""ScenarioStore — durable scenario context objects (SQLite).

Rich scenario object with a lifecycle (created → active → paused →
completed → archived). Phase C only creates scenarios at extraction time and
tracks status; completion detection and project anchoring arrive in later
phases.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..types import Scenario, ScenarioStatus

log = logging.getLogger("novi.brain.storage.scenarios")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scenarios (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    purpose       TEXT NOT NULL DEFAULT '',
    project_id    TEXT,
    status        TEXT NOT NULL,
    goal          TEXT NOT NULL DEFAULT '',
    summary       TEXT NOT NULL DEFAULT '',
    participants  TEXT NOT NULL DEFAULT '[]',
    started_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    completed_at  TEXT
);
"""


class ScenarioStore:
    """SQLite-backed scenario store."""

    def __init__(self, persist_dir: str | Path, db_name: str = "scenarios.sqlite"):
        self._path = Path(persist_dir) / db_name
        Path(persist_dir).mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def create(self, scenario: Scenario) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO scenarios (id, name, purpose, project_id, status, goal,
                                          summary, participants, started_at, updated_at,
                                          completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scenario.id,
                    scenario.name,
                    scenario.purpose,
                    scenario.project_id,
                    scenario.status.value,
                    scenario.goal,
                    scenario.summary,
                    json.dumps(list(scenario.participants)),
                    scenario.started_at.isoformat(),
                    scenario.updated_at.isoformat(),
                    scenario.completed_at.isoformat() if scenario.completed_at else None,
                ),
            )
            self._conn.commit()

    def get(self, scenario_id: str) -> Optional[Scenario]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM scenarios WHERE id = ?", (scenario_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_scenario(row)

    def update(self, scenario: Scenario) -> None:
        with self._lock:
            self._conn.execute(
                """UPDATE scenarios SET name = ?, purpose = ?, project_id = ?, status = ?,
                         goal = ?, summary = ?, participants = ?, updated_at = ?,
                         completed_at = ?
                   WHERE id = ?""",
                (
                    scenario.name,
                    scenario.purpose,
                    scenario.project_id,
                    scenario.status.value,
                    scenario.goal,
                    scenario.summary,
                    json.dumps(list(scenario.participants)),
                    scenario.updated_at.isoformat(),
                    scenario.completed_at.isoformat() if scenario.completed_at else None,
                    scenario.id,
                ),
            )
            self._conn.commit()

    def set_status(self, scenario_id: str, status: ScenarioStatus) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE scenarios SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, datetime.now().isoformat(), scenario_id),
            )
            self._conn.commit()

    def list(self, limit: int = 100) -> tuple[Scenario, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM scenarios ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return tuple(self._row_to_scenario(r) for r in rows)

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM scenarios").fetchone()
        return int(row[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row_to_scenario(row: sqlite3.Row) -> Scenario:
        def _dt(value: Optional[str]) -> Optional[datetime]:
            return datetime.fromisoformat(value) if value else None

        try:
            participants = tuple(json.loads(row["participants"]))
        except Exception:
            participants = ()
        return Scenario(
            id=row["id"],
            name=row["name"],
            purpose=row["purpose"],
            project_id=row["project_id"],
            status=ScenarioStatus(row["status"]),
            goal=row["goal"],
            summary=row["summary"],
            participants=participants,
            started_at=_dt(row["started_at"]) or datetime.now(),
            updated_at=_dt(row["updated_at"]) or datetime.now(),
            completed_at=_dt(row["completed_at"]),
        )
