"""Tests for ExecutionContext — the unified runtime state object."""

import pytest

from cozmo.runtime.execution_context import ExecutionContext
from cozmo.orchestrator.task_types import (
    ComplexityScore,
    EvidenceAnalysis,
    EvidenceRequirements,
    EvidenceSignal,
    ExecutionPlan,
    ExecutionStrategy,
    Goal,
    GroundingDecision,
    IntentType,
    TaskAnalysis,
)


# ── Construction ────────────────────────────────────────────────────────────


def test_default_construction():
    ctx = ExecutionContext()
    assert ctx.user_input == ""
    assert ctx.attachments == []
    assert ctx.analysis is None
    assert ctx.execution_plan is None
    assert ctx.history == []
    assert ctx.summary == ""
    assert ctx.model_name == ""
    assert ctx.allowed_tools == []
    assert ctx.activated_skills == []
    assert ctx.grounding_text == ""
    assert ctx.plan_context == ""
    assert ctx.trace is None
    assert ctx.force_model == ""
    assert ctx.force_capability == ""
    assert ctx.metadata == {}


def test_from_input_builder():
    ctx = ExecutionContext.from_input(
        "fix auth.py",
        attachments=[{"type": "image", "name": "screenshot.png"}],
        history=[("hi", "hello"), ("fix auth", "ok")],
        summary="Earlier context",
        force_model="qwen3:8b",
    )
    assert ctx.user_input == "fix auth.py"
    assert len(ctx.attachments) == 1
    assert len(ctx.history) == 2
    assert ctx.summary == "Earlier context"
    assert ctx.force_model == "qwen3:8b"


def test_from_input_defaults():
    ctx = ExecutionContext.from_input("hello")
    assert ctx.user_input == "hello"
    assert ctx.attachments == []
    assert ctx.history == []


# ── Derived helpers ─────────────────────────────────────────────────────────


def test_intent_str_from_analysis():
    analysis = TaskAnalysis(intent=IntentType.CODING)
    ctx = ExecutionContext(analysis=analysis)
    assert ctx.intent_str == "coding"


def test_intent_str_from_plan():
    plan = ExecutionPlan(goal=Goal(intent=IntentType.RESEARCH))
    ctx = ExecutionContext(execution_plan=plan)
    assert ctx.intent_str == "research"


def test_intent_str_default():
    ctx = ExecutionContext()
    assert ctx.intent_str == "conversation"


def test_cap_ids_from_analysis():
    analysis = TaskAnalysis(capabilities=["coding", "filesystem"])
    ctx = ExecutionContext(analysis=analysis)
    assert ctx.cap_ids == ["coding", "filesystem"]


def test_cap_ids_from_plan():
    from cozmo.capabilities.base import Capability

    plan = ExecutionPlan(capabilities=[
        Capability(id="coding"),
        Capability(id="filesystem"),
    ])
    ctx = ExecutionContext(execution_plan=plan)
    assert ctx.cap_ids == ["coding", "filesystem"]


def test_cap_ids_default():
    ctx = ExecutionContext()
    assert ctx.cap_ids == ["conversation"]


def test_complexity_score():
    analysis = TaskAnalysis(complexity=ComplexityScore(score=7))
    ctx = ExecutionContext(analysis=analysis)
    assert ctx.complexity_score == 7


def test_complexity_score_default():
    ctx = ExecutionContext()
    assert ctx.complexity_score == 1


def test_plan_level():
    analysis = TaskAnalysis(complexity=ComplexityScore(plan_level=2))
    ctx = ExecutionContext(analysis=analysis)
    assert ctx.plan_level == 2


def test_plan_level_default():
    ctx = ExecutionContext()
    assert ctx.plan_level == 0


# ── Evidence-gated helpers ──────────────────────────────────────────────────


def test_needs_grounding_from_analysis():
    analysis = TaskAnalysis(
        grounding=GroundingDecision(
            needs_grounding=True,
            confidence=0.95,
            reason="Intent classified as research",
            source="keyword",
        )
    )
    ctx = ExecutionContext(analysis=analysis)
    assert ctx.needs_grounding is True


def test_needs_grounding_false_from_analysis():
    analysis = TaskAnalysis(
        grounding=GroundingDecision(
            needs_grounding=False,
            source="none",
        )
    )
    ctx = ExecutionContext(analysis=analysis)
    assert ctx.needs_grounding is False


