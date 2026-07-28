"""Comprehensive evaluation of orchestrator decision quality.

Tests intent detection, evidence detection, and capability resolution
across diverse scenarios. Reports accuracy, false positives, false negatives.
"""

from dataclasses import dataclass, field
from typing import Optional

from cozmo.orchestrator import Orchestrator
from cozmo.capabilities import CapabilityRegistry
from cozmo.capabilities.builtin import register_builtin_capabilities

# ── Scenario definition ──────────────────────────────────────────────────


@dataclass
class Scenario:
    query: str
    expected_intent: str
    expected_external: bool = False
    expected_project: bool = False
    expected_memory: bool = False
    expected_capabilities: Optional[list[str]] = None
    expected_strategy: str = "respond"
    tags: list[str] = field(default_factory=list)


SCENARIOS: list[Scenario] = [
    # ── Category: Timeless knowledge ────────────────────────────────────
    Scenario(
        "Explain binary search",
        "conversation",
        tags=["timeless"],
    ),
    Scenario(
        "How does TCP work?",
        "conversation",
        tags=["timeless"],
    ),
    Scenario(
        "What is dependency injection?",
        "conversation",
        tags=["timeless"],
    ),
    Scenario(
        "Explain transformers in machine learning",
        "conversation",
        tags=["timeless"],
    ),
    Scenario(
        "What is a monad?",
        "conversation",
        tags=["timeless"],
    ),
    Scenario(
        "How do I write a for loop in Python?",
        "conversation",
        tags=["timeless"],
    ),

    # ── Category: Current events / external info ────────────────────────
    Scenario(
        "What is the best PVE loadout in Shindo Life?",
        "conversation",
        expected_external=True,
        expected_capabilities=["search", "conversation"],
        tags=["current", "gaming"],
    ),
    Scenario(
        "Latest Ollama release?",
        "conversation",
        expected_external=True,
        expected_capabilities=["search", "conversation"],
        tags=["current", "software"],
    ),
    Scenario(
        "Should I buy an RTX 5090?",
        "conversation",
        expected_external=True,
        expected_capabilities=["search", "conversation"],
        tags=["current", "hardware"],
    ),
    Scenario(
        "Best local AI models in 2026?",
        "conversation",
        expected_external=True,
        expected_capabilities=["search", "conversation"],
        tags=["current", "ai"],
    ),
    Scenario(
        "Who won the Super Bowl?",
        "research",
        expected_external=True,
        expected_strategy="research",
        expected_capabilities=["search", "research", "conversation"],
        tags=["current", "sports"],
    ),
    Scenario(
        "What is the current weather in Tokyo?",
        "research",
        expected_external=True,
        expected_strategy="research",
        expected_capabilities=["search", "research", "conversation"],
        tags=["current", "weather"],
    ),
    Scenario(
        "What is the price of Apple stock?",
        "research",
        expected_external=True,
        expected_strategy="research",
        expected_capabilities=["search", "research", "conversation"],
        tags=["current", "finance"],
    ),

    # ── Category: Product recommendations ──────────────────────────────
    Scenario(
        "Best programming language for a startup in 2026?",
        "conversation",
        expected_external=True,
        expected_capabilities=["search", "conversation"],
        tags=["recommendation"],
    ),
    Scenario(
        "Is RTX 5090 worth buying?",
        "conversation",
        expected_external=True,
        expected_capabilities=["search", "conversation"],
        tags=["recommendation", "hardware"],
    ),
    Scenario(
        "Best GPU tier list 2026",
        "conversation",
        expected_external=True,
        expected_capabilities=["search", "conversation"],
        tags=["recommendation"],
    ),

    # ── Category: Coding requests ──────────────────────────────────────
    Scenario(
        "Write a Python function to sort a list",
        "coding",
        expected_project=True,
        expected_capabilities=["filesystem", "terminal", "coding"],
        expected_strategy="execute",
        tags=["coding"],
    ),
    Scenario(
        "Fix auth.py",
        "coding",
        expected_project=True,
        expected_capabilities=["filesystem", "terminal", "coding"],
        expected_strategy="execute",
        tags=["coding"],
    ),
    Scenario(
        "Refactor the backend API",
        "coding",
        expected_project=True,
        expected_capabilities=["filesystem", "terminal", "coding"],
        expected_strategy="execute",
        tags=["coding"],
    ),
    Scenario(
        "Add a new feature to the frontend",
        "coding",
        expected_project=True,
        expected_capabilities=["filesystem", "terminal", "coding"],
        expected_strategy="execute",
        tags=["coding"],
    ),

    # ── Category: Coding + latest documentation ─────────────────────────
    Scenario(
        "Fix the bug in FastAPI using latest docs",
        "coding",
        expected_external=True,
        expected_project=True,
        expected_capabilities=["filesystem", "terminal", "coding", "search", "conversation"],
        expected_strategy="execute",
        tags=["coding", "current"],
    ),
    Scenario(
        "Implement React hooks using current best practices",
        "coding",
        expected_external=True,
        expected_project=True,
        expected_capabilities=["filesystem", "terminal", "coding", "search", "conversation"],
        expected_strategy="execute",
        tags=["coding", "current"],
    ),

    # ── Category: Memory retrieval ──────────────────────────────────────
    Scenario(
        "What did we decide yesterday?",
        "conversation",
        expected_memory=True,
        expected_capabilities=["memory", "conversation"],
        tags=["memory"],
    ),
    Scenario(
        "Continue our previous discussion",
        "conversation",
        expected_memory=True,
        expected_capabilities=["memory", "conversation"],
        tags=["memory"],
    ),
    Scenario(
        "Do you remember what I asked earlier?",
        "conversation",
        expected_memory=True,
        expected_capabilities=["memory", "conversation"],
        tags=["memory"],
    ),

    # ── Category: Planning ─────────────────────────────────────────────
    Scenario(
        "Plan the architecture for a new feature",
        "conversation",
        expected_capabilities=None,  # planning or conversation based on plan_level
        tags=["planning"],
    ),
    Scenario(
        "Design a roadmap for Q3",
        "conversation",
        expected_capabilities=None,
        tags=["planning"],
    ),

    # ── Category: Edge cases ───────────────────────────────────────────
    Scenario(
        "Hello, how are you?",
        "conversation",
        tags=["edge"],
    ),
    Scenario(
        "What time is it?",
        "conversation",
        tags=["edge"],
    ),
    Scenario(
        "",
        "conversation",
        tags=["edge"],
    ),
    Scenario(
        "42",
        "conversation",
        tags=["edge"],
    ),

    # ── Category: Ambiguous / over-triggering risk ─────────────────────
    Scenario(
        "What is the best programming language?",
        "conversation",
        expected_external=False,  # timeless topic, no temporal/dynamic context
        tags=["ambiguous"],
    ),
    Scenario(
        "Best restaurant near me",
        "conversation",
        expected_external=True,
        expected_capabilities=["search", "conversation"],
        tags=["ambiguous"],
    ),
]


