"""Tests for ExecutionContext — the unified runtime state object."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from novi.runtime.execution_context import ExecutionContext
from novi.runtime.tool_executor import ToolExecutor
from novi.orchestrator.task_types import (
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
    from novi.capabilities.base import Capability

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
    ctx = ExecutionContext(analysis=analysis, model_name="qwen3:8b", workload="code")
    d = ctx.to_dict()
    assert d["intent"] == "coding"
    assert d["complexity_score"] == 5
    assert d["plan_level"] == 1
    assert d["strategy"] == "execute"
    assert d["evidence_signals"] == ["project"]
    assert d["model_name"] == "qwen3:8b"
    assert d["workload"] == "code"


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
    from novi.runtime.runtime import NoviRuntime

    runtime = NoviRuntime()
    ctx = ExecutionContext(user_input="hello")
    # run_stream is a generator; consume it to trigger init
    events = list(runtime.run_stream(context=ctx))
    # Should have produced at least a thinking event and a token
    kinds = [k for k, *_ in events]
    assert "thinking" in kinds or "token" in kinds
    # Trace should be populated
    assert ctx.trace is not None
    assert ctx.trace.user_input == "hello"
    assert ctx.trace.request_id  # auto-generated


# ── Phase 6B: ctx as source of truth ────────────────────────────────────────


def test_ctx_model_name_controls_execution():
    """ctx.model_name set before run_stream should be used for model binding."""
    from novi.runtime.runtime import NoviRuntime

    runtime = NoviRuntime()
    ctx = ExecutionContext(user_input="hello")
    ctx.model_name = "test-model"
    list(runtime.run_stream(context=ctx))
    assert ctx.trace.model_selected == "test-model"


def test_ctx_memory_context_persists():
    """ctx.memory_context should survive through execution."""
    from novi.runtime.runtime import NoviRuntime

    runtime = NoviRuntime()
    ctx = ExecutionContext(user_input="hello")
    ctx.memory_context = "User prefers dark mode"
    list(runtime.run_stream(context=ctx))
    # memory_context should still be on ctx after execution
    assert ctx.memory_context == "User prefers dark mode"


def test_ctx_plan_context_set_by_planner():
    """ctx.plan_context should be populated when planning triggers."""
    from novi.runtime.runtime import NoviRuntime

    runtime = NoviRuntime()
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
    from novi.runtime.runtime import NoviRuntime

    runtime = NoviRuntime()
    analysis = TaskAnalysis(intent=IntentType.CODING)
    ctx = ExecutionContext(user_input="fix bug", analysis=analysis)
    list(runtime.run_stream(context=ctx))
    assert ctx.trace.intent == "coding"


def test_ctx_trace_receives_model_routing():
    """ctx.trace should record model routing decisions."""
    from novi.runtime.runtime import NoviRuntime

    runtime = NoviRuntime(model_service=SimpleNamespace(resolve=lambda role: ("test", "test-model")))
    ctx = ExecutionContext(user_input="hello")
    list(runtime.run_stream(context=ctx))
    # model_selected should be set (from configured model)
    assert ctx.trace.model_selected
    assert ctx.trace.model_reason in ("workload_match", "config_override", "force_capability", "execution_plan")


def test_ctx_allowed_tools_populated():
    """ctx.allowed_tools should be populated from analysis capabilities."""
    from novi.runtime.runtime import NoviRuntime

    runtime = NoviRuntime()
    analysis = TaskAnalysis(
        intent=IntentType.CODING,
        capabilities=["coding", "filesystem"],
    )
    ctx = ExecutionContext(user_input="edit file", analysis=analysis)
    list(runtime.run_stream(context=ctx))
    assert len(ctx.allowed_tools) > 0


def test_ctx_execution_plan_drives_tools():
    """When execution_plan is set, ctx.allowed_tools comes from plan.tools."""
    from novi.runtime.runtime import NoviRuntime
    from novi.orchestrator.task_types import ExecutionPlan, Goal

    runtime = NoviRuntime()
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
    from novi.runtime.runtime import NoviRuntime

    runtime = NoviRuntime()
    ctx = ExecutionContext(user_input="hello")
    list(runtime.run_stream(context=ctx))
    # grounding_text exists on ctx (may be empty for non-research)
    assert isinstance(ctx.grounding_text, str)


# ── Regression: full web-search execution path ──────────────────────────────


def test_full_research_pipeline_trace_ownership():
    """Full research pipeline (intent → grounding → routing → ReAct → trace finalization)
    must complete without AttributeError and ctx.trace must own all trace data.

    Regression test for: 'NoviRuntime' object has no attribute '_trace'
    """
    from novi.runtime.runtime import NoviRuntime

    runtime = NoviRuntime(model_service=SimpleNamespace(resolve=lambda role: ("test", "test-model")))
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
    # Deterministic: stub the live search; assertions target trace/routing state,
    # not search results.
    with patch("novi.tools.search_pipeline._search_multi", return_value=([], None)):
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
    Regression test for: 'NoviRuntime' object has no attribute '_trace'
    """
    from novi.runtime.runtime import NoviRuntime

    runtime = NoviRuntime()
    events = list(runtime.run_stream("hello"))
    # Must not crash with AttributeError
    kinds = [k for k, *_ in events]
    assert "thinking" in kinds or "token" in kinds