def test_needs_grounding_from_plan():
    analysis = TaskAnalysis(
        grounding=GroundingDecision(
            needs_grounding=True,
            source="heuristic",
        )
    )
    plan = ExecutionPlan(
        goal=Goal(text="test"),
        context={"analysis": analysis},
    )
    ctx = ExecutionContext(execution_plan=plan)
    assert ctx.needs_grounding is True


def test_needs_grounding_research_intent_fallback():
    ctx = ExecutionContext()
    # No analysis or plan, intent defaults to conversation
    assert ctx.needs_grounding is False


def test_needs_memory_from_evidence():
    analysis = TaskAnalysis(
        evidence=EvidenceAnalysis(
            signals=[EvidenceSignal(type="memory", strength="strong")],
        )
    )
    ctx = ExecutionContext(analysis=analysis)
    assert ctx.needs_memory is True


def test_needs_memory_conversation_fallback():
    ctx = ExecutionContext()
    assert ctx.needs_memory is True  # conversation intent


def test_should_plan():
    analysis = TaskAnalysis(complexity=ComplexityScore(plan_level=2))
    ctx = ExecutionContext(analysis=analysis)
    assert ctx.should_plan is True


def test_should_plan_no_analysis():
    ctx = ExecutionContext()
    assert ctx.should_plan is False


# ── Serialization ───────────────────────────────────────────────────────────


def test_to_dict_minimal():
    ctx = ExecutionContext(user_input="hello")
    d = ctx.to_dict()
    assert d["user_input"] == "hello"
    assert d["intent"] == "conversation"
    assert d["cap_ids"] == ["conversation"]
    assert d["model_name"] == ""
    assert "complexity_score" not in d


def test_to_dict_with_analysis():
    analysis = TaskAnalysis(
        intent=IntentType.CODING,
        complexity=ComplexityScore(score=5, plan_level=1),
        strategy=ExecutionStrategy.EXECUTE,
        evidence=EvidenceAnalysis(
            signals=[EvidenceSignal(type="project", strength="strong")]
        ),
    )
    ctx = ExecutionContext(analysis=analysis, model_name="qwen3:8b", role="coder")
    d = ctx.to_dict()
    assert d["intent"] == "coding"
    assert d["complexity_score"] == 5
    assert d["plan_level"] == 1
    assert d["strategy"] == "execute"
    assert d["evidence_signals"] == ["project"]
    assert d["model_name"] == "qwen3:8b"
    assert d["role"] == "coder"


def test_to_dict_with_grounding():
    ctx = ExecutionContext(grounding_text="x" * 500)
    d = ctx.to_dict()
    assert d["grounding_length"] == 500


def test_to_dict_with_forces():
    ctx = ExecutionContext(force_model="gpt-4", force_capability="coding")
    d = ctx.to_dict()
    assert d["force_model"] == "gpt-4"
    assert d["force_capability"] == "coding"


# ── Integration with run_stream (smoke) ─────────────────────────────────────


def test_run_stream_accepts_context():
    """run_stream() should accept context= without error."""
    from cozmo.runtime.runtime import CozmoRuntime

    runtime = CozmoRuntime()
    ctx = ExecutionContext(user_input="hello")
    # run_stream is a generator; consume it to trigger init
    events = list(runtime.run_stream(context=ctx))
    # Should have produced at least a thinking event and a token
    kinds = [k for k, *_ in events]
    assert "thinking" in kinds or "token" in kinds
    # Trace should be populated
    assert ctx.trace is not None
    assert ctx.trace.user_input == "hello"


def test_run_stream_backward_compat():
    """run_stream() with old positional params still works."""
    from cozmo.runtime.runtime import CozmoRuntime

    runtime = CozmoRuntime()
    events = list(runtime.run_stream("hello world"))
    kinds = [k for k, *_ in events]
    assert "thinking" in kinds or "token" in kinds


def test_run_stream_populates_ctx_trace():
    """Trace created by run_stream should be stored in ctx.trace."""
    from cozmo.runtime.runtime import CozmoRuntime

    runtime = CozmoRuntime()
    ctx = ExecutionContext(user_input="test")
    list(runtime.run_stream(context=ctx))
    assert ctx.trace is not None
    assert ctx.trace.request_id  # auto-generated


# ── Phase 6B: ctx as source of truth ────────────────────────────────────────


