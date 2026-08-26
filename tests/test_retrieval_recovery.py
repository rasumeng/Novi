"""Phase 9 step 7 — recovery ownership lives on RetrievalExecutor.

Verifies the executor produces structured recovery decisions/state and that
runtime's three former recovery sites are driven by executor recommendations.
No runtime, no live stores, no network.
"""

from __future__ import annotations

import types

from novi.runtime.evidence import RetrievalQuality
from novi.runtime.execution_context import ExecutionContext
from novi.runtime.retrieval import (
    RecoveryAction,
    RecoveryDecision,
    RetrievalExecutor,
    RetrievalRecoveryState,
)
from novi.runtime.retrieval_policy import RetrievalPlan, RetrievalStrategy
from novi.runtime.trace import ExecutionTrace


def _plan(strategy: RetrievalStrategy) -> RetrievalPlan:
    return RetrievalPlan(strategy=strategy)


def _analysis(intent="conversation", strategy=RetrievalStrategy.NONE,
              needs_grounding=False, needs_memory=False, has_plan=True):
    plan = _plan(strategy) if has_plan else None
    return types.SimpleNamespace(
        intent=types.SimpleNamespace(value=intent),
        capabilities=[intent],
        evidence=types.SimpleNamespace(
            signals=[], confidence=0.0, needs_memory=needs_memory
        ),
        grounding=types.SimpleNamespace(
            needs_grounding=needs_grounding, confidence=0.0, source="none", reason=""
        ),
        retrieval_plan=plan,
        complexity=types.SimpleNamespace(score=1, plan_level=0, max_steps=5),
        strategy=types.SimpleNamespace(value="direct"),
    )


def _ctx(quality="", strategy=RetrievalStrategy.NONE, needs_grounding=False,
         has_plan=True, intent="conversation"):
    ctx = ExecutionContext(user_input="question")
    ctx.trace = ExecutionTrace(user_input="question")
    ctx.analysis = _analysis(intent=intent, strategy=strategy,
                             needs_grounding=needs_grounding, has_plan=has_plan)
    ctx.grounding_quality = quality
    return ctx


class TestRecoveryState:
    def test_retry_available_tracks_attempts(self):
        s = RetrievalRecoveryState()
        assert s.retry_available is True
        s.attempts_used = 1
        assert s.retry_available is False

    def test_init_recovery_populates_quality(self):
        exe = RetrievalExecutor()
        ctx = _ctx(quality="weak")
        state = exe.init_recovery(ctx)
        assert ctx.retrieval_recovery is state
        assert state.quality == "weak"
        assert state.retry_available is True


class TestPlanRequiresWeb:
    def test_web_strategies_require_web(self):
        exe = RetrievalExecutor()
        for s in (RetrievalStrategy.WEB_ONLY, RetrievalStrategy.KNOWLEDGE_THEN_WEB,
                  RetrievalStrategy.MEMORY_FIRST):
            assert exe.plan_requires_web(_plan(s)) is True, s

    def test_non_web_strategies_do_not(self):
        exe = RetrievalExecutor()
        for s in (RetrievalStrategy.NONE, RetrievalStrategy.KNOWLEDGE_ONLY,
                  RetrievalStrategy.PROJECT_FIRST):
            assert exe.plan_requires_web(_plan(s)) is False, s

    def test_none_plan(self):
        assert RetrievalExecutor.plan_requires_web(None) is False


class TestRecommendPreLoop:
    def test_plan_requires_web_gives_upgrade(self):
        exe = RetrievalExecutor()
        ctx = _ctx(strategy=RetrievalStrategy.WEB_ONLY)
        d = exe.recommend_pre_loop(ctx)
        assert d.action == RecoveryAction.UPGRADE_SEARCH
        assert d.commit_on_grant is True
        assert "requires web" in d.reason

    def test_quality_insufficient_gives_upgrade(self):
        exe = RetrievalExecutor()
        ctx = _ctx(quality="weak")
        d = exe.recommend_pre_loop(ctx)
        assert d.action == RecoveryAction.UPGRADE_SEARCH
        assert d.commit_on_grant is False
        assert "quality=weak" in d.reason

    def test_sufficient_quality_no_upgrade(self):
        exe = RetrievalExecutor()
        ctx = _ctx(quality=RetrievalQuality.SUFFICIENT.value)
        assert exe.recommend_pre_loop(ctx).action == RecoveryAction.NONE

    def test_no_quality_no_upgrade(self):
        exe = RetrievalExecutor()
        ctx = _ctx(quality="")
        assert exe.recommend_pre_loop(ctx).action == RecoveryAction.NONE

    def test_quality_upgrade_consumes_retry(self):
        exe = RetrievalExecutor()
        ctx = _ctx(quality="empty")
        ctx.retrieval_recovery = RetrievalRecoveryState(attempts_used=1)
        assert exe.recommend_pre_loop(ctx).action == RecoveryAction.NONE