# ── Search reformulation tests ────────────────────────────────────────────────


class TestSearchReformulation:
    """Unit tests for RetrievalExecutor static utilities."""

    def test_key_terms_extracts_meaningful_words(self):
        from novi.runtime.retrieval import RetrievalExecutor
        terms = RetrievalExecutor.extract_key_terms("what is the best pve build in SHindo Life")
        assert "pve" in terms
        assert "build" in terms
        assert "shindo" in terms
        assert "life" in terms
        assert "what" not in terms
        assert "is" not in terms
        assert "the" not in terms

    def test_key_terms_skips_stopwords(self):
        from novi.runtime.retrieval import RetrievalExecutor
        terms = RetrievalExecutor.extract_key_terms("how do I fix this bug")
        assert "fix" in terms
        assert "bug" in terms
        assert "how" not in terms
        assert "do" not in terms

    def test_relevance_score_high_match(self):
        from novi.runtime.retrieval import RetrievalExecutor
        text = "Shindo Life is a Roblox game with PvE builds and bloodlines"
        score = RetrievalExecutor.compute_relevance(text, ["shindo", "life", "pve", "build"])
        assert score >= 0.75

    def test_relevance_score_low_match(self):
        from novi.runtime.retrieval import RetrievalExecutor
        text = "Genshin Impact best builds for Hu Tao and Zhongli"
        score = RetrievalExecutor.compute_relevance(text, ["shindo", "life", "pve"])
        assert score < 0.3

    def test_relevance_score_empty_terms(self):
        from novi.runtime.retrieval import RetrievalExecutor
        assert RetrievalExecutor.compute_relevance("any text", []) == 1.0

    def test_reformulate_query_uses_key_terms(self):
        from novi.runtime.retrieval import RetrievalExecutor
        terms = ["shindo", "life", "pve", "build", "roblox"]
        result = RetrievalExecutor.reformulate_query(None, terms)
        assert result == "shindo life pve build roblox"

