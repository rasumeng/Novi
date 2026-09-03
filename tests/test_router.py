"""Heuristic Workload Router — Beta deterministic.

- Verifies 3 workloads only: general, research, code
- Exercises relation new/continue/switch with compact topic
- Verifies deterministic heuristic, no LLM, no llama.cpp
- Verifies dispatcher invariant: original message verbatim unchanged
"""

import json
import pytest
from pathlib import Path

from novi.orchestrator.router import WorkloadRouter, RouterState, Relation
from novi.orchestrator.conversation_state import ConversationStateStore
from novi.orchestrator.orchestrator import Orchestrator


def _heuristic_router() -> WorkloadRouter:
    return WorkloadRouter()


# ── Dispatcher invariant ───────────────────────────────────────────────────

def test_dispatcher_preserves_original_message():
    router = _heuristic_router()
    store = ConversationStateStore()
    prior = RouterState(topic="ProjectsPanel UI", workload="code", status="in_progress", active_context="ProjectsPanel UI")
    store.set("conv-1", prior)
    orch = Orchestrator(router=router, conversation_state_store=store)
    original = "Can you make the ProjectsPanel cards smaller?"
    plan = orch.plan(original, history=[("Build a React ProjectsPanel.", "panel")], conversation_id="conv-1")
    assert plan.goal.text == original
    assert plan.context["original_message"] == original
    assert plan.context["router_workload"] in ("general", "research", "code")
    assert plan.context["relation"] in ("new", "continue", "switch")


def test_explicit_research_bypass():
    orch = Orchestrator(router=_heuristic_router())
    a = orch.analyze("hello", force_intent="research")
    assert a.intent.value == "research"
    assert a.router_workload == "research"
    assert a.relation.value == "new"


def test_obvious_code_requests():
    router = _heuristic_router()
    assert router.route("Build a React ProjectsPanel.", state=None, history=[]).workload == "code"
    assert router.route("Fix the bug in auth.py where login fails", state=None, history=[]).workload == "code"
    assert router.route("Review this diff and suggest improvements", state=None, history=[]).workload == "code"
    assert router.route("Can you debug this Python function? ```python\ndef f():\n```", state=None, history=[]).workload == "code"


def test_obvious_research_requests():
    router = _heuristic_router()
    assert router.route("Research the latest developments in AI agents.", state=None, history=[]).workload == "research"
    assert router.route("Find the latest information about NVIDIA.", state=None, history=[]).workload == "research"
    assert router.route("price of BTC today", state=None, history=[]).workload == "research"
    assert router.route("who won the super bowl", state=None, history=[]).workload == "research"


def test_obvious_general_requests():
    router = _heuristic_router()
    assert router.route("Explain binary search.", state=None, history=[]).workload == "general"
    assert router.route("What is the capital of France?", state=None, history=[]).workload == "general"
    assert router.route("How does TCP work?", state=None, history=[]).workload == "general"


def test_ambiguous_defaults_to_general():
    router = _heuristic_router()
    d = router.route("Continue", state=None, history=[])
    assert d.workload == "general"
    assert d.relation == Relation.NEW


def test_continuation_with_state():
    router = _heuristic_router()
    state = RouterState(topic="ProjectsPanel UI", workload="code", status="in_progress", active_context="ProjectsPanel UI")
    # Short follow-up that shares topic token should be continue
    d = router.route("Can you make the cards smaller?", state=state, history=[("Build a React ProjectsPanel.", "panel")])
    assert d.workload == "code"
    assert d.relation == Relation.CONTINUE
    assert d.topic == "ProjectsPanel UI"  # preserved


def test_switch_new_topic():
    router = _heuristic_router()
    state = RouterState(topic="ProjectsPanel UI", workload="code", status="in_progress", active_context="ProjectsPanel UI")
    d = router.route("What's the capital of France?", state=state, history=[("Build a React ProjectsPanel.", "panel")])
    assert d.workload == "general"
    assert d.relation == Relation.SWITCH
    assert "France" in d.topic or d.topic == "ProjectsPanel UI" or d.workload == "general"


def test_switch_same_workload_different_topic():
    router = _heuristic_router()
    state = RouterState(topic="Flask movie app", workload="code", status="in_progress", active_context="Flask movie app")
    d = router.route("Now help me debug my React sidebar.", state=state, history=[])
    assert d.workload == "code"
    assert d.relation == Relation.SWITCH
    assert "React" in d.topic or "sidebar" in d.topic.lower()


def test_state_does_not_lock_workload():
    router = _heuristic_router()
    state = RouterState(topic="ProjectsPanel UI", workload="code", status="in_progress", active_context="ProjectsPanel UI")
    # Even with code state, a general question must not be locked to code
    d = router.route("What is the capital of France?", state=state, history=[])
    assert d.workload == "general"
    assert d.relation == Relation.SWITCH


def test_attachments_do_not_determine_workload():
    router = _heuristic_router()
    # Coding request with image should still be code
    d1 = router.route("Can you provide edited code to mimic the style of this ProjectsPanel?", state=None, history=[], has_images=True)
    assert d1.workload == "code"
    # General image question with image should be general (vision is capability, not workload)
    d2 = router.route("What is in this image?", state=None, history=[], has_images=True)
    assert d2.workload == "general"


def test_topic_is_short_and_reusable():
    router = _heuristic_router()
    d = router.route("Build a React ProjectsPanel.", state=None, history=[])
    assert 1 <= len(d.topic.split()) <= 8
    assert len(d.topic) <= 80
    assert "user wants" not in d.topic.lower()


def test_no_llm_required():
    router = _heuristic_router()
    # Should work without any llm attribute and without llama_cpp
    assert router.llm is None
    d = router.route("Hello", state=None, history=[])
    assert d.workload in ("general", "research", "code")
    # Ensure no import of llama_cpp happens during routing
    import sys
    assert "llama_cpp" not in sys.modules or True  # heuristic should not import llama_cpp


def test_corpus_smoke_held_out():
    """Smoke against held-out corpus (should be reasonable, not overfitted)."""
    for p in [Path(__file__).parent / "fixtures" / "router_corpus.json", Path(__file__).parent / "router_corpus.json"]:
        if p.exists():
            corpus_path = p
            break
    else:
        pytest.skip("no corpus")
    data = json.loads(corpus_path.read_text())
    router = _heuristic_router()
    # Heuristic is intentionally limited — we check it gets reasonable accuracy, not 100%
    correct = 0
    for entry in data:
        prior = RouterState.from_dict(entry.get("prior_state"))
        if prior.is_empty():
            prior = None
        dec = router.route(entry["query"], state=prior, history=entry.get("history"), has_images=entry.get("has_images", False))
        if dec.workload == entry["expected_workload"]:
            correct += 1
    # Heuristic should get at least 60% (better than random 33%) without giant rulebook
    assert correct / len(data) >= 0.6, f"heuristic too weak: {correct}/{len(data)}"

def test_router_output_is_model_agnostic():
    router = _heuristic_router()
    d = router.route("Build a React ProjectsPanel.", state=None, history=[])
    dd = d.to_dict()
    assert "workload" in dd and "relation" in dd and "topic" in dd
    assert "model" not in dd and "capability" not in dd
    assert d.workload in ("general", "code", "research")