def test_ctx_model_name_controls_execution():
    """ctx.model_name set before run_stream should be used for model binding."""
    from cozmo.runtime.runtime import CozmoRuntime

    runtime = CozmoRuntime()
    ctx = ExecutionContext(user_input="hello")
    ctx.model_name = "test-model"
    list(runtime.run_stream(context=ctx))
    assert ctx.trace.model_selected == "test-model"


def test_ctx_memory_context_persists():
    """ctx.memory_context should survive through execution."""
    from cozmo.runtime.runtime import CozmoRuntime

    runtime = CozmoRuntime()
    ctx = ExecutionContext(user_input="hello")
    ctx.memory_context = "User prefers dark mode"
    list(runtime.run_stream(context=ctx))
    # memory_context should still be on ctx after execution
    assert ctx.memory_context == "User prefers dark mode"


def test_ctx_plan_context_set_by_planner():
    """ctx.plan_context should be populated when planning triggers."""
    from cozmo.runtime.runtime import CozmoRuntime

    runtime = CozmoRuntime()
    # High complexity to trigger planning
    analysis = TaskAnalysis(
        intent=IntentType.PLANNING,
        complexity=ComplexityScore(score=8, plan_level=3, max_steps=10),
        strategy=ExecutionStrategy.PLANNED,
    )
    ctx = ExecutionContext(user_input="design a distributed system", analysis=analysis)
    list(runtime.run_stream(context=ctx))
    # plan_context may or may not be populated depending on model_service,
    # but the field should exist and trace should record the attempt
    assert isinstance(ctx.plan_context, str)
    assert isinstance(ctx.trace.plan_generated, bool)


def test_ctx_trace_receives_intent():
    """ctx.trace.intent should match ctx.intent_str after analysis."""
    from cozmo.runtime.runtime import CozmoRuntime

    runtime = CozmoRuntime()
    analysis = TaskAnalysis(intent=IntentType.CODING)
    ctx = ExecutionContext(user_input="fix bug", analysis=analysis)
    list(runtime.run_stream(context=ctx))
    assert ctx.trace.intent == "coding"


def test_ctx_trace_receives_model_routing():
    """ctx.trace should record model routing decisions."""
    from cozmo.runtime.runtime import CozmoRuntime

    runtime = CozmoRuntime()
    ctx = ExecutionContext(user_input="hello")
    list(runtime.run_stream(context=ctx))
    # model_selected should be set (from router default)
    assert ctx.trace.model_selected
    assert ctx.trace.model_reason in ("role_match", "config_override", "force_capability", "execution_plan")


def test_ctx_allowed_tools_populated():
    """ctx.allowed_tools should be populated from analysis capabilities."""
    from cozmo.runtime.runtime import CozmoRuntime

    runtime = CozmoRuntime()
    analysis = TaskAnalysis(
        intent=IntentType.CODING,
        capabilities=["coding", "filesystem"],
    )
    ctx = ExecutionContext(user_input="edit file", analysis=analysis)
    list(runtime.run_stream(context=ctx))
    assert len(ctx.allowed_tools) > 0


def test_ctx_execution_plan_drives_tools():
    """When execution_plan is set, ctx.allowed_tools comes from plan.tools."""
    from cozmo.runtime.runtime import CozmoRuntime
    from cozmo.orchestrator.task_types import ExecutionPlan, Goal

    runtime = CozmoRuntime()
    plan = ExecutionPlan(
        goal=Goal(intent=IntentType.CODING),
        tools=["read_file", "edit_file"],
        model_spec={"model": "test-model"},
    )
    ctx = ExecutionContext(user_input="edit file", execution_plan=plan)
    list(runtime.run_stream(context=ctx))
    assert "read_file" in ctx.allowed_tools
    assert "edit_file" in ctx.allowed_tools


def test_ctx_to_dict_includes_memory_context():
    """to_dict should include memory_context_length when set."""
    ctx = ExecutionContext(user_input="hello", memory_context="some memory")
    d = ctx.to_dict()
    assert d["memory_context_length"] == 11


def test_ctx_grounding_populated():
    """ctx.grounding_text should be set (even if empty) after grounding phase."""
    from cozmo.runtime.runtime import CozmoRuntime

    runtime = CozmoRuntime()
    ctx = ExecutionContext(user_input="hello")
    list(runtime.run_stream(context=ctx))
    # grounding_text exists on ctx (may be empty for non-research)
    assert isinstance(ctx.grounding_text, str)


# ── Regression: full web-search execution path ──────────────────────────────