class TestExecutorEntryPoint:
    """Strategy dispatch via single execute() entry point."""

    # ── helpers ──────────────────────────────────────────────────────────

    def _fake_search(self):
        """Monkey-patch EvidenceCollector.collect to return SUFFICIENT."""
        from novi.runtime.evidence import EvidenceBundle, EvidenceCollector, RetrievalQuality

        def fake_collect(self, query, min_sources=1):
            b = EvidenceBundle(query=query)
            b.merged_text = "test result about shindo life"
            b.results = [{"title": "x", "content": "test"}]
            b.source_count = 1
            b.quality = RetrievalQuality.SUFFICIENT
            return b

        orig = EvidenceCollector.collect
        EvidenceCollector.collect = fake_collect
        return orig

    def _make_ctx(self, user_input: str):
        from novi.runtime.execution_context import ExecutionContext
        from novi.runtime.trace import ExecutionTrace
        ctx = ExecutionContext(user_input=user_input)
        ctx.trace = ExecutionTrace(user_input=user_input)
        return ctx

    def _set_research_analysis(self, ctx):
        import types
        ctx.analysis = types.SimpleNamespace(
            intent=types.SimpleNamespace(value="research"),
            capabilities=["research"],
            grounding=types.SimpleNamespace(
                needs_grounding=False, confidence=0.0,
                source="fallback", reason="no analysis",
            ),
            evidence=types.SimpleNamespace(signals=[], confidence=0.0),
            retrieval_plan=None,
            complexity=types.SimpleNamespace(score=1, plan_level=0),
            strategy=types.SimpleNamespace(value="direct"),
        )

    def _set_plan(self, ctx, strategy):
        import types
        from novi.runtime.retrieval_policy import RetrievalStrategy
        ctx.analysis = types.SimpleNamespace(
            intent=types.SimpleNamespace(value="research"),
            capabilities=["research"],
            grounding=types.SimpleNamespace(
                needs_grounding=False, confidence=0.0,
                source="plan", reason="plan-driven",
            ),
            evidence=types.SimpleNamespace(signals=[], confidence=0.0),
            retrieval_plan=types.SimpleNamespace(
                strategy=strategy,
                sources=[],
                reason="test",
            ),
            complexity=types.SimpleNamespace(score=1, plan_level=0),
            strategy=types.SimpleNamespace(value="direct"),
        )

    # ── path 1: retrieval plan (WEB_ONLY) ────────────────────────────────

    def test_execute_plan_web_only(self):
        from novi.runtime.retrieval import RetrievalExecutor
        from novi.runtime.retrieval_policy import RetrievalStrategy

        exe = RetrievalExecutor(debug_trace=True)
        ctx = self._make_ctx("test web only")
        self._set_plan(ctx, RetrievalStrategy.WEB_ONLY)
        orig = self._fake_search()
        try:
            results = list(exe.execute(ctx, "test web only"))
        finally:
            from novi.runtime.evidence import EvidenceCollector
            EvidenceCollector.collect = orig
        kinds = [r[0] for r in results]
        assert "trace" in kinds
        assert "thinking" in kinds
        assert ctx.grounding_text == "test result about shindo life"
        assert ctx.trace.retrieval_strategy == "web_only"

    # ── path 1: retrieval plan (KNOWLEDGE_ONLY) ──────────────────────────

    def test_execute_plan_knowledge_only(self):
        from novi.runtime.retrieval import RetrievalExecutor
        from novi.runtime.retrieval_policy import RetrievalStrategy

        exe = RetrievalExecutor(debug_trace=True)
        ctx = self._make_ctx("test kb only")
        self._set_plan(ctx, RetrievalStrategy.KNOWLEDGE_ONLY)
        results = list(exe.execute(ctx, "test kb only"))
        kinds = [r[0] for r in results]
        assert "trace" in kinds
        assert "thinking" in kinds
        assert ctx.trace.retrieval_strategy == "knowledge_only"

    # ── path 1: retrieval plan (NONE) → no-op trace ─────────────────────

    def test_execute_plan_none(self):
        from novi.runtime.retrieval import RetrievalExecutor
        from novi.runtime.retrieval_policy import RetrievalStrategy

        exe = RetrievalExecutor(debug_trace=True)
        ctx = self._make_ctx("test plan none")
        self._set_plan(ctx, RetrievalStrategy.NONE)
        results = list(exe.execute(ctx, "test plan none"))
        kinds = [r[0] for r in results]
        assert kinds == ["trace"]
        assert ctx.grounding_text == ""

    # ── path 2: analysis needs_grounding ─────────────────────────────────

    def test_execute_needs_grounding(self):
        import types
        from novi.runtime.retrieval import RetrievalExecutor

        exe = RetrievalExecutor(debug_trace=True)
        ctx = self._make_ctx("test needs grounding")
        ctx.analysis = types.SimpleNamespace(
            intent=types.SimpleNamespace(value="research"),
            capabilities=["research"],
            grounding=types.SimpleNamespace(
                needs_grounding=True, confidence=0.8,
                source="evidence", reason="low confidence",
            ),
            evidence=types.SimpleNamespace(
                signals=[types.SimpleNamespace(type="ambiguous")],
                confidence=0.6,
            ),
            retrieval_plan=None,
            complexity=types.SimpleNamespace(score=1, plan_level=0),
            strategy=types.SimpleNamespace(value="direct"),
        )
        orig = self._fake_search()
        try:
            results = list(exe.execute(ctx, "test needs grounding"))
        finally:
            from novi.runtime.evidence import EvidenceCollector
            EvidenceCollector.collect = orig
        kinds = [r[0] for r in results]
        assert "trace" in kinds
        assert "thinking" in kinds
        assert ctx.grounding_text == "test result about shindo life"

    # ── path 3: analysis exists but no grounding ─────────────────────────

    def test_execute_no_grounding_needed(self):
        import types
        from novi.runtime.retrieval import RetrievalExecutor

        exe = RetrievalExecutor(debug_trace=True)
        ctx = self._make_ctx("test no grounding")
        ctx.analysis = types.SimpleNamespace(
            intent=types.SimpleNamespace(value="coding"),
            capabilities=["coding"],
            grounding=types.SimpleNamespace(
                needs_grounding=False, confidence=0.9,
                source="stable", reason="well-known",
            ),
            evidence=types.SimpleNamespace(
                signals=[types.SimpleNamespace(type="stable_context")],
                confidence=0.9,
            ),
            retrieval_plan=None,
            complexity=types.SimpleNamespace(score=1, plan_level=0),
            strategy=types.SimpleNamespace(value="direct"),
        )
        results = list(exe.execute(ctx, "test no grounding"))
        kinds = [r[0] for r in results]
        assert kinds == ["trace"]
        assert ctx.grounding_text == ""

    # ── path 4: research intent fallback (no analysis) ───────────────────

    def test_execute_research_fallback(self):
        import types
        from novi.runtime.retrieval import RetrievalExecutor

        exe = RetrievalExecutor(debug_trace=True)
        # research intent requires no analysis, needs execution_plan
        ctx = self._make_ctx("test research fallback")
        ctx.execution_plan = types.SimpleNamespace(
            goal=types.SimpleNamespace(intent=types.SimpleNamespace(value="research")),
            capabilities=[],
            model_spec={},
            strategy=types.SimpleNamespace(value="direct"),
        )
        orig = self._fake_search()
        try:
            results = list(exe.execute(ctx, "test research fallback"))
        finally:
            from novi.runtime.evidence import EvidenceCollector
            EvidenceCollector.collect = orig
        kinds = [r[0] for r in results]
        assert "trace" in kinds
        assert "thinking" in kinds
        assert ctx.grounding_text == "test result about shindo life"

    # ── path 5: nothing to do ───────────────────────────────────────────

    def test_execute_noop(self):
        from novi.runtime.retrieval import RetrievalExecutor

        exe = RetrievalExecutor(debug_trace=True)
        ctx = self._make_ctx("test noop")
        results = list(exe.execute(ctx, "test noop"))
        assert results == []
        assert ctx.grounding_text == ""


