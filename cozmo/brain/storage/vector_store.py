"""VectorStore — typed-column knowledge vector store (LanceDB).

Phase D: metadata is no longer the filter medium. KnowledgeItems are stored
with promoted typed columns (form, status, confidence, tags, sources,
scenario_id, source_kind, created_at). Filters are column predicates
(scenario_id equality, source_kind equality, form IN, list_contains on tags)
plus vector similarity. Query rows still carry a ``metadata`` dict only for
legacy consumers (runtime MemoryRetrievalSource, webui, tools).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

import lancedb
import pyarrow as pa

from ...services.embedding import EmbeddingService
from ..types import KnowledgeForm, KnowledgeItem, KnowledgeStatus

log = logging.getLogger("cozmo.brain.storage.vector")

_TABLE_NAME = "knowledge_items"

# Columns added after initial table creation; _ensure_columns migrates
# existing tables by adding any absent column with null values.
_NEW_COLUMNS = {"last_seen_at": "null::string", "importance": "null::string"}


class VectorStore:
    """LanceDB-backed typed store for KnowledgeItems.

    Implements the KnowledgeStore protocol: add / add_many / query / get /
    delete / count / list_all. The vector dimension is fixed at table
    creation; reopening validates the embedding model.
    """

    def __init__(
        self,
        persist_dir: str | Path,
        embed_model: str | EmbeddingService | None = None,
        vector_index: bool = True,
        table_name: str = _TABLE_NAME,
    ):
        if isinstance(embed_model, EmbeddingService):
            embed_service = embed_model
        else:
            from ...configuration.bootstrap import get_configuration

            cfg = get_configuration().snapshot()
            model_name = embed_model or cfg.get("embedding", {}).get("model", "")
            embed_cfg = dict(cfg)
            embed_cfg.setdefault("embedding", {})["model"] = model_name
            embed_service = EmbeddingService(embed_cfg)

        def embed(text: str) -> list[float]:
            return embed_service.encode(text, normalize=True)

        self._embedder = embed_service
        self._embed = embed
        self._embed_dim = embed_service.dimension
        self._table_name = table_name
        self._vector_index = vector_index
        self._db = lancedb.connect(str(Path(persist_dir) / "lancedb"))
        self._table = self._open_or_create()

    def _open_or_create(self):
        try:
            table = self._db.open_table(self._table_name)
            return self._ensure_columns(table)
        except Exception:
            schema = pa.schema(
                [
                    pa.field("id", pa.string()),
                    pa.field("text", pa.string()),
                    pa.field("form", pa.string()),
                    pa.field("status", pa.string()),
                    pa.field("confidence", pa.float32()),
                    pa.field("tags", pa.list_(pa.string())),
                    pa.field("sources", pa.list_(pa.string())),
                    pa.field("scenario_id", pa.string()),
                    pa.field("source_kind", pa.string()),
                    pa.field("created_at", pa.string()),
                    pa.field("last_seen_at", pa.string()),
                    pa.field("importance", pa.string()),
                    pa.field("vector", pa.list_(pa.float32(), self._embed_dim)),
                ]
            )
            return self._db.create_table(self._table_name, schema=schema)

    @staticmethod
    def _ensure_columns(table) -> object:
        """Add any new typed columns to an existing table (Phase F migration).

        Backward-compatible: opens tables created before a column existed and
        adds the absent column with null values. Returns the table.
        """
        existing = set(table.schema.names)
        add = {name: dtype for name, dtype in _NEW_COLUMNS.items() if name not in existing}
        if add:
            try:
                table.add_columns(add)
            except Exception:
                log.warning("failed to add columns %s to existing table", sorted(add), exc_info=True)
        return table

    # ── writes ─────────────────────────────────────────────────────────

    def add(self, item: KnowledgeItem, source_kind: str = "extraction") -> str:
        return self.add_many([item], source_kind=source_kind)[0]

    def add_many(
        self, items: list[KnowledgeItem], source_kind: str = "extraction"
    ) -> list[str]:
        if not items:
            return []
        ids = []
        rows = []
        for item in items:
            kid = item.id or f"kn-{uuid4().hex[:12]}"
            ids.append(kid)
            rows.append(
                {
                    "id": kid,
                    "text": item.content,
                    "form": item.form.value,
                    "status": item.status.value,
                    "confidence": float(item.confidence),
                    "tags": list(item.tags),
                    "sources": list(item.sources),
                    "scenario_id": item.scenario_id,
                    "source_kind": source_kind,
                    "created_at": item.created_at.isoformat(),
                    "last_seen_at": (item.last_seen_at or item.created_at).isoformat(),
                    "importance": f"{item.importance:.6f}",
                    "vector": self._embed(item.content),
                }
            )
        self._table.add(rows)
        return ids

    # ── reads ──────────────────────────────────────────────────────────

    def query(
        self,
        text: str,
        k: int = 5,
        distance_threshold: Optional[float] = 0.5,
        scenario_id: Optional[str] = None,
        source_kind: Optional[str] = None,
        forms: Optional[tuple[KnowledgeForm, ...] | list[KnowledgeForm]] = None,
        tags: Optional[tuple[str, ...] | list[str]] = None,
        include_superseded: bool = False,
    ) -> list[dict]:
        """Vector search with column-predicate filters.

        Superseded items are excluded by default (post-M5 hardening): history
        is preserved in the store — read-path exclusion only, never a DELETE —
        mirroring ``reasoning.tiering.tier_hits``. Pass
        ``include_superseded=True`` for explicit history/audit reads.
        """
        where = self._where_clause(
            scenario_id=scenario_id,
            source_kind=source_kind,
            forms=forms,
            tags=tags,
            include_superseded=include_superseded,
        )
        query_vec = self._embed(text)
        try:
            q = self._table.search(query_vec)
            if where:
                q = q.where(where)
            results = q.limit(k * 3).to_list()
        except Exception as e:
            log.warning("vector search failed: %s", e)
            return []
        out = []
        for r in results:
            dist = r.get("_distance", 1.0)
            if distance_threshold is not None and dist > distance_threshold:
                continue
            out.append(self._row(r))
        return out[:k]

    def get(self, item_id: str) -> Optional[dict]:
        try:
            q = self._table.search([0.0] * self._embed_dim).where(
                f"id = '{_esc(item_id)}'"
            ).limit(1)
            rows = q.to_list()
        except Exception:
            return None
        if not rows:
            return None
        return self._row(rows[0])

    def get_many(self, item_ids: list[str]) -> list[dict]:
        """Batch durable-id lookup (M4.1).

        One indexed scan for the whole id set instead of one ``get()`` per
        neighbor. Rows arrive in arbitrary store order — callers must map by
        ``row["id"]``. Missing/deleted ids are simply absent from the result;
        never raises.
        """
        wanted: list[str] = []
        seen: set[str] = set()
        for item_id in item_ids or ():
            iid = str(item_id)
            if iid and iid not in seen:
                seen.add(iid)
                wanted.append(iid)
        if not wanted:
            return []
        clause = ", ".join(f"'{_esc(i)}'" for i in wanted)
        try:
            rows = (
                self._table.search([0.0] * self._embed_dim)
                .where(f"id IN ({clause})")
                .limit(len(wanted))
                .to_list()
            )
        except Exception:
            log.warning("batch knowledge fetch failed", exc_info=True)
            return []
        return [self._row(r) for r in rows]

    def list_all(self, limit: int = 100) -> list[dict]:
        try:
            rows = self._table.search().limit(limit).to_list()
        except Exception:
            return []
        return [self._row(r) for r in rows]

    def delete(self, item_id: str) -> bool:
        try:
            self._table.delete(f"id = '{_esc(item_id)}'")
            return True
        except Exception:
            return False

    def update_status(self, item_id: str, status: KnowledgeStatus) -> bool:
        """Promote/demote an item's lifecycle status (Phase F)."""
        try:
            self._table.update(
                where=f"id = '{_esc(item_id)}'",
                values={"status": status.value},
            )
            return True
        except Exception:
            log.warning("failed to update status for %s", item_id, exc_info=True)
            return False

    def update_last_seen(self, item_id: str, last_seen_at: datetime) -> bool:
        """Record a re-observation of an item (consolidation, Phase F).

        Append-only corroboration: advances ``last_seen_at`` without creating a
        sibling row. Returns False when the update or the item is absent.
        """
        try:
            self._table.update(
                where=f"id = '{_esc(item_id)}'",
                values={"last_seen_at": last_seen_at.isoformat()},
            )
            return True
        except Exception:
            log.warning("failed to update last_seen for %s", item_id, exc_info=True)
            return False

    def count(self) -> int:
        try:
            return self._table.count_rows()
        except Exception:
            return 0

    def close(self) -> None:
        try:
            self._table.close()
        except Exception:
            pass

    # ── mapping ────────────────────────────────────────────────────────

    def _row(self, r: dict) -> dict:
        dist = r.get("_distance", 1.0)
        last_seen = r.get("last_seen_at") or r.get("created_at", "")
        return {
            "id": r.get("id", ""),
            "text": r.get("text", ""),
            "form": r.get("form", KnowledgeForm.ATOMIC.value),
            "status": r.get("status", KnowledgeStatus.CANDIDATE.value),
            "confidence": float(r.get("confidence", 0.0)),
            "tags": list(r.get("tags", ()) or ()),
            "sources": list(r.get("sources", ()) or ()),
            "scenario_id": r.get("scenario_id"),
            "source_kind": r.get("source_kind", ""),
            "last_seen_at": last_seen,
            "importance": r.get("importance", "0.0"),
            "metadata": {
                "kind": "knowledge",
                "form": r.get("form", KnowledgeForm.ATOMIC.value),
                "status": r.get("status", KnowledgeStatus.CANDIDATE.value),
                "confidence": float(r.get("confidence", 0.0)),
                "tags": list(r.get("tags", ()) or ()),
                "sources": list(r.get("sources", ()) or ()),
                "scenario_id": r.get("scenario_id"),
                "source_kind": r.get("source_kind", ""),
                "created_at": r.get("created_at", ""),
                "last_seen_at": last_seen,
                "importance": r.get("importance", "0.0"),
            },
            "distance": dist,
            "score": 1.0 - dist,
        }

    @staticmethod
    def _where_clause(
        *,
        scenario_id: Optional[str] = None,
        source_kind: Optional[str] = None,
        forms: Optional[tuple[KnowledgeForm, ...] | list[KnowledgeForm]] = None,
        tags: Optional[tuple[str, ...] | list[str]] = None,
        include_superseded: bool = False,
    ) -> str:
        clauses = []
        if not include_superseded:
            # Rows predating the status column may carry NULL — keep them
            # eligible; only an explicit superseded value is excluded.
            clauses.append("(status IS NULL OR status != 'superseded')")
        if scenario_id is not None:
            clauses.append(f"scenario_id = '{_esc(scenario_id)}'")
        if source_kind is not None:
            clauses.append(f"source_kind = '{_esc(source_kind)}'")
        if forms:
            values = "', '".join(_esc(f.value) for f in forms)
            clauses.append(f"form IN ('{values}')")
        if tags:
            tag_preds = " OR ".join(f"list_contains(tags, '{_esc(t)}')" for t in tags)
            clauses.append(f"({tag_preds})")
        return " AND ".join(f"({c})" for c in clauses)

    @classmethod
    def item_from_row(cls, row: dict) -> KnowledgeItem:
        created = row.get("created_at")
        if created is None:
            created = row.get("metadata", {}).get("created_at")
        last_seen = row.get("last_seen_at") or created
        if last_seen is None:
            last_seen = row.get("metadata", {}).get("last_seen_at")
        imp = row.get("importance")
        if imp is None:
            imp = row.get("metadata", {}).get("importance", "0.0")
        try:
            importance = float(imp or 0.0)
        except (TypeError, ValueError):
            importance = 0.0
        return KnowledgeItem(
            id=row.get("id", ""),
            form=KnowledgeForm(row.get("form", KnowledgeForm.ATOMIC.value)),
            content=row.get("text", ""),
            confidence=float(row.get("confidence", 0.0)),
            status=KnowledgeStatus(row.get("status", KnowledgeStatus.CANDIDATE.value)),
            tags=tuple(row.get("tags", ()) or ()),
            sources=tuple(row.get("sources", ()) or ()),
            scenario_id=row.get("scenario_id"),
            created_at=datetime.fromisoformat(created) if created else None,
            last_seen_at=datetime.fromisoformat(last_seen) if last_seen else None,
            importance=importance,
        )


def _esc(value: str) -> str:
    return str(value).replace("'", "''")
