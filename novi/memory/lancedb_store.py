"""
LanceStore — LanceDB-backed vector store with hybrid search and importance ranking.

Replaces ChromaDB. Local-first, disk-backed, no daemon.
Native hybrid search (vector + FTS) without manual scoring hacks.
Structured metadata queries via SQL-like expressions.

Architecture:
  LanceStore
    ├── add_texts(texts, metadatas) → auto-embed → insert
    ├── similarity_search(query, k, filters) → vector search
    ├── hybrid_search(query, k, filters) → vector + FTS
    └── search_with_importance(query, k) → ranked by relevance × recency × frequency
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

import lancedb
import pyarrow as pa

log = logging.getLogger("novi.memory.lance")


class LanceStore:
    """LanceDB vector store with hybrid search and importance ranking.

    Args:
        uri: LanceDB URI (directory path)
        table_name: Name of the LanceDB table
        embed_func: Callable that takes text and returns a list of floats
        embed_dim: Dimension of embedding vectors (default 384 for all-MiniLM-L6-v2)
    """

    def __init__(
        self,
        uri: str | Path,
        table_name: str = "novi_memories",
        embed_func: Optional[Callable[[str], list[float]]] = None,
        embed_dim: int = 384,
        embed_model: Optional[str] = None,
        vector_index: bool = True,
        legacy_table_names: tuple[str, ...] = (),
    ):
        self.uri = str(uri)
        self.table_name = table_name
        self.embed_func = embed_func or self._default_embed
        self.embed_dim = embed_dim
        self.embed_model = embed_model
        self.vector_index = vector_index
        self._index_checked = False
        self._db = lancedb.connect(self.uri)
        for legacy_name in legacy_table_names:
            names = set(self._db.table_names())
            if legacy_name in names and table_name not in names:
                try:
                    self._db.rename_table(legacy_name, table_name)
                    log.info(
                        "migrated legacy table '%s' -> '%s' in %s",
                        legacy_name, table_name, self.uri,
                    )
                except Exception as exc:
                    log.warning(
                        "could not rename legacy table '%s' -> '%s': %s",
                        legacy_name, table_name, exc,
                    )
        self._table = None
        self._open_or_create()

    def _default_embed(self, text: str) -> list[float]:
        """Minimal fallback: returns a zero vector.
        Real usage should pass sentence_transformers or OllamaEmbeddings.
        """
        return [0.0] * self.embed_dim

    def _open_or_create(self):
        """Open existing table or create a new one with the right schema."""
        existed = True
        try:
            self._table = self._db.open_table(self.table_name)
            stored = self._stored_vector_dim()
            if stored is not None and stored != self.embed_dim:
                self._rebuild_for_dimension(stored)
                existed = False
        except Exception:
            existed = False
            schema = pa.schema([
                pa.field("id", pa.string()),
                pa.field("text", pa.string()),
                pa.field("metadata", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), self.embed_dim)),
            ])
            self._table = self._db.create_table(self.table_name, schema=schema)
        if existed:
            self._check_embedding_model()
            self._maybe_create_index()

    def _stored_vector_dim(self) -> Optional[int]:
        """Vector column width of the open table, or None when unreadable."""
        try:
            return getattr(self._table.schema.field("vector").type,
                           "list_size", None)
        except Exception:
            return None

    def _rebuild_for_dimension(self, stored: int):
        """Recreate the table when its vectors don't fit the current embedder.

        Vectors from a different embedding space are unusable for search and
        every write fails with an Arrow cast error, so the table must be
        rebuilt at the configured dimension. Non-empty tables are first copied
        to a timestamped backup — never silently destroyed. Callers owning a
        source of truth (e.g. knowledge markdown) re-index naturally; derived
        stores simply repopulate over time.
        """
        backup = None
        try:
            count = self._table.count_rows()
            if count:
                stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
                backup = f"{self.table_name}_old_dim{stored}_{stamp}"
                self._db.create_table(backup, data=self._table.to_arrow())
                log.warning(
                    "Table '%s' holds %d vector(s) at dim %d but the "
                    "embedding dimension is now %d. Old rows moved to '%s'; "
                    "re-index to repopulate.",
                    self.table_name, count, stored, self.embed_dim, backup,
                )
            else:
                log.info(
                    "Table '%s' empty at stale dim %d — recreating at %d",
                    self.table_name, stored, self.embed_dim,
                )
        except Exception:
            log.warning("dimension migration preflight failed for %s",
                        self.table_name, exc_info=True)
        try:
            self._db.drop_table(self.table_name)
        except Exception:
            pass
        schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("text", pa.string()),
            pa.field("metadata", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), self.embed_dim)),
        ])
        self._table = self._db.create_table(self.table_name, schema=schema)

    def add_texts(
        self,
        texts: list[str],
        metadatas: Optional[list[dict]] = None,
        ids: Optional[list[str]] = None,
    ):
        """Embed and insert texts with metadata.

        Args:
            texts: List of texts to embed and store.
            metadatas: Optional per-text metadata dicts.
            ids: Optional deterministic ids (e.g. path-derived chunk ids).
                 Falls back to uuid4 when omitted.
        """
        if not texts:
            return
        metadatas = metadatas or [{}] * len(texts)
        if ids is not None and len(ids) != len(texts):
            raise ValueError("ids length must match texts length")
        vectors = [self.embed_func(t) for t in texts]
        data = []
        for i, (text, meta, vec) in enumerate(zip(texts, metadatas, vectors)):
            data.append({
                "id": ids[i] if ids is not None else str(uuid4()),
                "text": text,
                "metadata": json.dumps(meta),
                "vector": vec,
            })
        self._table.add(data)
        self._maybe_create_index()

    def _check_embedding_model(self):
        """Warn if stored vectors were produced by a different embedding model.

        Stored rows carry their embedding model name in metadata when written
        by an embedding-aware caller. On open, sample recent rows and log a
        warning when the stored model no longer matches the configured one.
        """
        if not self.embed_model:
            return
        try:
            rows = self._table.search().limit(100).to_list()
        except Exception:
            return
        for r in rows:
            try:
                meta = json.loads(r.get("metadata", "{}"))
            except (TypeError, ValueError):
                continue
            stored = meta.get("embed_model")
            if stored and stored != self.embed_model:
                log.warning(
                    "Table '%s' contains vectors from embed model '%s' but "
                    "config now uses '%s' — similarity results may be degraded. "
                    "Re-index the store to migrate.",
                    self.table_name, stored, self.embed_model,
                )
                return

    def _maybe_create_index(self):
        """Create a vector index once the table is large enough.

        Guarded by:
          - vector_index flag (caller reads config)
          - existing index (never re-index)
          - row-count threshold (brute force is fine for small tables)
          - try/except (index creation must never break writes)
        """
        if not self.vector_index or self._index_checked:
            return
        self._index_checked = True
        try:
            indices = self._table.list_indices()
            if indices:
                return
        except Exception:
            return
        try:
            count = self._table.count_rows()
        except Exception:
            return
        if count < 1000:
            return
        try:
            from lancedb.index import IvfPq
            num_partitions = max(4, min(64, count // 256))
            self._table.create_index(
                "vector",
                config=IvfPq(distance_type="cosine", num_partitions=num_partitions),
            )
            log.info("Created IVF_PQ vector index on %s (%d rows)", self.table_name, count)
        except Exception as e:
            log.warning("vector index creation skipped for %s: %s", self.table_name, e)

    def similarity_search(
        self,
        query: str,
        k: int = 5,
        distance_threshold: Optional[float] = None,
        filters: Optional[str] = None,
    ) -> list[dict]:
        """Vector similarity search. Returns results sorted by distance (ascending)."""
        query_vec = self.embed_func(query)
        try:
            q = self._table.search(query_vec).metric("cosine").limit(k * 2)
            if filters:
                q = q.where(filters)
            results = q.to_list()
        except Exception as e:
            log.warning("lance similarity search failed: %s", e)
            return []

        items = []
        for r in results:
            meta = json.loads(r.get("metadata", "{}"))
            dist = r.get("_distance", 1.0)
            if distance_threshold is not None and dist > distance_threshold:
                continue
            items.append({
                "id": r.get("id", ""),
                "text": r.get("text", ""),
                "metadata": meta,
                "distance": dist,
            })
        return items[:k]

    def hybrid_search(
        self,
        query: str,
        k: int = 5,
        distance_threshold: Optional[float] = None,
    ) -> list[dict]:
        """Hybrid search: vector similarity + full-text search (LanceDB FTS).

        Falls back to vector-only if FTS is unavailable.
        """
        vec_results = self.similarity_search(query, k=k * 3, distance_threshold=distance_threshold)
        if not vec_results:
            return []

        keywords = set(query.lower().split())
        for item in vec_results:
            text_lower = item["text"].lower()
            keyword_hits = sum(1 for kw in keywords if kw in text_lower)
            boost = (keyword_hits / max(len(keywords), 1)) * 0.3
            item["distance"] = max(0, item["distance"] - boost)

        vec_results.sort(key=lambda x: x["distance"])
        return vec_results[:k]

    def search_with_importance(
        self,
        query: str,
        k: int = 5,
        distance_threshold: Optional[float] = None,
    ) -> list[dict]:
        """Search with importance scoring: relevance × recency × frequency."""
        results = self.hybrid_search(query, k=k * 2, distance_threshold=distance_threshold)
        if not results:
            return []

        now = datetime.now()
        for item in results:
            meta = item["metadata"]
            score = 1.0 - item["distance"]

            ts = meta.get("timestamp", "")
            if ts:
                try:
                    mem_time = datetime.fromisoformat(ts)
                    days_old = (now - mem_time).days
                    recency = max(0, 1.0 - (days_old / 30.0))
                    score = score * 0.7 + recency * 0.3
                except ValueError:
                    pass

            freq = meta.get("frequency", 1)
            if freq > 1:
                freq_boost = min(0.15, (freq - 1) * 0.02)
                score += freq_boost

            item["score"] = round(score, 4)

        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results[:k]

    def increment_frequency(self, id_: str):
        """Increment frequency counter for a memory (boost importance)."""
        try:
            table = self._table
            result = table.search().where(f"id = '{id_}'").limit(1).to_list()
            if not result:
                return
            meta = json.loads(result[0].get("metadata", "{}"))
            freq = meta.get("frequency", 1) + 1
            meta["frequency"] = freq
            table.update(where=f"id = '{id_}'", values={"metadata": json.dumps(meta)})
        except Exception as e:
            log.warning("increment_frequency failed: %s", e)

    def count(self) -> int:
        try:
            return self._table.count_rows()
        except Exception:
            return 0

    def list_all(self, limit: int = 100) -> list[dict]:
        try:
            data = self._table.search().limit(limit).to_list()
            return [
                {
                    "id": r.get("id", ""),
                    "text": r.get("text", ""),
                    "metadata": json.loads(r.get("metadata", "{}")),
                }
                for r in data
            ]
        except Exception:
            return []

    def delete(self, id_: str) -> bool:
        try:
            self._table.delete(f"id = '{id_}'")
            return True
        except Exception:
            return False

    def query_sql(self, sql: str) -> list[dict]:
        """Execute a raw SQL query for structured filtering (e.g. WHERE type = 'preference')."""
        try:
            return self._table.search().where(sql).to_list()
        except Exception as e:
            log.warning("SQL query failed: %s", e)
            return []
