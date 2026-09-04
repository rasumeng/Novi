"""Task 2.2 — Search Honest State (disabled vs no_results vs failure)."""

import types
import pytest
from unittest.mock import MagicMock, patch

from novi.runtime.evidence import EvidenceBundle, RetrievalQuality
from novi.runtime.execution_context import ExecutionContext
from novi.runtime.retrieval import NOT_CONFIGURED_MSG, RetrievalExecutor
from novi.runtime.trace import ExecutionTrace


def _ctx_with_trace(**kwargs):
    ctx = ExecutionContext(user_input=kwargs.pop("user_input", "latest news"), **kwargs)
    ctx.trace = ExecutionTrace(user_input=ctx.user_input)
    # Minimal analysis for grounding_search path: needs_grounding True
    from novi.orchestrator.task_types import GroundingDecision, EvidenceAnalysis, TaskAnalysis, IntentType, ComplexityScore, ExecutionStrategy
    if ctx.analysis is None and kwargs.get("needs_grounding", True):
        ctx.analysis = TaskAnalysis(
            intent=IntentType.RESEARCH,
            evidence=EvidenceAnalysis(signals=[], confidence=0.9, requirements=types.SimpleNamespace(external=True)),
            grounding=GroundingDecision(needs_grounding=True, confidence=0.9, reason="test", source="keyword"),
            complexity=ComplexityScore(score=3, plan_level=0, max_steps=5),
            strategy=ExecutionStrategy.RESPOND,
            capabilities=["research"],
        )
    return ctx


def _bundle(query="test", *, error=None, results=None, source_count=None, quality=None):
    # Helper to build EvidenceBundle with explicit quality/source_count
    if error is not None:
        return EvidenceBundle(query=query, error=error, quality=quality or RetrievalQuality.FAILED, source_count=source_count or 0, results=results or [])
    if results is not None:
        return EvidenceBundle(query=query, results=results, source_count=source_count if source_count is not None else len(results), quality=quality or RetrievalQuality.SUFFICIENT, merged_text="merged" if results else "")
    # empty no_results case
    return EvidenceBundle(query=query, results=[], source_count=0, quality=quality or RetrievalQuality.EMPTY, merged_text="")


class TestClassifyGroundingStatus:
    def test_not_configured_from_error_message(self):
        ex = RetrievalExecutor()
        b = _bundle(error=NOT_CONFIGURED_MSG)
        assert ex._classify_grounding_status(b) == "not_configured"
        # variant lowercases
        b2 = _bundle(error="Web search isn't configured. Connect a search provider")
        assert ex._classify_grounding_status(b2) == "not_configured"

    def test_failed_from_error(self):
        ex = RetrievalExecutor()
        b = _bundle(error="Web search failed: 500", quality=RetrievalQuality.FAILED)
        assert ex._classify_grounding_status(b) == "failed"

    def test_no_results_empty_bundle(self):
        ex = RetrievalExecutor()
        b = _bundle(quality=RetrievalQuality.EMPTY)
        assert ex._classify_grounding_status(b) == "no_results"
        b2 = EvidenceBundle(query="q", results=[], source_count=0, merged_text="", quality=RetrievalQuality.EMPTY)
        assert ex._classify_grounding_status(b2) == "no_results"

    def test_grounded_success(self):
        ex = RetrievalExecutor()
        b = _bundle(results=[types.SimpleNamespace()], source_count=2, quality=RetrievalQuality.SUFFICIENT)
        # Need real SearchResult like objects but source_count>0 and quality sufficient
        b.merged_text = "evidence"
        b.quality = RetrievalQuality.SUFFICIENT
        assert ex._classify_grounding_status(b) == "grounded"


