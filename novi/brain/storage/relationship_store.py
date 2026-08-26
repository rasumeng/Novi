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

log = logging.getLogger("novi.brain.storage.relationships")

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

# A (source, target, kind) triple is one semantic edge — never duplicated. The
# unique index is applied after the base schema so pre-existing databases that
# contain duplicates (from earlier builds) degrade to a warning instead of a
# startup crash; diff-based re-indexing cleans those up over time.
_UNIQUE_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_rel_unique "
    "ON relationships (source_id, target_id, kind)"
)


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
            try:
                self._conn.execute(_UNIQUE_INDEX_SQL)
            except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
                if "duplicate" in str(e).lower() or "unique" in str(e).lower():
                    log.warning(
                        "relationships has duplicate edges; unique index not applied (%s)",
                        e,
                    )
                else:
                    raise
            self._conn.commit()

    def add(self, relationship: Relationship) -> None:
        self.add_many([relationship])

    def add_many(self, relationships: list[Relationship]) -> None:
        if not relationships:
            return
        # De-duplicate within the batch: an (source, target, kind) triple is a
        # single semantic edge — re-asserting it must never spawn a duplicate
        # row (diff-based re-indexing relies on this).
        seen: set[tuple[str, str, str]] = set()
        rows = []
        for r in relationships:
            key = (r.source_id, r.target_id, r.kind.value)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                (
                    r.source_id,
                    r.target_id,
                    r.kind.value,
                    r.created_at.isoformat(),
                )
            )
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO relationships "
                "(source_id, target_id, kind, created_at) VALUES (?, ?, ?, ?)",
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

    def list(self, limit: int = 500, *, kind: Optional[EdgeKind] = None) -> tuple[Relationship, ...]:
        sql = "SELECT * FROM relationships"
        args: list = []
        if kind is not None:
            sql += " WHERE kind = ?"
            args.append(kind.value)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return tuple(self._row_to_rel(r) for r in rows)

    def remove(
        self,
        *,
        source_id: Optional[str] = None,
        target_id: Optional[str] = None,
        kind: Optional[EdgeKind] = None,
    ) -> int:
        """Delete edges matching every provided filter. Returns rows deleted.

        Omitting a filter means "any value" — e.g. ``remove(source_id=x)``
        drops every edge leaving ``x``; ``remove(source_id=x, kind=...)`` drops
        only that kind. At least one filter must be supplied.
        """
        if source_id is None and target_id is None and kind is None:
            return 0
        clauses: list[str] = []
        args: list = []
        if source_id is not None:
            clauses.append("source_id = ?")
            args.append(source_id)
        if target_id is not None:
            clauses.append("target_id = ?")
            args.append(target_id)
        if kind is not None:
            clauses.append("kind = ?")
            args.append(kind.value)
        sql = "DELETE FROM relationships WHERE " + " AND ".join(clauses)
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return max(cur.rowcount, 0)

    def has(self, source_id: str, target_id: str, kind: EdgeKind) -> bool:
        """True when a (source, target, kind) edge already exists."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM relationships WHERE source_id = ? AND target_id = ? "
                "AND kind = ? LIMIT 1",
                (source_id, target_id, kind.value),
            ).fetchone()
        return row is not None

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