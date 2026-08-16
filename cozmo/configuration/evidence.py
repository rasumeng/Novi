"""Capability evidence assembly with provenance (Phase 5D).

Merges capability claims from the three evidence tiers into one
provenance-rich list, first-set wins:

1. **runtime**       — measured/reported by the model runtime (e.g. Ollama
                       ``/api/show`` capability tokens or tags details). Most
                       reliable for what the runtime itself knows.
2. **seed**          — curated non-authoritative facts (``model_seeds.py``).
3. **name-inference**— weak heuristic claims from the model name, advisory
                       only and always lowest confidence.

Tier order is: runtime > seed > name-inference. A capability already claimed
by a higher tier is not overwritten by a lower one.
"""

from __future__ import annotations

from typing import Iterable, Optional

from .model_records import CapabilityEvidence
from .model_seeds import ModelFact


def _evidence(known: dict[str, CapabilityEvidence],
              capability: str,
              supported: bool,
              source: str,
              confidence: float,
              note: str = "") -> None:
    """First-set-wins merge of a single claim."""
    existing = known.get(capability)
    if existing is not None:
        return
    known[capability] = CapabilityEvidence(
        capability=capability,
        supported=supported,
        source=source,
        confidence=confidence,
        note=note,
    )


def assemble_capability_evidence(
    *,
    fact: Optional[ModelFact] = None,
    runtime_capabilities: Optional[Iterable[str]] = None,
    name: str = "",
    inference_hints: Optional[Iterable[CapabilityEvidence]] = None,
) -> list[CapabilityEvidence]:
    """Merge capability claims from all evidence tiers (first-set wins).

    ``runtime_capabilities`` is the set of capability names reported by the
    runtime. ``inference_hints`` is the output of ``name_inference`` (or
    ``None`` to skip name inference entirely).
    """
    known: dict[str, CapabilityEvidence] = {}

    # 1. Runtime-reported (strongest for runtime behaviour).
    for cap in runtime_capabilities or ():
        if cap:
            _evidence(known, cap, True, "runtime", 0.95,
                      "reported by the model runtime")

    # 2. Curated seed facts (advisory).
    if fact is not None:
        for cap in fact.capabilities:
            _evidence(known, cap, True, "seed", 0.9,
                      "curated seed metadata (non-authoritative)")

    # 3. Weak name inference (advisory only, lowest confidence).
    for hint in inference_hints or ():
        _evidence(known, hint.capability, hint.supported, hint.source,
                  hint.confidence, hint.note)

    return list(known.values())


def capability_flags(evidence: Iterable[CapabilityEvidence]) -> dict[str, bool]:
    """Flat ``{capability: bool}`` view of an evidence list."""
    flags: dict[str, bool] = {}
    for ev in evidence:
        if ev.supported is not None:
            flags[ev.capability] = ev.supported
    return flags