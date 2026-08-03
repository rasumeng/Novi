"""RelationshipStore — typed cross-layer edges (SQLite).

Provenance is a first-class relationship: knowledge items are linked to their
source conversation (`derived_from`) and their scenario (`observed_in`) via
edges, not JSON metadata. Retrieval can traverse the graph before scoring
(Phase E).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..types import EdgeKind, Relationship

log = logging.getLogger("cozmo.brain.storage.relationships")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS relationships (
    source_id  TEXT NOT NULL,
    target_id  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships (source_id, kind);
CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships (target_id, kind);
"""


class RelationshipStore:
    """SQLite-backed typed edge store."""

    def __init__(self, persist_dir: str | Path, db_name: str = "relationships.sqlite"):
        self._path = Path(persist_dir) / db_name
        Path(persist_dir).mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def add(self, relationship: Relationship) -> None:
        self.add_many([relationship])

    def add_many(self, relationships: list[Relationship]) -> None:
        if not relationships:
            return
        rows = [
            (
                r.source_id,
                r.target_id,
                r.kind.value,
                r.created_at.isoformat(),
            )
            for r in relationships
        ]
        with self._lock:
            self._conn.executemany(
                "INSERT INTO relationships (source_id, target_id, kind, created_at) "
                "VALUES (?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()

    def outgoing(
        self, source_id: str, *, kind: Optional[EdgeKind] = None
    ) -> tuple[Relationship, ...]:
        return self._query("source_id", source_id, kind)

    def incoming(
        self, target_id: str, *, kind: Optional[EdgeKind] = None
    ) -> tuple[Relationship, ...]:
        return self._query("target_id", target_id, kind)

    def list(self, limit: int = 500) -> tuple[Relationship, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM relationships "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(self._row_to_rel(r) for r in rows)

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM relationships").fetchone()
        return int(row[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _query(
        self, column: str, value: str, kind: Optional[EdgeKind]
    ) -> tuple[Relationship, ...]:
        sql = f"SELECT * FROM relationships WHERE {column} = ?"
        args: list = [value]
        if kind is not None:
            sql += " AND kind = ?"
            args.append(kind.value)
        sql += " ORDER BY created_at ASC"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return tuple(self._row_to_rel(r) for r in rows)

    @staticmethod
    def _row_to_rel(row: sqlite3.Row) -> Relationship:
        created = row["created_at"]
        return Relationship(
            source_id=row["source_id"],
            target_id=row["target_id"],
            kind=EdgeKind(row["kind"]),
            created_at=datetime.fromisoformat(created) if created else None,
        )