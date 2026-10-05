"""SQLite authority for durable agent runs and their ordered event journal."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novi.runtime.run_contracts import (
    ModelSnapshot, RunEvent, RunEventType, RunImage, RunRequest, RunState, RunStatus,
)
from novi.runtime.transcript import TranscriptMessage


class RunStoreError(RuntimeError):
    pass


class RunNotFoundError(RunStoreError):
    pass


class InvalidRunTransitionError(RunStoreError):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, status TEXT NOT NULL,
    terminal_reason TEXT, state_json TEXT NOT NULL, task_id TEXT, job_id TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_conversation ON agent_runs(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS agent_run_events (
    event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES agent_runs(id),
    sequence INTEGER NOT NULL, conversation_id TEXT NOT NULL, type TEXT NOT NULL,
    timestamp TEXT NOT NULL, payload_json TEXT NOT NULL, UNIQUE(run_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_agent_run_events_replay ON agent_run_events(run_id, sequence);
CREATE TABLE IF NOT EXISTS agent_run_artifacts (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES agent_runs(id),
    media_type TEXT NOT NULL, content BLOB NOT NULL, byte_length INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""


class RunStore:
    MAX_ARTIFACT_BYTES = 2 * 1024 * 1024

    def __init__(self, persist_dir: str | Path, db_name: str = "runs.sqlite") -> None:
        path = Path(persist_dir)
        path.mkdir(parents=True, exist_ok=True)
        self.database_path = path / db_name
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.database_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    def create(self, run: RunState) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO agent_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run.id, run.conversation_id, run.status.value, run.terminal_reason,
                 json.dumps(run.to_dict(), separators=(",", ":")), run.task_id,
                 run.job_id, run.created_at.isoformat(), run.updated_at.isoformat()),
            )

    def snapshot(self, run_id: str) -> RunState:
        with self._lock:
            row = self._conn.execute("SELECT state_json FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        return _run_from_dict(json.loads(row["state_json"]))

    def append(self, event: RunEvent, state: RunState) -> None:
        if event.run_id != state.id or event.conversation_id != state.conversation_id:
            raise RunStoreError("event identity does not match run state")
        with self._lock, self._conn:
            current = self._conn.execute("SELECT status FROM agent_runs WHERE id=?", (state.id,)).fetchone()
            if current is None:
                raise RunNotFoundError(state.id)
            if RunStatus(current["status"]).terminal:
                raise InvalidRunTransitionError("terminal run cannot accept more events")
            expected = self._conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_run_events WHERE run_id=?", (state.id,)
            ).fetchone()[0]
            if event.sequence != expected:
                raise RunStoreError(f"expected event sequence {expected}, got {event.sequence}")
            self._conn.execute(
                "INSERT INTO agent_run_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event.id, event.run_id, event.sequence, event.conversation_id,
                 event.type.value, event.timestamp.isoformat(),
                 json.dumps(event.payload, separators=(",", ":"))),
            )
            self._conn.execute(
                "UPDATE agent_runs SET status=?, terminal_reason=?, state_json=?, task_id=?, job_id=?, updated_at=? WHERE id=?",
                (state.status.value, state.terminal_reason,
                 json.dumps(state.to_dict(), separators=(",", ":")), state.task_id,
                 state.job_id, state.updated_at.isoformat(), state.id),
            )

    def transition(self, run_id: str, status: RunStatus, event: RunEvent,
                   *, reason: str | None = None) -> RunState:
        current = self.snapshot(run_id)
        _validate_transition(current.status, status)
        state = replace(current, status=status, terminal_reason=reason,
                        updated_at=datetime.now(timezone.utc))
        self.append(event, state)
        return state

    def update_snapshot(self, state: RunState) -> None:
        """Replace run state after its emitted events have already been journaled."""
        with self._lock, self._conn:
            current = self._conn.execute(
                "SELECT conversation_id FROM agent_runs WHERE id=?", (state.id,)).fetchone()
            if current is None:
                raise RunNotFoundError(state.id)
            if current["conversation_id"] != state.conversation_id:
                raise RunStoreError("snapshot conversation identity changed")
            self._conn.execute(
                "UPDATE agent_runs SET status=?, terminal_reason=?, state_json=?, task_id=?, job_id=?, updated_at=? WHERE id=?",
                (state.status.value, state.terminal_reason,
                 json.dumps(state.to_dict(), separators=(",", ":")), state.task_id,
                 state.job_id, state.updated_at.isoformat(), state.id),
            )

    def events(self, run_id: str, after_sequence: int = 0) -> tuple[RunEvent, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM agent_run_events WHERE run_id=? AND sequence>? ORDER BY sequence",
                (run_id, after_sequence),
            ).fetchall()
        return tuple(RunEvent(
            id=row["event_id"], sequence=row["sequence"], run_id=row["run_id"],
            conversation_id=row["conversation_id"], type=RunEventType(row["type"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
            payload=json.loads(row["payload_json"]),
        ) for row in rows)

    def runs_for_conversation(self, conversation_id: str) -> tuple[RunState, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT state_json FROM agent_runs WHERE conversation_id=? ORDER BY created_at",
                (conversation_id,),
            ).fetchall()
        return tuple(_run_from_dict(json.loads(row["state_json"])) for row in rows)

    def put_artifact(self, artifact_id: str, run_id: str, content: bytes,
                     media_type: str = "application/octet-stream") -> None:
        if len(content) > self.MAX_ARTIFACT_BYTES:
            raise RunStoreError(f"artifact exceeds {self.MAX_ARTIFACT_BYTES} byte limit")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO agent_run_artifacts VALUES (?, ?, ?, ?, ?, ?)",
                (artifact_id, run_id, media_type, content, len(content),
                 datetime.now(timezone.utc).isoformat()),
            )

    def artifact(self, artifact_id: str) -> tuple[str, bytes]:
        with self._lock:
            row = self._conn.execute(
                "SELECT media_type, content FROM agent_run_artifacts WHERE id=?", (artifact_id,)
            ).fetchone()
        if row is None:
            raise RunStoreError(f"artifact not found: {artifact_id}")
        return row["media_type"], bytes(row["content"])

    def interrupt_active_runs(self) -> int:
        with self._lock:
            ids = [row["id"] for row in self._conn.execute(
                "SELECT id FROM agent_runs WHERE status IN (?, ?, ?)",
                (RunStatus.QUEUED.value, RunStatus.RUNNING.value,
                 RunStatus.AWAITING_PERMISSION.value),
            ).fetchall()]
        for run_id in ids:
            run = self.snapshot(run_id)
            seq = len(self.events(run_id)) + 1
            self.transition(run_id, RunStatus.INTERRUPTED, RunEvent(
                id=f"{run_id}:interrupted:{seq}", sequence=seq, run_id=run_id,
                conversation_id=run.conversation_id, type=RunEventType.RUN_INTERRUPTED,
                payload={"reason": "process_restart_unknown_effect_state"}),
                reason="process_restart_unknown_effect_state")
        return len(ids)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _validate_transition(old: RunStatus, new: RunStatus) -> None:
    if old.terminal:
        raise InvalidRunTransitionError("terminal run cannot transition")
    allowed = {
        RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.INTERRUPTED},
        RunStatus.RUNNING: {RunStatus.AWAITING_PERMISSION, RunStatus.COMPLETED,
                            RunStatus.BLOCKED, RunStatus.FAILED,
                            RunStatus.CANCELLED, RunStatus.INTERRUPTED},
        RunStatus.AWAITING_PERMISSION: {RunStatus.RUNNING, RunStatus.BLOCKED,
                                        RunStatus.CANCELLED, RunStatus.INTERRUPTED},
    }
    if new not in allowed.get(old, set()):
        raise InvalidRunTransitionError(f"invalid transition: {old.value} -> {new.value}")


def _run_from_dict(value: dict[str, Any]) -> RunState:
    rv = value["request"]
    mv = rv.get("model")
    request = RunRequest(
        conversation_id=rv["conversation_id"], user_message_id=rv["user_message_id"],
        user_text=rv["user_text"], project_id=rv.get("project_id", ""),
        workspace=rv.get("workspace", ""),
        images=tuple(RunImage(**item) for item in rv.get("images", ())),
        research_selected=bool(rv.get("research_selected", False)),
        model=ModelSnapshot(**mv) if mv else None)
    return RunState(
        id=value["id"], request=request, status=RunStatus(value["status"]),
        terminal_reason=value.get("terminal_reason"), current_turn=int(value.get("current_turn", 0)),
        transcript=tuple(TranscriptMessage.from_dict(item) for item in value.get("transcript", ())),
        context_summary=value.get("context_summary", ""),
        pending_call_ids=tuple(value.get("pending_call_ids", ())),
        pending_permission_ids=tuple(value.get("pending_permission_ids", ())),
        model_turns=int(value.get("model_turns", 0)), tool_calls=int(value.get("tool_calls", 0)),
        elapsed_ms=int(value.get("elapsed_ms", 0)), input_tokens=value.get("input_tokens"),
        output_tokens=value.get("output_tokens"), task_id=value.get("task_id"), job_id=value.get("job_id"),
        created_at=datetime.fromisoformat(value["created_at"]),
        updated_at=datetime.fromisoformat(value["updated_at"]))
