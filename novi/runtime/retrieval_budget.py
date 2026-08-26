"""Context allocation contract for the Phase 9 unified retrieval layer.

Distinct from the web-tool :class:`RetrievalBudget` in
``novi.runtime.retrieval_coordinator``, which tracks per-call web
search/fetch counts. ``ContextAllocation`` caps how much context a single
``retrieve()`` call may consume from a retrieval plan's budget.

Design (docs/phase9-blueprint.md section 3): resolves the PLAN.md section 5.5
``RetrievalBudget`` naming collision by keeping the web-tool budget on the
coordinator and giving the context budget its own type here.

Consumed by the retrieval policy (``plan.allocation``), recorded in executor
retrieval trace events, and honored by source adapters as the per-source item
cap (``budget.max_results``). Not an executor-side enforcement gate yet.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ContextAllocation:
    """Context budget for one ``retrieve()`` call within a retrieval plan.

    Attributes:
        max_sources: Max distinct sources a single plan may consult.
        max_results: Max items any single source may return.
        max_context_chars: Max characters any single source's items may span.
    """

    max_sources: int = 3
    max_results: int = 8
    max_context_chars: int = 6000
