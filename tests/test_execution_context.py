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


# ── Phase 6B: ctx as source of truth ────────────────────────────────────────


def test_ctx_to_dict_includes_memory_context():
    """to_dict should include memory_context_length when set."""
    ctx = ExecutionContext(user_input="hello", memory_context="some memory")
    d = ctx.to_dict()
    assert d["memory_context_length"] == 11


# ── Regression: full web-search execution path ──────────────────────────────


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
            from novi.tools.search_pipeline import SearchResult
            b.results = [SearchResult(title="test", url="https://example.com", snippet=b.merged_text)]
            b.source_count = 1
            b.quality = RetrievalQuality.SUFFICIENT
            return b

        orig = EvidenceCollector.collect
        EvidenceCollector.collect = fake_collect
        return orig

    def _make_ctx(self, user_input: str):
        from novi.runtime.execution_context import ExecutionContext
        ctx = ExecutionContext(user_input=user_input)
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

        exe = RetrievalExecutor()
        exe._is_search_configured = lambda: True
        ctx = self._make_ctx("test web only")
        self._set_plan(ctx, RetrievalStrategy.WEB_ONLY)
        orig = self._fake_search()
        try:
            results = list(exe.execute(ctx, "test web only"))
        finally:
            from novi.runtime.evidence import EvidenceCollector
            EvidenceCollector.collect = orig
        kinds = [r[0] for r in results]
        assert "status" in kinds
        assert "thinking" in kinds
        assert "test result about shindo life" in ctx.grounding_text
        assert "https://example.com" in ctx.grounding_text

    # ── path 1: retrieval plan (KNOWLEDGE_ONLY) ──────────────────────────

    def test_execute_plan_knowledge_only(self):
        from novi.runtime.retrieval import RetrievalExecutor
        from novi.runtime.retrieval_policy import RetrievalStrategy

        exe = RetrievalExecutor()
        exe._is_search_configured = lambda: True
        ctx = self._make_ctx("test kb only")
        self._set_plan(ctx, RetrievalStrategy.KNOWLEDGE_ONLY)
        results = list(exe.execute(ctx, "test kb only"))
        kinds = [r[0] for r in results]
        assert "status" in kinds
        assert "thinking" in kinds

    # ── path 1: retrieval plan (NONE) → no-op trace ─────────────────────

    def test_execute_plan_none(self):
        from novi.runtime.retrieval import RetrievalExecutor
        from novi.runtime.retrieval_policy import RetrievalStrategy

        exe = RetrievalExecutor()
        exe._is_search_configured = lambda: True
        ctx = self._make_ctx("test plan none")
        self._set_plan(ctx, RetrievalStrategy.NONE)
        results = list(exe.execute(ctx, "test plan none"))
        kinds = [r[0] for r in results]
        assert kinds == ["status"]
        assert ctx.grounding_text == ""

    # ── path 2: analysis needs_grounding ─────────────────────────────────

    def test_execute_needs_grounding(self):
        import types
        from novi.runtime.retrieval import RetrievalExecutor

        exe = RetrievalExecutor()
        exe._is_search_configured = lambda: True
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
        assert "status" in kinds
        assert "thinking" in kinds
        assert "test result about shindo life" in ctx.grounding_text
        assert "https://example.com" in ctx.grounding_text

    # ── path 3: analysis exists but no grounding ─────────────────────────

    def test_execute_no_grounding_needed(self):
        import types
        from novi.runtime.retrieval import RetrievalExecutor

        exe = RetrievalExecutor()
        exe._is_search_configured = lambda: True
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
        assert kinds == ["status"]
        assert ctx.grounding_text == ""

    # ── path 4: research intent fallback (no analysis) ───────────────────

    def test_execute_research_fallback(self):
        import types
        from novi.runtime.retrieval import RetrievalExecutor

        exe = RetrievalExecutor()
        exe._is_search_configured = lambda: True
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
        assert "status" in kinds
        assert "thinking" in kinds
        assert "test result about shindo life" in ctx.grounding_text
        assert "https://example.com" in ctx.grounding_text

    # ── path 5: nothing to do ───────────────────────────────────────────

    def test_execute_noop(self):
        from novi.runtime.retrieval import RetrievalExecutor

        exe = RetrievalExecutor()
        exe._is_search_configured = lambda: True
        ctx = self._make_ctx("test noop")
        results = list(exe.execute(ctx, "test noop"))
        assert results == []
        assert ctx.grounding_text == ""


class TestModelResolution:
    """Single primary model through ModelSelector.

    The primary model is used verbatim for every strategy — no capability ranking,
    no VRAM/loaded preference, no complexity upgrade, no default fallback.
    """

    def _service(self, model="qwen3:8b"):
        import types
        return types.SimpleNamespace(
            _model=model,
            resolve_primary=lambda: ("test-provider", model),
            bind_model=lambda name, tools, temperature=0.0: None,
            client_for_model=lambda name, temperature=0.0: None,
            validate=lambda *a, **k: [],
        )

    def _selector(self, model_service):
        from novi.runtime.model_selector import ModelSelector
        return ModelSelector(model_service)

    def test_resolve_returns_configured_model_verbatim(self):
        from novi.models import ModelRegistry, ModelService
        from novi.providers import ModelInfo
        reg = ModelRegistry()
        reg.update("test-provider", [ModelInfo(name="qwen3:8b", provider="test-provider")])
        from novi.runtime.model_selector import ModelSelector
        sel = ModelSelector(ModelService({"llm": {"primary_model": "qwen3:8b"}}, reg))
        assert sel.resolve() == "qwen3:8b"

    def test_resolve_unset_primary_raises_not_substitutes(self):
        """Unset primary must error, never fall back to another model."""
        import types
        from novi.models import ModelUnavailableError
        svc = types.SimpleNamespace(
            resolve_primary=lambda: ("test-provider", ""),
        )
        sel = self._selector(svc)
        with pytest.raises(ModelUnavailableError):
            sel.resolve()

    def test_resolve_missing_configured_model_raises(self):
        """Configured-but-not-installed model must error, never substitute."""
        from novi.models import ModelRegistry, ModelService, ModelUnavailableError
        from novi.providers import ModelInfo
        from novi.runtime.model_selector import ModelSelector

        reg = ModelRegistry()
        reg.update("test-provider", [ModelInfo(name="qwen3:8b", provider="test-provider")])
        sel = ModelSelector(ModelService({"llm": {"primary_model": "not-installed-model"}}, reg))
        with pytest.raises(ModelUnavailableError) as exc_info:
            sel.resolve()
        assert "workload" not in str(exc_info.value).lower()


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

    def test_failure_requires_explicit_new_tool_call(self, executor, lesson_store):
        tr = executor.execute("fail", {})
        assert tr.success is False
        assert tr.error == "Error: boom"
        assert len(lesson_store.calls) == 1

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


    def test_record_tool_call_no_trace(self):
        # Must not raise
        ToolExecutor.record_tool_call(None, 0, "echo", {}, "", 0, True, trace=None)