class TestFinalizeGrounding:
    def test_finalize_sets_ctx_and_trace(self):
        ex = RetrievalExecutor()
        ctx = _ctx_with_trace(user_input="hello")
        b = _bundle(error=NOT_CONFIGURED_MSG, quality=RetrievalQuality.FAILED)
        status = ex._finalize_grounding(ctx, b)
        assert status == "not_configured"
        assert ctx.grounding_status == "not_configured"
        assert ctx.grounding_error == NOT_CONFIGURED_MSG
        assert ctx.search_error == NOT_CONFIGURED_MSG
        assert ctx.trace.grounding_status == "not_configured"
        assert ctx.trace.grounding_error == NOT_CONFIGURED_MSG
        assert ctx.trace.grounding_searched is True

    def test_finalize_failed(self):
        ex = RetrievalExecutor()
        ctx = _ctx_with_trace()
        b = _bundle(error="Web search failed with an unexpected error.", quality=RetrievalQuality.FAILED)
        status = ex._finalize_grounding(ctx, b)
        assert status == "failed"
        assert ctx.grounding_status == "failed"

    def test_finalize_no_results(self):
        ex = RetrievalExecutor()
        ctx = _ctx_with_trace()
        b = _bundle(quality=RetrievalQuality.EMPTY)
        status = ex._finalize_grounding(ctx, b)
        assert status == "no_results"
        assert ctx.grounding_status == "no_results"
        assert ctx.trace.grounding_searched is True

    def test_finalize_grounded(self):
        ex = RetrievalExecutor()
        ctx = _ctx_with_trace()
        b = _bundle(results=[types.SimpleNamespace()], source_count=1, quality=RetrievalQuality.SUFFICIENT)
        b.merged_text = "evidence text"
        b.results = [MagicMock()]
        status = ex._finalize_grounding(ctx, b)
        assert status == "grounded"
        assert ctx.grounding_status == "grounded"


class TestExecuteSearchHonest:
    def test_execute_search_not_configured_without_network(self, monkeypatch):
        ex = RetrievalExecutor()
        monkeypatch.setattr(ex, "_is_search_configured", lambda: False)
        bundle = ex.execute_search("latest AI news")
        assert bundle.error == NOT_CONFIGURED_MSG
        assert bundle.quality == RetrievalQuality.FAILED
        status = ex._classify_grounding_status(bundle)
        assert status == "not_configured"

    def test_execute_search_failed_via_collector(self, monkeypatch):
        ex = RetrievalExecutor()
        monkeypatch.setattr(ex, "_is_search_configured", lambda: True)
        # collector returns bundle with error
        fake_bundle = EvidenceBundle(query="q", error="Web search failed: 429", quality=RetrievalQuality.FAILED)
        monkeypatch.setattr(ex._web_source, "collect", lambda q, min_sources=2: fake_bundle)
        bundle = ex.execute_search("query")
        assert bundle.error == "Web search failed: 429"
        assert bundle.quality == RetrievalQuality.FAILED
        assert ex._classify_grounding_status(bundle) == "failed"

    def test_execute_search_no_results(self, monkeypatch):
        ex = RetrievalExecutor()
        monkeypatch.setattr(ex, "_is_search_configured", lambda: True)
        empty_bundle = EvidenceBundle(query="q", results=[], source_count=0, merged_text="", quality=RetrievalQuality.EMPTY)
        monkeypatch.setattr(ex._web_source, "collect", lambda q, min_sources=2: empty_bundle)
        bundle = ex.execute_search("query")
        # execute_search maps empty to EMPTY quality
        assert bundle.quality == RetrievalQuality.EMPTY
        assert ex._classify_grounding_status(bundle) == "no_results"


