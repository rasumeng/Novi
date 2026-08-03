"""Knowledge layer — owns KnowledgeItems and their store.

Persistence only: turns extracted claims into KnowledgeItems (ids, provenance
edges via ``sources``, scenario ownership via ``scenario_id``) and writes them.
No reasoning, no other layers.
"""

from __future__ import annotations

from uuid import uuid4

from ..reasoning.extraction import ExtractionResult
from ..storage.knowledge_store import KnowledgeStore
from ..types import KnowledgeForm, KnowledgeItem, KnowledgeStatus

_SCENARIO_SUMMARY_CONFIDENCE = 0.8


class KnowledgeLayer:
    """Domain manager for knowledge items."""

    def __init__(self, store: KnowledgeStore):
        self._store = store

    @property
    def store(self) -> KnowledgeStore:
        return self._store

    def store_extracted(
        self, conversation_id: str, scenario_id: str, result: ExtractionResult
    ) -> list[str]:
        """Persist extracted claims + scenario summary. Returns written ids."""
        items: list[KnowledgeItem] = []
        for claim in result.claims:
            items.append(
                KnowledgeItem(
                    id=f"kn-{uuid4().hex[:12]}",
                    form=KnowledgeForm.ATOMIC,
                    content=claim.statement,
                    confidence=claim.confidence,
                    status=KnowledgeStatus.CANDIDATE,
                    tags=claim.tags,
                    sources=(conversation_id,),
                    scenario_id=scenario_id,
                )
            )
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
        return self._store.add_many(items)

    def query(
        self,
        text: str,
        k: int = 5,
        distance_threshold: float | None = 0.5,
        tags: tuple[str, ...] | list[str] | None = None,
    ) -> list[dict]:
        return self._store.query(
            text, k=k, distance_threshold=distance_threshold, tags=tags
        )
