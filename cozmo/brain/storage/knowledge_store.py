"""KnowledgeStore — durable KnowledgeItems (LanceDB vectors).

Storage is an implementation detail of the knowledge layer. Phase C reuses the
flat LanceStore schema (Phase D migrates to typed columns); metadata carries
knowledge fields as JSON. No search-time filters beyond vector similarity and
soft tag overlap — no `metadata LIKE` (Phase D removes it entirely).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional
from uuid import uuid4

from ...memory.lancedb_store import LanceStore
from ...services.embedding import EmbeddingService
from ..types import KnowledgeForm, KnowledgeItem, KnowledgeStatus

log = logging.getLogger("cozmo.brain.storage.knowledge")

_TABLE_NAME = "cozmo_knowledge"


class KnowledgeStore:
    """LanceDB-backed knowledge item store."""

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
            from ... import config as cozmo_config

            cfg = cozmo_config.load()
            model_name = embed_model or cfg.get("embedding", {}).get("model", "all-MiniLM-L6-v2")
            embed_service = EmbeddingService({"embedding": {"model": model_name}})

        def embed(text: str) -> list[float]:
            return embed_service.encode(text, normalize=True)

        self._embedder = embed_service
        self._store = LanceStore(
            uri=Path(persist_dir) / "lancedb",
            table_name=table_name,
            embed_func=embed,
            embed_dim=embed_service.dimension,
            embed_model=embed_service.model_name,
            vector_index=vector_index,
        )

    def add(self, item: KnowledgeItem) -> str:
        return self.add_many([item])[0]

    def add_many(self, items: list[KnowledgeItem]) -> list[str]:
        if not items:
            return []
        ids = []
        texts = []
        metas = []
        for item in items:
            kid = item.id or f"kn-{uuid4().hex[:12]}"
            ids.append(kid)
            texts.append(item.content)
            metas.append(self._meta(item, kid))
        self._store.add_texts(texts, metas, ids=ids)
        return ids

    def query(
        self,
        text: str,
        k: int = 5,
        distance_threshold: Optional[float] = 0.5,
        tags: Optional[tuple[str, ...] | list[str]] = None,
        forms: Optional[tuple[KnowledgeForm, ...] | list[KnowledgeForm]] = None,
    ) -> list[dict]:
        """Vector search over knowledge items. Soft post-filters only."""
        results = self._store.similarity_search(
            text, k=k * 3, distance_threshold=distance_threshold
        )
        if not results:
            return []
        wanted_tags = set(tags or ())
        wanted_forms = {f.value for f in (forms or ())}
        out = []
        for r in results:
            meta = r.get("metadata", {})
            if wanted_forms and meta.get("form") not in wanted_forms:
                continue
            if wanted_tags:
                item_tags = set(meta.get("tags", ()))
                if not wanted_tags & item_tags:
                    continue
            score = 1.0 - r.get("distance", 1.0)
            out.append(
                {
                    "id": r.get("id", ""),
                    "text": r.get("text", ""),
                    "metadata": meta,
                    "distance": r.get("distance", 1.0),
                    "score": score,
                }
            )
        return out[:k]

    def get(self, item_id: str) -> Optional[dict]:
        rows = self._store.similarity_search(item_id, k=1)
        for r in rows:
            if r.get("id") == item_id:
                return r
        return None

    def delete(self, item_id: str) -> bool:
        return self._store.delete(item_id)

    def count(self) -> int:
        return self._store.count()

    def list_all(self, limit: int = 100) -> list[dict]:
        return self._store.list_all(limit=limit)

    def close(self) -> None:
        try:
            self._store.close()
        except AttributeError:
            pass

    # ── metadata mapping ────────────────────────────────────────────────

    def _meta(self, item: KnowledgeItem, kid: str) -> dict:
        return {
            "kind": "knowledge",
            "form": item.form.value,
            "status": item.status.value,
            "confidence": float(item.confidence),
            "tags": list(item.tags),
            "sources": list(item.sources),
            "scenario_id": item.scenario_id,
            "created_at": item.created_at.isoformat(),
            "embed_model": self._store.embed_model,
        }

    @classmethod
    def item_from_row(cls, row: dict) -> KnowledgeItem:
        meta = row.get("metadata", {})
        created = meta.get("created_at")
        from datetime import datetime

        return KnowledgeItem(
            id=row.get("id", ""),
            form=KnowledgeForm(meta.get("form", KnowledgeForm.ATOMIC.value)),
            content=row.get("text", ""),
            confidence=float(meta.get("confidence", 0.0)),
            status=KnowledgeStatus(meta.get("status", KnowledgeStatus.CANDIDATE.value)),
            tags=tuple(meta.get("tags", ())),
            sources=tuple(meta.get("sources", ())),
            scenario_id=meta.get("scenario_id"),
            created_at=datetime.fromisoformat(created) if created else None,
        )