class TestRetrievalEmitsHonestStatus:
    def _run_grounding_search(self, ex, bundle):
        # helper to run _execute_grounding_search and collect yields
        ctx = _ctx_with_trace(user_input="latest AI news")
        with patch.object(ex, "execute_search", return_value=bundle):
            # also patch _apply_web_evidence to set grounding_text from bundle
            def fake_apply(ctx_, b_):
                ctx_.grounding_text = b_.merged_text
            with patch.object(ex, "_apply_web_evidence", side_effect=fake_apply):
                events = list(ex._execute_grounding_search(ctx, ctx.user_input))
        return ctx, events

    def test_grounding_search_emits_not_configured_status(self):
        ex = RetrievalExecutor()
        bundle = EvidenceBundle(query="q", error=NOT_CONFIGURED_MSG, quality=RetrievalQuality.FAILED, merged_text="")
        ctx, events = self._run_grounding_search(ex, bundle)
        assert ctx.grounding_status == "not_configured"
        assert ctx.grounding_text == ""  # no fake grounding
        kinds = [e[0] for e in events]
        assert "status" in kinds
        status_texts = [e[1] for e in events if e[0] == "status"]
        assert any(NOT_CONFIGURED_MSG in t for t in status_texts)

    def test_grounding_search_emits_failed_status(self):
        ex = RetrievalExecutor()
        bundle = EvidenceBundle(query="q", error="Search provider unavailable", quality=RetrievalQuality.FAILED, merged_text="")
        ctx, events = self._run_grounding_search(ex, bundle)
        assert ctx.grounding_status == "failed"
        kinds = [e[0] for e in events]
        assert "status" in kinds
        assert any("Search provider unavailable" in e[1] for e in events if e[0] == "status")

    def test_grounding_search_emits_no_results_status(self):
        ex = RetrievalExecutor()
        bundle = EvidenceBundle(query="q", results=[], source_count=0, merged_text="", quality=RetrievalQuality.EMPTY)
        ctx, events = self._run_grounding_search(ex, bundle)
        assert ctx.grounding_status == "no_results"
        kinds = [e[0] for e in events]
        assert "status" in kinds

    def test_grounding_search_grounded_no_error_status(self):
        ex = RetrievalExecutor()
        bundle = EvidenceBundle(query="q", results=[MagicMock()], source_count=1, merged_text="evidence", quality=RetrievalQuality.SUFFICIENT)
        ctx, events = self._run_grounding_search(ex, bundle)
        assert ctx.grounding_status == "grounded"
        # grounded should not emit not_configured/failed status; may emit no status at all
        status_texts = [e[1] for e in events if e[0] == "status"]
        assert not any(NOT_CONFIGURED_MSG in t for t in status_texts)
        assert not any("Search failed" in t for t in status_texts)


class TestRuntimeSystemPromptHonest:
    def test_not_configured_prompt_contains_search_disabled(self):
        from novi.runtime.runtime import NoviRuntime
        rt = NoviRuntime()
        prompt = rt._system_prompt("hello", grounding="", grounding_error=NOT_CONFIGURED_MSG, grounding_status="not_configured", search_error=NOT_CONFIGURED_MSG)
        assert "[Search disabled]" in prompt
        assert "Search not configured" in prompt
        assert "Brave API key" in prompt or "SearXNG" in prompt
        # must not contain fake grounding marker as primary source with empty grounding
        # grounding section should be the disabled message, not "Search results"
        assert "Search results (use as primary source" not in prompt

    def test_no_fake_grounding_when_disabled(self):
        from novi.runtime.runtime import NoviRuntime
        rt = NoviRuntime()
        prompt = rt._system_prompt("hello", grounding="", grounding_status="not_configured")
        assert "[Search disabled]" in prompt
        assert "Search results" not in prompt

    def test_failed_prompt_surfaced(self):
        from novi.runtime.runtime import NoviRuntime
        rt = NoviRuntime()
        prompt = rt._system_prompt("hello", grounding="", grounding_error="Web search failed: 500", grounding_status="failed", search_error="Web search failed: 500")
        assert "Search failed" in prompt
        assert "500" in prompt
        assert "Do NOT pretend" in prompt

    def test_no_results_prompt(self):
        from novi.runtime.runtime import NoviRuntime
        rt = NoviRuntime()
        prompt = rt._system_prompt("hello", grounding="", grounding_status="no_results")
        assert "no results" in prompt.lower()
        assert "[Search disabled]" not in prompt

    def test_grounded_prompt_contains_results(self):
        from novi.runtime.runtime import NoviRuntime
        rt = NoviRuntime()
        prompt = rt._system_prompt("hello", grounding="evidence text here", grounding_status="grounded")
        assert "Search results" in prompt
        assert "evidence text here" in prompt
        assert "[Search disabled]" not in prompt


class TestWebSearchServiceHonest:
    def test_is_configured_false_when_backend_empty(self, monkeypatch):
        from novi.search.service import WebSearchService
        svc = WebSearchService(config_get=lambda k, d=None: "" if k == "search.backend" else d)
        assert svc.is_configured() is False
        with pytest.raises(Exception) as ei:
            svc.create_provider()
        # NotConfiguredError message should contain expected guidance
        assert "not configured" in str(ei.value).lower() or "isn't configured" in str(ei.value).lower()

    def test_test_connection_not_configured(self):
        import asyncio
        from novi.search.service import WebSearchService
        svc = WebSearchService(config_get=lambda k, d=None: "" if k == "search.backend" else d)
        result = asyncio.run(svc.test_connection())
        assert result["state"] == "not_configured"