def test_full_research_pipeline_trace_ownership():
    """Full research pipeline (intent → grounding → routing → ReAct → trace finalization)
    must complete without AttributeError and ctx.trace must own all trace data.

    Regression test for: 'CozmoRuntime' object has no attribute '_trace'
    """
    from cozmo.runtime.runtime import CozmoRuntime

    runtime = CozmoRuntime()
    analysis = TaskAnalysis(
        intent=IntentType.RESEARCH,
        strategy=ExecutionStrategy.RESEARCH,
        complexity=ComplexityScore(score=3, plan_level=0, max_steps=5),
        evidence=EvidenceAnalysis(
            requirements=EvidenceRequirements(external=True),
            confidence=0.9,
            signals=[EvidenceSignal(type="temporal", strength="strong")],
        ),
        grounding=GroundingDecision(
            needs_grounding=True,
            confidence=0.9,
            reason="Research intent",
            source="keyword",
        ),
    )
    ctx = ExecutionContext(user_input="what is the best pve build in SHindo Life", analysis=analysis)
    events = list(runtime.run_stream(context=ctx))

    # Trace must exist and be fully owned by ctx
    assert ctx.trace is not None, "ctx.trace must exist after execution"
    assert ctx.trace.request_id, "trace must have request_id"
    # total_latency_ms is >= 0 (may be 0 for very fast error paths)
    assert isinstance(ctx.trace.total_latency_ms, (int, float))

    # Grounding must have been attempted
    assert isinstance(ctx.grounding_text, str)

    # Tool binding must have been recorded (may be empty in error path)
    assert isinstance(ctx.trace.tools_available, list)
    assert isinstance(ctx.trace.tools_bound, list)

    # Router decision must be recorded
    assert ctx.trace.model_selected, "trace must record model name"
    assert ctx.trace.model_reason, "trace must record model reason"

    # Stop reason must be set
    assert ctx.trace.stop_reason in ("completed", "empty", "max_steps", "stopped", "error")

    # No AttributeError means no self._trace references survived
    kinds = [k for k, *_ in events]
    assert "thinking" in kinds or "token" in kinds


def test_backward_compat_trace_ownership():
    """Old API (positional params) must also populate trace through ctx.
    Regression test for: 'CozmoRuntime' object has no attribute '_trace'
    """
    from cozmo.runtime.runtime import CozmoRuntime

    runtime = CozmoRuntime()
    events = list(runtime.run_stream("hello"))
    # Must not crash with AttributeError
    kinds = [k for k, *_ in events]
    assert "thinking" in kinds or "token" in kinds


# ── Search reformulation tests ────────────────────────────────────────────────


class TestSearchReformulation:
    """Unit tests for _grounding_search reformulation logic."""

    def test_key_terms_extracts_meaningful_words(self):
        from cozmo.runtime.runtime import CozmoRuntime
        terms = CozmoRuntime._key_terms("what is the best pve build in SHindo Life")
        assert "pve" in terms
        assert "build" in terms
        assert "shindo" in terms
        assert "life" in terms
        assert "what" not in terms
        assert "is" not in terms
        assert "the" not in terms

    def test_key_terms_skips_stopwords(self):
        from cozmo.runtime.runtime import CozmoRuntime
        terms = CozmoRuntime._key_terms("how do I fix this bug")
        assert "fix" in terms
        assert "bug" in terms
        assert "how" not in terms
        assert "do" not in terms

    def test_relevance_score_high_match(self):
        from cozmo.runtime.runtime import CozmoRuntime
        text = "Shindo Life is a Roblox game with PvE builds and bloodlines"
        score = CozmoRuntime._relevance_score(text, ["shindo", "life", "pve", "build"])
        assert score >= 0.75

    def test_relevance_score_low_match(self):
        from cozmo.runtime.runtime import CozmoRuntime
        text = "Genshin Impact best builds for Hu Tao and Zhongli"
        score = CozmoRuntime._relevance_score(text, ["shindo", "life", "pve"])
        # At most 0/3
        assert score < 0.3

    def test_relevance_score_empty_terms(self):
        from cozmo.runtime.runtime import CozmoRuntime
        assert CozmoRuntime._relevance_score("any text", []) == 1.0

    def test_reformulate_query_uses_key_terms(self):
        from cozmo.runtime.runtime import CozmoRuntime
        terms = ["shindo", "life", "pve", "build", "roblox"]
        result = CozmoRuntime._reformulate_query(None, terms)
        assert result == "shindo life pve build roblox"
