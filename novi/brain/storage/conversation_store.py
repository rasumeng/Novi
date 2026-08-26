"""ConversationStore — durable append-only record of raw turns (SQLite).

Intentionally dumb: append, retrieve, list, close. No search, no embeddings,
no ranking, no summarization, no filtering. Intelligence belongs in the
Brain, not here.

Conversation identity is owned by the Brain. This store only persists the
``conversation_id`` it is given; it never generates identifiers.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..types import ConversationRecord, Turn

log = logging.getLogger("novi.brain.storage.conversations")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    scenario_id TEXT,
    project_id  TEXT,
    title       TEXT NOT NULL DEFAULT '',
    turn_count  INTEGER NOT NULL DEFAULT 0,
    started_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    user_text       TEXT NOT NULL DEFAULT '',
    assistant_text  TEXT NOT NULL DEFAULT '',
    tool_outputs    TEXT NOT NULL DEFAULT '[]',
    ts              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_conversation ON turns (conversation_id, seq);
"""


class ConversationStore:
    """SQLite-backed store for raw conversation turns."""

    def __init__(self, persist_dir: str | Path, db_name: str = "conversations.sqlite"):
        self._path = Path(persist_dir) / db_name
        Path(persist_dir).mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def append(self, turn: Turn, conversation_id: str) -> None:
        """Persist one raw turn under the Brain-supplied conversation id."""
        with self._lock:
            now = turn.timestamp.isoformat()
            self._conn.execute(
                """INSERT INTO conversations (id, title, turn_count, started_at, updated_at)
                   VALUES (?, '', 1, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       turn_count = turn_count + 1,
                       updated_at = excluded.updated_at""",
                (conversation_id, now, now),
            )
            seq = self._conn.execute(
                "SELECT COUNT(*) FROM turns WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
            self._conn.execute(
                """INSERT INTO turns (conversation_id, seq, user_text, assistant_text,
                                      tool_outputs, ts)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    conversation_id,
                    seq,
                    turn.user,
                    turn.assistant,
                    json.dumps(list(turn.tool_outputs)),
                    now,
                ),
            )
            self._conn.commit()

    def set_scenario_id(self, conversation_id: str, scenario_id: str) -> None:
        """Link a conversation to its scenario (ownership column)."""
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET scenario_id = ? WHERE id = ?",
                (scenario_id, conversation_id),
            )
            self._conn.commit()

    def get(self, conversation_id: str) -> Optional[ConversationRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def turns(self, conversation_id: str, *, limit: Optional[int] = None) -> tuple[Turn, ...]:
        query = "SELECT * FROM turns WHERE conversation_id = ? ORDER BY seq ASC"
        args: list = [conversation_id]
        if limit is not None:
            query += " LIMIT ?"
            args.append(limit)
        with self._lock:
            rows = self._conn.execute(query, args).fetchall()
        return tuple(self._row_to_turn(r) for r in rows)

    def list_conversations(self) -> tuple[ConversationRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC"
            ).fetchall()
        return tuple(self._row_to_record(r) for r in rows)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _row_to_record(self, row: sqlite3.Row) -> ConversationRecord:
        return ConversationRecord(
            id=row["id"],
            scenario_id=row["scenario_id"],
            project_id=row["project_id"],
            title=row["title"] or "",
            turn_count=row["turn_count"],
            started_at=datetime.fromisoformat(row["started_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _row_to_turn(self, row: sqlite3.Row) -> Turn:
        try:
            outputs = tuple(json.loads(row["tool_outputs"]))
        except Exception:
            outputs = ()
        return Turn(
            user=row["user_text"],
            assistant=row["assistant_text"],
            timestamp=datetime.fromisoformat(row["ts"]),
            tool_outputs=outputs,
            conversation_id=row["conversation_id"],
        )
