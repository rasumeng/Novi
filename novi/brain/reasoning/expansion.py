"""Bounded WikiLink-neighborhood traversal (M4).

Shared expansion core for retrieval: given seed durable knowledge ids, walk
``references`` (outgoing) and backlinks (incoming) edges through an injected
``neighborhood`` callable and return the discovered neighbors.

Traversal contract (spec M4 §4):

- **bounded**      — at most ``depth`` hops and at most ``max_neighbors``
  discovered items; BFS order, first-discovered wins.
- **deterministic** — seeds keep caller order (deduplicated); each node's
  references and backlinks are visited in sorted id order, so the same graph
  always yields the same neighbor sequence.
- **cycle-safe**   — a ``visited`` set makes A→B→A terminate immediately.
- **dangling-safe**— unresolved ``note:<Title>`` targets (M2 convention) are
  skipped here: they have no durable Brain identity to fetch.
- **pure**         — no storage imports; edges arrive via the injected
  callable (``Brain.neighborhood`` shape: ``{"references": (...),
  "backlinks": (...)}``). Callable failures degrade to "no neighbors" from
  that node, never an exception into retrieval.

This module discovers *identities only*. Resolving identities to content and
assigning presentation scores belong to the callers (resolver / source).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

log = logging.getLogger("novi.brain.reasoning.expansion")

# Unresolved/dangling WikiLink edge target form (M2 convention,
# novi.brain.wikilinks._NOTE_PREFIX). Duplicated as a literal so this module
# stays import-pure for the reasoning tier.
DANGLING_PREFIX = "note:"


@dataclass(frozen=True)
class ExpansionConfig:
    """Bounds for one neighborhood expansion.

    ``depth``         — maximum hops from a seed (1 = direct neighbors only).
    ``max_neighbors`` — hard cap on distinct discovered neighbors per call.
    ``hop_decay``     — presentation-score multiplier per hop; callers apply
    it as ``seed_score * hop_decay ** hops``. Provenance signal only, not a
    ranking redesign.
    """

    depth: int = 1
    max_neighbors: int = 8
    hop_decay: float = 0.5


@dataclass(frozen=True)
class GraphNeighbor:
    """One discovered neighbor identity.

    ``parent_id`` is the node whose edges produced this discovery (score
    inheritance anchor); ``hops`` is the BFS distance from the nearest seed
    (1-based). ``via`` is "reference" (outgoing) or "backlink" (incoming).
    """

    item_id: str
    hops: int
    parent_id: str
    via: str


def is_dangling(item_id: str) -> bool:
    """True when ``item_id`` is an unresolved ``note:<Title>`` edge target."""
    return str(item_id).startswith(DANGLING_PREFIX)


def traverse(
    seeds: Sequence[str],
    neighborhood: Callable[[str], dict],
    *,
    config: ExpansionConfig = ExpansionConfig(),
) -> list[GraphNeighbor]:
    """Bounded BFS over ``references``/backlinks from ``seeds``.

    Returns newly discovered neighbor identities in deterministic discovery
    order. Seeds themselves are never returned. ``neighborhood`` may return
    anything falsy, raise, or contain unknown shapes — every failure degrades
    to "no edges for this node".
    """
    if config.depth < 1 or config.max_neighbors < 1 or not seeds:
        return []

    seen: set[str] = {str(s) for s in seeds}
    frontier: list[str] = []
    for seed in seeds:
        sid = str(seed)
        if sid not in frontier:
            frontier.append(sid)

    out: list[GraphNeighbor] = []
    for depth in range(1, max(1, config.depth) + 1):
        next_frontier: list[str] = []
        for node in frontier:
            refs, backs = _edges(node, neighborhood)
            candidates = [
                *(("reference", t) for t in sorted(refs)),
                *(("backlink", t) for t in sorted(backs)),
            ]
            for via, candidate in candidates:
                cid = str(candidate)
                if not cid or cid in seen or is_dangling(cid):
                    continue
                seen.add(cid)
                neighbor = GraphNeighbor(
                    item_id=cid, hops=depth, parent_id=node, via=via
                )
                out.append(neighbor)
                if len(out) >= config.max_neighbors:
                    return out
                next_frontier.append(cid)
        if not next_frontier:
            break
        frontier = next_frontier
    return out


def decay_score(seed_score: float, hops: int, config: ExpansionConfig) -> float:
    """Presentation score for a graph neighbor: seed score decayed per hop."""
    return max(0.0, float(seed_score)) * (config.hop_decay ** max(0, hops))


def _edges(node: str, neighborhood: Callable[[str], dict]) -> tuple[Iterable[str], Iterable[str]]:
    """Read one node's outgoing references + incoming backlinks, safely."""
    try:
        view = neighborhood(node) or {}
    except Exception:
        log.warning("neighborhood read failed for %s", node, exc_info=True)
        return (), ()
    refs = view.get("references") or ()
    backs = view.get("backlinks") or ()
    return refs, backs
