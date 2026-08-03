"""Promotion — candidate → corroborated → verified, supersede-with-history.

Phase F. Pure lifecycle decisions over KnowledgeItem objects. Promotion never
reads or writes storage: the Brain supplies corroboration counts and the
current verified item (when one exists) via ``decide`` and applies the
resulting status/edge itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..types import EdgeKind, KnowledgeItem, KnowledgeStatus, Relationship


@dataclass(frozen=True)
class PromotionOutcome:
    """Decision for a single item.

    ``new_status`` is the status to write (equal to the current one when
    unchanged). ``supersedes`` is the edge to add when a newly verified claim
    replaces an older verified item (old → new, with the old marked
    ``superseded``).
    """

    item: KnowledgeItem
    new_status: KnowledgeStatus
    supersedes: Optional[Relationship] = None


# Corroboration count required to verify without an explicit confirmation.
_VERIFY_CORROBORATIONS = 3


def decide(
    item: KnowledgeItem,
    corroborations: int,
    confirmed: bool = False,
    existing_verified: Optional[KnowledgeItem] = None,
    relationship_kind: EdgeKind = EdgeKind.SUPERSEDES,
) -> PromotionOutcome:
    """Compute the promoted status (and any supersession) for one item.

    Rules:
      - explicit confirmation, or >= 2 corroborations → ``verified``
      - any corroboration → ``corroborated``
      - otherwise the item keeps its current status (usually ``candidate``)
      - when an item reaches ``verified`` and replaces an older verified claim
        with different content, the old item is marked ``superseded`` and a
        ``supersedes`` edge is written from the new item to the old one.
    """
    current = item.status
    if confirmed or corroborations >= _VERIFY_CORROBORATIONS:
        new_status = KnowledgeStatus.VERIFIED
    elif corroborations >= 1:
        new_status = KnowledgeStatus.CORROBORATED
    else:
        new_status = current

    supersedes: Optional[Relationship] = None
    if (
        new_status == KnowledgeStatus.VERIFIED
        and existing_verified is not None
        and existing_verified.id != item.id
        and existing_verified.status != KnowledgeStatus.SUPERSEDED
    ):
        substantive_change = _tokens(existing_verified.content) != _tokens(item.content)
        if substantive_change:
            supersedes = Relationship(
                source_id=item.id,
                target_id=existing_verified.id,
                kind=relationship_kind,
            )

    return PromotionOutcome(item=item, new_status=new_status, supersedes=supersedes)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", (text or "").lower()))