class TestRecommendWhenModelAnswered:
    def test_no_quality_no_recovery(self):
        exe = RetrievalExecutor()
        d = exe.recommend_when_model_answered(_ctx(quality=""))
        assert d.action == RecoveryAction.NONE
        assert "no retrieval quality recorded" in d.reason

    def test_unrecognized_quality_no_recovery(self):
        exe = RetrievalExecutor()
        d = exe.recommend_when_model_answered(_ctx(quality="garbage"))
        assert d.action == RecoveryAction.NONE
        assert "unrecognized" in d.reason

    def test_sufficient_quality_no_recovery(self):
        exe = RetrievalExecutor()
        d = exe.recommend_when_model_answered(_ctx(quality="sufficient"))
        assert d.action == RecoveryAction.NONE
        assert "sufficient" in d.reason

    def test_no_retrieval_requested_no_recovery(self):
        exe = RetrievalExecutor()
        ctx = _ctx(quality="weak", strategy=RetrievalStrategy.NONE,
                   needs_grounding=False, has_plan=False)
        d = exe.recommend_when_model_answered(ctx)
        assert d.action == RecoveryAction.NONE
        assert "no retrieval was requested" in d.reason

    def test_max_attempts_reached_no_recovery(self):
        exe = RetrievalExecutor()
        ctx = _ctx(quality="weak", strategy=RetrievalStrategy.WEB_ONLY)
        ctx.retrieval_recovery = RetrievalRecoveryState(attempts_used=1)
        d = exe.recommend_when_model_answered(ctx)
        assert d.action == RecoveryAction.NONE
        assert "max recovery attempts" in d.reason

    def test_poor_quality_upgrade(self):
        exe = RetrievalExecutor()
        ctx = _ctx(quality="weak", strategy=RetrievalStrategy.WEB_ONLY)
        d = exe.recommend_when_model_answered(ctx)
        assert d.action == RecoveryAction.UPGRADE_SEARCH
        assert "answered without tools" in d.reason


class TestRecommendAfterTool:
    def test_knowledge_empty_escalates(self):
        exe = RetrievalExecutor()
        ctx = _ctx()
        d = exe.recommend_after_tool(ctx, "search_knowledge", "[info] No matching knowledge found.")
        assert d.action == RecoveryAction.ESCALATE_WEB
        assert d.commit_on_grant is True

    def test_knowledge_nonempty_no_escalation(self):
        exe = RetrievalExecutor()
        ctx = _ctx()
        d = exe.recommend_after_tool(ctx, "search_knowledge", "- **foo** (x.py): body")
        assert d.action == RecoveryAction.NONE

    def test_other_tool_no_escalation(self):
        exe = RetrievalExecutor()
        ctx = _ctx()
        d = exe.recommend_after_tool(ctx, "read_file", "[info] No matching knowledge found.")
        assert d.action == RecoveryAction.NONE

    def test_attempts_exhausted_no_escalation(self):
        exe = RetrievalExecutor()
        ctx = _ctx()
        ctx.retrieval_recovery = RetrievalRecoveryState(attempts_used=1)
        d = exe.recommend_after_tool(ctx, "search_knowledge", "[info] No matching knowledge found.")
        assert d.action == RecoveryAction.NONE


class TestCommitRecovery:
    def test_records_state_and_trace(self):
        exe = RetrievalExecutor()
        ctx = _ctx()
        d = RecoveryDecision(action=RecoveryAction.UPGRADE_SEARCH,
                             reason="plan needs web")
        state = exe.commit_recovery(ctx, d, "upgrade_search")
        assert state.attempts_used == 1
        assert state.retry_attempted is True
        assert state.action == "upgrade_search"
        assert state.recommendation == RecoveryAction.UPGRADE_SEARCH
        assert state.reason == "plan needs web"
        assert state.retry_available is False
        assert ctx.trace.recovery_attempts == 1
        assert ctx.trace.recovery_action == "upgrade_search"

    def test_commit_blocks_further_upgrades(self):
        exe = RetrievalExecutor()
        ctx = _ctx(quality="weak", strategy=RetrievalStrategy.WEB_ONLY)
        exe.commit_recovery(ctx, RecoveryDecision(RecoveryAction.UPGRADE_SEARCH, "x"), "upgrade_search")
        assert exe.recommend_when_model_answered(ctx).action == RecoveryAction.NONE
        assert exe.recommend_after_tool(ctx, "search_knowledge", "No matching knowledge found").action == RecoveryAction.NONE


class TestExecuteInitializesState:
    def test_execute_creates_recovery_state(self):
        exe = RetrievalExecutor()
        ctx = _ctx()
        list(exe.execute(ctx, "question"))
        assert ctx.retrieval_recovery is not None
        assert ctx.retrieval_recovery.attempts_used == 0

    def test_execute_syncs_quality(self):
        exe = RetrievalExecutor()
        ctx = _ctx(quality="weak")
        list(exe.execute(ctx, "question"))
        assert ctx.retrieval_recovery.quality == "weak"


