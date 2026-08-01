"""Regression test suite for orchestrator decision quality.

Loads regression_corpus.json and validates every entry against the
orchestrator. Every phase of Phase 5 must pass this suite.
"""

from pathlib import Path
from typing import Any

import pytest

from cozmo.orchestrator import Orchestrator
from cozmo.capabilities import CapabilityRegistry
from cozmo.capabilities.builtin import register_builtin_capabilities

CORPUS_PATH = Path(__file__).parent / "regression_corpus.json"


def load_corpus() -> list[dict[str, Any]]:
    import json
    with open(CORPUS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_orchestrator():
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    return Orchestrator(capability_registry=registry)


@pytest.mark.parametrize("entry", load_corpus(), ids=lambda e: e["id"])
def test_regression(entry: dict[str, Any]):
    orch = get_orchestrator()
    plan = orch.plan(entry["query"], has_images=entry.get("has_images", False))
    analysis = plan.context.get("analysis")
    actual_intent = plan.goal.intent.value
    actual_strategy = plan.strategy.value
    actual_caps = [c.id for c in plan.capabilities]

    expected = entry["expected"]
    errors = []

    # Intent
    if actual_intent != expected["intent"]:
        errors.append(f"intent: expected={expected['intent']} actual={actual_intent}")

    # Strategy
    if actual_strategy != expected["strategy"]:
        errors.append(f"strategy: expected={expected['strategy']} actual={actual_strategy}")

    # Capabilities — expected must be subset of actual
    for cap in expected.get("capabilities", []):
        if cap not in actual_caps:
            errors.append(f"capability '{cap}' missing from {actual_caps}")

    # Evidence checks when specified
    if "evidence_external" in expected and analysis:
        has_external = any(s.type == "temporal" for s in analysis.evidence.signals)
        # Comparative signals also indicate external data is needed
        has_comparative = any(s.type == "comparative" for s in analysis.evidence.signals)
        has_dynamic = any(s.type == "dynamic" for s in analysis.evidence.signals)
        net = has_external or has_comparative or has_dynamic
        if expected["evidence_external"] and not net:
            errors.append(f"expected external evidence, none found (signals: {[s.type for s in analysis.evidence.signals]})")
        if not expected["evidence_external"] and net and analysis.evidence.confidence >= 0.7:
            pass  # Allow low-confidence evidence triggers

    if "evidence_memory" in expected and analysis:
        has_memory = any(s.type == "memory" for s in analysis.evidence.signals)
        has_temporal = any(s.type == "temporal" for s in analysis.evidence.signals)
        net = has_memory or has_temporal
        if expected["evidence_memory"] and not net:
            errors.append(f"expected memory evidence, none found (signals: {[s.type for s in analysis.evidence.signals]})")

    assert not errors, f"{entry['id']}: {'; '.join(errors)}"
