"""Knowledge layer — owns KnowledgeItems and their store.

Persistence only: turns extracted claims into KnowledgeItems (ids, provenance
edges via ``sources``, scenario ownership via ``scenario_id``) and writes them.
No reasoning, no other layers.

Repeated equivalent claims retain independent conversation sources on one
canonical item. Replay within a conversation cannot inflate corroboration.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4, uuid5, NAMESPACE_URL

from ..reasoning import verification
from ..reasoning.extraction import ExtractionResult
from ..storage.vector_store import VectorStore
from ..types import KnowledgeForm, KnowledgeHit, KnowledgeItem, KnowledgeStatus

_SCENARIO_SUMMARY_CONFIDENCE = 0.8

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

        Equivalent claims retain sources and return the canonical id. Opposing
        or differently attributed claims are distinct. IDs make retries safe.
        """
        corpus = self.list_objects(limit=None)
        items: list[KnowledgeItem] = []
        ids: list[str] = []
        for claim in result.claims:
            actor = getattr(claim, 'speaker', 'user')
            evidence_source = conversation_id if actor == 'user' else f'{actor}:{conversation_id}'
            eligible = [i for i in corpus if
                        ('assistant_observation' in i.tags) == (actor == 'assistant') and
                        ('tool_observation' in i.tags) == (actor == 'tool')]
            match = verification.find_near_duplicate(eligible, claim.statement)
            if match is not None:
                sources = tuple(dict.fromkeys((*match.sources, evidence_source)))
                match.sources = sources
                match.last_seen_at = datetime.now()
                self._store.update_observation(match.id, sources, match.last_seen_at)
                ids.append(match.id)
                continue
            item = KnowledgeItem(
                id=f"kn-{uuid5(NAMESPACE_URL, evidence_source + ':' + verification.canonical_claim(claim.statement)).hex}",
                form=KnowledgeForm.ATOMIC,
                content=claim.statement,
                confidence=claim.confidence,
                status=KnowledgeStatus.CANDIDATE,
                tags=claim.tags + ((f'{actor}_observation',) if actor != 'user' else ()),
                sources=(evidence_source,),
                scenario_id=scenario_id,
            )
            items.append(item)
            corpus.append(item)
        if result.summary:
            items.append(
                KnowledgeItem(
                    id=f"kn-{uuid5(NAMESPACE_URL, conversation_id + ':summary:' + result.summary).hex}",
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

    def list_objects(self, limit: int | None = 200) -> list[KnowledgeItem]:
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
        item_id: str | None = None,
    ) -> str:
        """Explicit knowledge acquisition (Brain.learn).

        Persists a verified atomic item directly — immediately discoverable by
        the resolver/retrieval, closing the legacy stale-index gap.
        """
        item = KnowledgeItem(
            id=item_id or f"kn-{uuid4().hex[:12]}",
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