# ── Evaluation ────────────────────────────────────────────────────────────


def evaluate():
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    orchestrator = Orchestrator(capability_registry=registry)

    results = {
        "total": 0,
        "intent_correct": 0,
        "strategy_correct": 0,
        "capability_exact": 0,
        "capability_superset": 0,
        "scored": 0,
    }

    print("=" * 80)
    print("Orchestrator Decision Quality Evaluation")
    print("=" * 80)

    for s in SCENARIOS:
        if not s.query:
            continue

        results["total"] += 1
        plan = orchestrator.plan(s.query)
        analysis = plan.context.get("analysis")
        caps = [c.id for c in plan.capabilities]
        sigs = [f"{sig.type}({sig.strength})" for sig in analysis.evidence.signals] if analysis else []

        # Intent accuracy
        intent_ok = plan.goal.intent.value == s.expected_intent
        if intent_ok:
            results["intent_correct"] += 1

        # Strategy accuracy
        strat_ok = plan.strategy.value == s.expected_strategy
        if strat_ok:
            results["strategy_correct"] += 1

        # Capability check — test whether expected caps are a subset of actual caps
        caps_ok = True
        if s.expected_capabilities is not None:
            results["scored"] += 1
            missing = [c for c in s.expected_capabilities if c not in caps]
            extra = [c for c in caps if s.expected_capabilities and c not in s.expected_capabilities]
            caps_ok = len(missing) == 0
            if caps_ok and len(extra) == 0:
                results["capability_exact"] += 1
            if caps_ok:
                results["capability_superset"] += 1

        # Check evidence signals for reporting
        evidence_signals_str = ", ".join(sigs) if sigs else "(none)"

        # Overall pass/fail
        all_ok = intent_ok and strat_ok and caps_ok

        status = "PASS" if all_ok else "FAIL"
        tags = ",".join(s.tags)
        print(f"\n{status} [{tags:20s}] {s.query[:60]:<60s}")
        print(f"      Intent={plan.goal.intent.value} Strategy={plan.strategy.value} Caps={str(caps)}")
        if not all_ok:
            if not intent_ok:
                print(f"      X Intent: expected={s.expected_intent} got={plan.goal.intent.value}")
            if not strat_ok:
                print(f"      X Strategy: expected={s.expected_strategy} got={plan.strategy.value}")
            if not caps_ok and s.expected_capabilities:
                print(f"      X Caps: expected={s.expected_capabilities} got={caps}")

    # ── Summary ────────────────────────────────────────────────────────
    n = results["total"]
    scored = results["scored"]
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Scenarios: {n}")
    print(f"Intent accuracy:       {results['intent_correct']}/{n} ({results['intent_correct']/n*100:.1f}%)")
    print(f"Strategy accuracy:     {results['strategy_correct']}/{n} ({results['strategy_correct']/n*100:.1f}%)")
    if scored:
        print(f"Capability exact:      {results['capability_exact']}/{scored} ({results['capability_exact']/scored*100:.1f}%)")
        print(f"Capability superset:   {results['capability_superset']}/{scored} ({results['capability_superset']/scored*100:.1f}%)")

    return results


if __name__ == "__main__":
    evaluate()
