"""Knowledge layer — owns KnowledgeItems and their store.

Persistence only: turns extracted claims into KnowledgeItems (ids, provenance
edges via ``sources``, scenario ownership via ``scenario_id``) and writes them.
No reasoning, no other layers.

Phase F consolidation: a newly extracted claim that restates an existing
ATOMIC, non-superseded item corroborates it (advances ``last_seen_at``) instead
of inserting a sibling row. Provenance of the re-observation is not appended in
this phase.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from ..reasoning import verification
from ..reasoning.extraction import ExtractionResult
from ..storage.vector_store import VectorStore
from ..types import KnowledgeForm, KnowledgeHit, KnowledgeItem, KnowledgeStatus

_SCENARIO_SUMMARY_CONFIDENCE = 0.8

# Corpus scan cap for cross-corpus consolidation per extraction batch.
_DEDUP_SCAN_LIMIT = 2000


class KnowledgeLayer:
    """Domain manager for knowledge items."""

    def __init__(self, store: VectorStore):
        self._store = store

    @property
    def store(self) -> VectorStore:
        return self._store

    def store_extracted(
        self, conversation_id: str, scenario_id: str, result: ExtractionResult
    ) -> list[str]:
        """Persist extracted claims + scenario summary. Returns written ids.

        Claims that restate an existing ATOMIC, non-superseded item corroborate
        it (advance ``last_seen_at``) and return that item's id instead of
        inserting a sibling. Non-duplicates are written as new items.
        """
        corpus = self.list_objects(limit=_DEDUP_SCAN_LIMIT)
        items: list[KnowledgeItem] = []
        ids: list[str] = []
        for claim in result.claims:
            match = verification.find_near_duplicate(corpus, claim.statement)
            if match is not None and self._store.update_last_seen(
                match.id, datetime.now()
            ):
                ids.append(match.id)
                continue
            item = KnowledgeItem(
                id=f"kn-{uuid4().hex[:12]}",
                form=KnowledgeForm.ATOMIC,
                content=claim.statement,
                confidence=claim.confidence,
                status=KnowledgeStatus.CANDIDATE,
                tags=claim.tags,
                sources=(conversation_id,),
                scenario_id=scenario_id,
            )
            items.append(item)
            corpus.append(item)
        if result.summary:
            items.append(
                KnowledgeItem(
                    id=f"kn-{uuid4().hex[:12]}",
                    form=KnowledgeForm.COMPOSITE,
                    content=result.summary,
                    confidence=_SCENARIO_SUMMARY_CONFIDENCE,
                    status=KnowledgeStatus.CANDIDATE,
                    tags=("conversation", "summary"),
                    sources=(conversation_id,),
                    scenario_id=scenario_id,
                )
            )
        ids.extend(self._store.add_many(items))
        return ids

    def query(
        self,
        text: str,
        k: int = 5,
        distance_threshold: float | None = 0.5,
        tags: tuple[str, ...] | list[str] | None = None,
    ) -> list[KnowledgeHit]:
        """Retrieve scored KnowledgeItem objects for a query."""
        return [
            KnowledgeHit(
                item=self._store.item_from_row(r),
                score=float(r.get("score", 0.0)),
                distance=float(r.get("distance", 1.0)),
            )
            for r in self._store.query(
                text, k=k, distance_threshold=distance_threshold, tags=tags
            )
        ]

    def list_items(self, limit: int = 200) -> list[dict]:
        """All items as flat dicts, for compatibility consumers."""
        return self._store.list_all(limit=limit)

    def list_objects(self, limit: int = 200) -> list[KnowledgeItem]:
        """All items as KnowledgeItem objects (reflection / promotion)."""
        return [self._store.item_from_row(r) for r in self._store.list_all(limit=limit)]

    def update_status(self, item_id: str, status: KnowledgeStatus) -> bool:
        """Promote/demote an item's lifecycle status (Phase F)."""
        return self._store.update_status(item_id, status)

    def write(
        self,
        statement: str,
        tags: tuple[str, ...] | list[str] = (),
        source_kind: str = "explicit",
    ) -> str:
        """Explicit knowledge acquisition (Brain.learn).

        Persists a verified atomic item directly — immediately discoverable by
        the resolver/retrieval, closing the legacy stale-index gap.
        """
        item = KnowledgeItem(
            id=f"kn-{uuid4().hex[:12]}",
            form=KnowledgeForm.ATOMIC,
            content=statement,
            confidence=1.0,
            status=KnowledgeStatus.VERIFIED,
            tags=tuple(tags),
        )
        return self._store.add(item, source_kind=source_kind)

    def query_scoped(
        self,
        text: str,
        *,
        scenario_id: str | None = None,
        k: int = 5,
        distance_threshold: float | None = 0.5,
        forms: tuple | list | None = None,
        tags: tuple[str, ...] | list[str] | None = None,
    ) -> list[KnowledgeHit]:
        """Layer-scoped query: score within the given scenario's neighborhood.

        Without a scenario, skips the ownership predicate and searches the
        whole knowledge graph (used by the resolver's expansion step).
        """
        return [
            KnowledgeHit(
                item=self._store.item_from_row(r),
                score=float(r.get("score", 0.0)),
                distance=float(r.get("distance", 1.0)),
            )
            for r in self._store.query(
                text,
                k=k,
                distance_threshold=distance_threshold,
                scenario_id=scenario_id,
                source_kind=None,
                forms=forms,
                tags=tags,
            )
        ]