class _ScriptedRunnable:
    """Returns a fixed sequence of messages from runnable.stream()."""

    def __init__(self, responses):
        self._responses = list(responses)
        self._i = 0

    def stream(self, msgs):
        idx = min(self._i, len(self._responses) - 1)
        self._i += 1
        yield self._responses[idx]


def _runtime(model_service=None, registry=None):
    from unittest.mock import MagicMock

    from novi.runtime.runtime import NoviRuntime
    from novi.runtime.tool_registry import ToolRegistry

    reg = registry
    if reg is None:
        from novi.tools import TOOL_REGISTRY

        reg = ToolRegistry()
        for name, fn in TOOL_REGISTRY.items():
            reg.register(name, fn)
    return NoviRuntime(model_service=model_service or MagicMock(), registry=reg)


class TestRuntimeIntegration:
    def test_midloop_upgrade_when_model_answers_without_tools(self):
        """Site 2: web plan + web tools already bound + poor quality →
        executor recommends upgrade, runtime rebinds tools, trace records it."""
        from unittest.mock import MagicMock, patch

        from novi.runtime.evidence import EvidenceBundle
        from novi.runtime.runtime import NoviRuntime

        rt = _runtime()
        ctx = ExecutionContext(user_input="question")
        ctx.trace = ExecutionTrace(user_input="question")
        ctx.model_name = "test-model"
        ctx.allowed_tools = ["web_search", "web_fetch", "read_file"]
        ctx.analysis = _analysis(strategy=RetrievalStrategy.WEB_ONLY)

        empty = EvidenceBundle(query="question", results=[], source_count=0,
                               quality=RetrievalQuality.EMPTY)
        with patch("novi.runtime.evidence.EvidenceCollector.collect", return_value=empty):
            events = list(rt.run_stream(context=ctx))

        assert ctx.trace.recovery_attempts == 1
        assert ctx.trace.recovery_action == "upgrade_search"
        assert ctx.retrieval_recovery.attempts_used == 1
        assert ctx.retrieval_recovery.action == "upgrade_search"
        assert "web_search" in ctx.allowed_tools
        assert any(k == "token" for k, *_ in events)

    def test_posttool_escalation_when_knowledge_empty(self):
        """Site 3: search_knowledge returns empty in-loop → executor escalates,
        runtime binds web tools, model answers afterwards."""
        from unittest.mock import MagicMock, patch

        from langchain_core.messages import AIMessage

        rt = _runtime()
        rt.tool_executor._perm_mode = "bypass"

        ai1 = AIMessage(content='{"name": "search_knowledge", "args": {"query": "q"}}')
        ai2 = AIMessage(content="Here is my answer.")
        rt.model_service.bind_model.return_value = _ScriptedRunnable([ai1, ai2])

        ctx = ExecutionContext(user_input="question")
        ctx.trace = ExecutionTrace(user_input="question")
        ctx.model_name = "test-model"
        ctx.analysis = _analysis(strategy=RetrievalStrategy.NONE)

        class _EmptyIndex:
            def search(self, query, k=5, rerank=True):
                return []

        with patch("novi.tools.file_ops.get_knowledge_index", return_value=_EmptyIndex()):
            events = list(rt.run_stream(context=ctx))

        assert ctx.trace.recovery_attempts == 1
        assert ctx.trace.recovery_action == "post_tool_escalation"
        assert ctx.retrieval_recovery.action == "post_tool_escalation"
        finals = "".join(str(e[1]) for e in events if e[0] == "token")
        assert "answer" in finals

    def test_preloop_plan_upgrade_grants_search_tools(self):
        """Site 1: web plan + web tools missing → tools granted before loop."""
        from unittest.mock import MagicMock, patch

        from novi.runtime.evidence import EvidenceBundle

        rt = _runtime()
        ctx = ExecutionContext(user_input="question")
        ctx.trace = ExecutionTrace(user_input="question")
        ctx.model_name = "test-model"
        ctx.allowed_tools = ["read_file"]
        ctx.analysis = _analysis(strategy=RetrievalStrategy.WEB_ONLY)

        empty = EvidenceBundle(query="question", results=[], source_count=0,
                               quality=RetrievalQuality.EMPTY)
        with patch("novi.runtime.evidence.EvidenceCollector.collect", return_value=empty):
            events = list(rt.run_stream(context=ctx))

        assert "web_search" in ctx.allowed_tools
        assert "web_fetch" in ctx.allowed_tools
        assert ctx.trace.recovery_attempts == 1
        assert ctx.trace.recovery_action == "upgrade_search"
        assert ctx.retrieval_recovery.attempts_used == 1