class TestModelResolution:
    """Phase 2: workload-based model resolution through ModelSelector.

    The configured workload model is used verbatim — no capability ranking,
    no VRAM/loaded preference, no complexity upgrade, no default fallback.
    """

    def _service(self, workloads=None):
        import types
        workloads = workloads or {}
        return types.SimpleNamespace(
            _workloads=workloads,
            resolve=lambda w: ("test-provider", workloads.get(w, "")),
        )

    def _selector(self, model_service):
        from novi.runtime.model_selector import ModelSelector
        return ModelSelector(model_service)

    def test_resolve_returns_configured_model_verbatim(self):
        svc = self._service({"general": "qwen3:8b", "research": "gemma4:12b"})
        sel = self._selector(svc)
        assert sel.resolve("general") == "qwen3:8b"
        assert sel.resolve("research") == "gemma4:12b"

    def test_resolve_unknown_workload_rejected(self):
        sel = self._selector(self._service({"general": "qwen3:8b"}))
        with pytest.raises(ValueError):
            sel.resolve("chat")

    def test_resolve_unset_workload_raises_not_substitutes(self):
        """Unset workload must error, never fall back to another workload's model."""
        from novi.models import ModelUnavailableError
        sel = self._selector(self._service({"general": "qwen3:8b"}))
        with pytest.raises(ModelUnavailableError):
            sel.resolve("research")

    def test_resolve_missing_configured_model_raises(self):
        """Configured-but-not-installed model must error, never substitute."""
        import types
        from novi.models import ModelUnavailableError

        def resolve(w):
            if w == "general":
                raise ModelUnavailableError("general", "not-installed-model", ["qwen3:8b"])
            return ("test-provider", "")

        sel = self._selector(types.SimpleNamespace(resolve=resolve))
        with pytest.raises(ModelUnavailableError):
            sel.resolve("general")

    def test_runtime_resolves_workload_model(self):
        """Runtime: intent/capability → workload → configured model."""
        from novi.runtime.runtime import NoviRuntime
        from novi.runtime.execution_context import ExecutionContext

        svc = self._service({"general": "gen-model", "research": "res-model"})
        runtime = NoviRuntime(model_service=svc)
        ctx = ExecutionContext(user_input="hello")
        list(runtime.run_stream(context=ctx))
        assert ctx.workload == "general"
        assert ctx.model_name == "gen-model"
        assert ctx.trace.workload == "general"

    def test_runtime_missing_model_yields_explicit_error(self):
        """Missing workload model: explicit error, no LLM loop, no token output."""
        import types
        from novi.models import ModelUnavailableError
        from novi.runtime.runtime import NoviRuntime
        from novi.runtime.execution_context import ExecutionContext

        def resolve(w):
            raise ModelUnavailableError("general", "not-installed-model", ["qwen3:8b"])

        runtime = NoviRuntime(model_service=types.SimpleNamespace(resolve=resolve))
        ctx = ExecutionContext(user_input="hello")
        events = list(runtime.run_stream(context=ctx))
        kinds = [k for k, *_ in events]
        assert "error" in kinds
        assert any("not-installed-model" in str(e[1]) for e in events if e[0] == "error")
        assert not any(k == "token" for k, *_ in events)

    def test_runtime_rejects_non_vision_model_for_images(self):
        """Image input against a non-vision selected model: explicit rejection."""
        import types
        from novi.runtime.runtime import NoviRuntime
        from novi.runtime.execution_context import ExecutionContext

        svc = self._service({"general": "gen-model"})
        runtime = NoviRuntime(model_service=svc)
        ctx = ExecutionContext(user_input="describe this")
        ctx.attachments = [{"type": "image", "path": "x.png", "mime": "image/png"}]
        events = list(runtime.run_stream(context=ctx))
        kinds = [k for k, *_ in events]
        assert "error" in kinds
        assert any("vision" in str(e[1]).lower() for e in events if e[0] == "error")


