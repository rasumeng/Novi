"""Weak, name-based capability inference — isolated and NON-authoritative.

This module is the ONLY place production code may inspect model names to
infer capabilities. It exists solely to give *advisory* hints to discovery
and recommendations, so that an unknown model still appears meaningfully in
the UI.

Hard rules:

* Every claim produced here is tagged ``source="name-inference"`` with a low
  confidence — it is the weakest evidence tier.
* It is NEVER used for authoritative runtime validation
  (``runtime/model_selector.py`` must not call it).
* It NEVER fabricates a definitive capability answer; a negative is just
  "no heuristic matched".
"""

from __future__ import annotations

from typing import Optional

from .model_records import CapabilityEvidence

_INFERENCE_HINTS: list[tuple[str, str, float]] = [
    # (name substring, inferred capability, confidence)
    ("llava", "vision", 0.8),
    ("minicpm", "vision", 0.8),
    ("qwen2-vl", "vision", 0.8),
    ("-vl", "vision", 0.7),
    ("coder", "coding", 0.7),
    ("codegemma", "coding", 0.7),
    ("deepseek-coder", "coding", 0.7),
    ("moondream", "vision", 0.8),
]


def infer_capabilities_from_name(name: str) -> list[CapabilityEvidence]:
    """Return weak capability claims inferred from a model name.

    Returns an empty list when no heuristic matches. Each claim is tagged
    ``name-inference`` and is advisory only — never authoritative.
    """
    lowered = (name or "").lower()
    evidence: list[CapabilityEvidence] = []
    for token, capability, confidence in _INFERENCE_HINTS:
        if token in lowered:
            evidence.append(
                CapabilityEvidence(
                    capability=capability,
                    supported=True,
                    source="name-inference",
                    confidence=confidence,
                    note=f"weak heuristic from model name '{token}'",
                )
            )
    return evidence


def name_hints(name: str) -> dict[str, bool]:
    """Flat advisory view of name-inference claims (``{capability: True}``)."""
    return {ev.capability: True for ev in infer_capabilities_from_name(name)}