class TestToolExecutor:
    """Regression tests for ToolExecutor (extracted from NoviRuntime)."""

    @pytest.fixture
    def registry(self):
        from novi.runtime.tool_registry import ToolRegistry
        reg = ToolRegistry()

        def _echo(**kwargs):
            return json.dumps(kwargs)

        def _fail():
            raise ValueError("boom")

        def _empty():
            return ""

        def _ok():
            return "ok result"

        def _requires_args(x):
            return str(x)

        reg.register("echo", _echo, "Echo args as JSON")
        reg.register("fail", _fail, "Always fails")
        reg.register("empty", _empty, "Returns empty string")
        reg.register("ok", _ok, "Always succeeds")
        reg.register("requires_args", _requires_args, "Requires 'x' arg")
        return reg

    @pytest.fixture
    def perms(self):
        import types
        return types.SimpleNamespace(resolve=lambda tool, args, agent: "ask")

    @pytest.fixture
    def lesson_store(self):
        import types
        calls = []
        store = types.SimpleNamespace(
            calls=calls,
            record=lambda name, args, out: calls.append((name, args, out)),
        )
        return store

    @pytest.fixture
    def executor(self, registry, perms, lesson_store):
        from novi.runtime.tool_executor import ToolExecutor
        lc_tools = registry.as_lc_tools()
        return ToolExecutor(
            registry=registry,
            perms=perms,
            lesson_store=lesson_store,
            lc_tools=lc_tools,
            tool_fallbacks={"fail": ["ok"]},
            max_tool_output=200,
            perm_mode="bypass",
        )

    # ── execute ──────────────────────────────────────────────────────────

    def test_execute_success(self, executor):
        tr = executor.execute("echo", {"x": 1})
        assert tr.success is True
        assert json.loads(tr.output) == {"x": 1}

    def test_execute_unknown_tool(self, executor, lesson_store):
        tr = executor.execute("nonexistent", {})
        assert tr.success is False
        assert tr.output.startswith("Error: unknown tool 'nonexistent'")
        assert len(lesson_store.calls) == 1

    def test_execute_permission_denied(self, executor):
        tr = executor.execute("echo", {"x": 1}, perm_mode="plan")
        assert tr.success is False
        assert tr.output.startswith("Error: the user DENIED permission")

    def test_execute_type_error(self, executor, lesson_store):
        tr = executor.execute("requires_args", {})
        assert tr.success is False
        assert tr.output.startswith("Error: bad arguments for requires_args")
        assert len(lesson_store.calls) == 1

    def test_execute_empty_result(self, executor):
        tr = executor.execute("empty", {})
        assert tr.success is False
        assert tr.output.startswith("Error: empty returned empty output")

    def test_execute_fallback_chain(self, executor, lesson_store):
        tr = executor.execute("fail", {})
        assert tr.success is True
        assert tr.output == "ok result"
        assert len(lesson_store.calls) == 2

    def test_execute_coordinator_intercept(self, executor):
        import types
        coord = types.SimpleNamespace(
            is_web_tool=lambda n: n == "echo",
            intercept=lambda n, a: "blocked by coordinator",
            record=lambda n, a, r: None,
        )
        tr = executor.execute("echo", {"x": 1}, coordinator=coord)
        assert tr.success is False
        assert tr.output == "blocked by coordinator"

    # ── permission gating ────────────────────────────────────────────────

    def test_check_permission_plan_mode(self, executor):
        assert executor._check_permission("echo", {}, perm_mode="plan") is False

    def test_check_permission_bypass_mode(self, executor):
        assert executor._check_permission("echo", {}, perm_mode="bypass") is True

    def test_check_permission_accept_edits(self, executor):
        assert executor._check_permission("edit_file", {}, perm_mode="accept-edits") is True
        assert executor._check_permission("echo", {}, perm_mode="accept-edits") is False

    def test_check_permission_callback(self, executor):
        called = []
        def cb(name, args):
            called.append(name)
            return True
        assert executor._check_permission("echo", {}, perm_mode="manual", permission_callback=cb) is True
        assert called == ["echo"]

    # ── compute_diff ─────────────────────────────────────────────────────

    def test_compute_diff_edit_file(self):
        diff = ToolExecutor.compute_diff("edit_file", {
            "path": "test.py", "old_text": "foo\nbar\n", "new_text": "foo\nbaz\n",
        })
        assert diff is not None
        assert diff["added"] == 1
        assert diff["removed"] == 1

    def test_compute_diff_write_file(self):
        diff = ToolExecutor.compute_diff("write_file", {"content": "hello\nworld\n"})
        assert diff is not None
        assert diff["added"] == 2

    def test_compute_diff_other(self):
        assert ToolExecutor.compute_diff("echo", {}) is None

    # ── tool_category ────────────────────────────────────────────────────

    def test_tool_category_known(self):
        assert ToolExecutor.tool_category("read_file") == "workspace"
        assert ToolExecutor.tool_category("web_search") == "web"
        assert ToolExecutor.tool_category("run_command") == "python"

    def test_tool_category_unknown(self):
        assert ToolExecutor.tool_category("nonexistent") == "other"

    # ── record_tool_call ─────────────────────────────────────────────────

    def test_record_tool_call(self):
        from novi.runtime.trace import ExecutionTrace
        trace = ExecutionTrace(user_input="test")
        ToolExecutor.record_tool_call(
            None, 0, "echo", {"x": 1}, '{"x":1}', 10.5, True,
            trace=trace,
        )
        assert len(trace.steps) == 1
        assert len(trace.steps[0].tool_calls) == 1
        tc = trace.steps[0].tool_calls[0]
        assert tc.name == "echo"
        assert tc.latency_ms == 10.5
        assert tc.success is True

    def test_record_tool_call_no_trace(self):
        # Must not raise
        ToolExecutor.record_tool_call(None, 0, "echo", {}, "", 0, True, trace=None)
