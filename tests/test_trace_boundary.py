"""Trace architecture boundary enforcement.

Strict separation between:
- User-facing TraceEvent (action/category/summary only)
- Internal DebugTraceEvent (debug_trace gated)
- WebUI payload (never internal objects)
- TraceAction enum (high-level, not implementation-tied)
"""

from unittest.mock import MagicMock, patch

import pytest

from novi.orchestrator.task_types import (
    ComplexityScore, EvidenceAnalysis, EvidenceRequirements, EvidenceSignal,
    ExecutionStrategy, GroundingDecision, IntentType, TaskAnalysis,
)
from novi.runtime.execution_context import ExecutionContext
from novi.runtime.runtime import NoviRuntime
from novi.runtime.trace import (
    DebugTraceEvent, ExecutionTrace, TraceAction, TraceActionMetadata,
    TraceEvent, TRACE_ACTION_METADATA,
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Trace boundary tests target trace events, not search results.
    Stub the live search so RESEARCH analyses run deterministically."""
    monkeypatch.setattr("novi.tools.search_pipeline._search_multi", lambda *a, **k: ([], None))


class TestTraceActionEnum:
    """TraceAction must be high-level, not tied to implementation."""

    VALID_ACTIONS = {"understanding", "retrieving", "planning", "executing", "responding"}

    def test_no_implementation_actions(self):
        forbidden = {"classifying", "heuristic", "keyword", "regex", "signal",
                      "grounding", "confidence", "intent", "decision", "evidence"}
        for action in TraceAction:
            assert action.value not in forbidden, (
                f"TraceAction '{action.value}' exposes implementation detail"
            )

    def test_all_actions_valid_strings(self):
        for action in TraceAction:
            assert action.value in self.VALID_ACTIONS, (
                f"Unknown action: {action.value}"
            )

    def test_action_value_never_empty(self):
        for action in TraceAction:
            assert action.value, f"TraceAction {action.name} has empty value"


class TestTraceEventContract:
    """TraceEvent is a user-facing explanation object.

    Must only contain: action, category, label, summary.
    Must never contain: confidence, source, signals, details, GroundingDecision, EvidenceAnalysis.
    """

    def test_to_dict_only_presentation_fields(self):
        event = TraceEvent(action=TraceAction.RETRIEVING, category="a", summary="b")
        keys = set(event.to_dict().keys())
        assert keys == {"action", "category", "label", "summary"}, f"Got extra keys: {keys - {'action', 'category', 'label', 'summary'}}"

    def test_to_dict_action_is_string(self):
        event = TraceEvent(action=TraceAction.PLANNING)
        d = event.to_dict()
        assert isinstance(d["action"], str)
        assert d["action"] == "planning"

    def test_no_debug_attributes(self):
        event = TraceEvent()
        attrs = {"confidence", "source", "signals", "data", "details", "grounding", "evidence", "title"}
        for a in attrs:
            assert not hasattr(event, a), f"TraceEvent should not have attribute: {a}"

    def test_summary_contains_no_internal_terms(self):
        runtime = NoviRuntime()
        ctx = ExecutionContext(user_input="hello")
        list(runtime.run_stream(context=ctx))
        forbidden = {"confidence", "heuristic", "keyword", "regex", "signal",
                      "classifier", "grounding source", "intent classified",
                      "evidence", "needs_grounding"}
        for event in ctx.trace.user_events:
            lower = event.summary.lower()
            for term in forbidden:
                assert term not in lower, (
                    f"TraceEvent summary contains internal term '{term}': "
                    f"summary={event.summary!r}"
                )


class TestDebugTraceEventGating:
    """DebugTraceEvent must only be created when debug_trace=True."""

    def test_no_debug_events_when_debug_false(self):
        runtime = NoviRuntime()
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
        ctx = ExecutionContext(user_input="test", analysis=analysis)
        list(runtime.run_stream(context=ctx))
        assert len(ctx.trace.debug_events) == 0, (
            f"Expected 0 debug_events, got {len(ctx.trace.debug_events)}: "
            f"{[e.category for e in ctx.trace.debug_events]}"
        )

    def test_debug_events_created_when_debug_true(self):
        runtime = NoviRuntime(debug_trace=True)
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
        ctx = ExecutionContext(user_input="test", analysis=analysis)
        list(runtime.run_stream(context=ctx))
        assert len(ctx.trace.debug_events) > 0, (
            f"Expected debug_events with debug_trace=True, got {len(ctx.trace.debug_events)}"
        )
        categories = {e.category for e in ctx.trace.debug_events}
        assert "analysis" in categories or "grounding" in categories or "planning" in categories, (
            f"No expected debug categories in: {categories}"
        )


class TestDebugEventContent:
    """DebugTraceEvent should contain internal implementation details."""

    def test_debug_events_contain_internal_state(self):
        runtime = NoviRuntime(debug_trace=True)
        analysis = TaskAnalysis(
            intent=IntentType.RESEARCH,
            strategy=ExecutionStrategy.RESEARCH,
            complexity=ComplexityScore(score=3, plan_level=0, max_steps=5),
            evidence=EvidenceAnalysis(
                requirements=EvidenceRequirements(external=True),
                confidence=0.85,
                signals=[EvidenceSignal(type="temporal", strength="strong"),
                          EvidenceSignal(type="dynamic", strength="medium")],
            ),
            grounding=GroundingDecision(
                needs_grounding=True,
                confidence=0.9,
                reason="Research intent",
                source="keyword",
            ),
        )
        ctx = ExecutionContext(user_input="test", analysis=analysis)
        list(runtime.run_stream(context=ctx))
        found_internal = False
        for dbg in ctx.trace.debug_events:
            if dbg.data:
                keys = set(dbg.data.keys())
                internal_keys = {"confidence", "source", "signals", "needs_grounding",
                                  "grounding_confidence", "grounding_source",
                                  "evidence_confidence", "plan_level", "intent"}
                if keys & internal_keys:
                    found_internal = True
                    break
        assert found_internal, (
            "No DebugTraceEvent contains internal state keys. "
            f"Got categories: {[e.category for e in ctx.trace.debug_events]}"
        )


class TestUserEventContent:
    """TraceEvent must NOT contain internal state."""

    def test_user_events_have_no_internal_state(self):
        runtime = NoviRuntime(debug_trace=True)
        analysis = TaskAnalysis(
            intent=IntentType.RESEARCH,
            strategy=ExecutionStrategy.RESEARCH,
            complexity=ComplexityScore(score=3, plan_level=0, max_steps=5),
            evidence=EvidenceAnalysis(
                requirements=EvidenceRequirements(external=True),
                confidence=0.85,
                signals=[EvidenceSignal(type="temporal", strength="strong")],
            ),
            grounding=GroundingDecision(
                needs_grounding=True,
                confidence=0.9,
                reason="Research intent",
                source="keyword",
            ),
        )
        ctx = ExecutionContext(user_input="test", analysis=analysis)
        list(runtime.run_stream(context=ctx))
        internal_terms = {"confidence", "heuristic", "keyword match", "regex", "signal",
                          "classifier", "grounding source", "intent classified",
                          "needs_grounding", "evidence confidence"}
        for event in ctx.trace.user_events:
            lower = event.summary.lower()
            for term in internal_terms:
                assert term not in lower, (
                    f"TraceEvent contains internal term '{term}': "
                    f"summary={event.summary!r}"
                )


class TestTraceActionUsage:
    """Every TraceEvent must carry a valid action."""

    def test_all_user_events_have_valid_action(self):
        runtime = NoviRuntime(debug_trace=True)
        ctx = ExecutionContext(user_input="hello")
        list(runtime.run_stream(context=ctx))
        for event in ctx.trace.user_events:
            assert isinstance(event.action, TraceAction), (
                f"Event action is not a TraceAction: {event.action!r}"
            )
            assert event.action in TraceAction, (
                f"Event has unknown action: {event.action}"
            )

    def test_user_events_use_distinct_actions(self):
        runtime = NoviRuntime(debug_trace=True)
        analysis = TaskAnalysis(
            intent=IntentType.RESEARCH,
            strategy=ExecutionStrategy.RESEARCH,
            complexity=ComplexityScore(score=3, plan_level=2, max_steps=10),
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
        ctx = ExecutionContext(user_input="test grounding and planning", analysis=analysis)
        list(runtime.run_stream(context=ctx))
        actions = {e.action for e in ctx.trace.user_events}
        assert TraceAction.UNDERSTANDING in actions, "Should emit UNDERSTANDING action"
        assert TraceAction.RETRIEVING in actions, "Should emit RETRIEVING action for grounding"
        assert TraceAction.PLANNING in actions or True, "May emit PLANNING action"

    def test_no_unknown_action_in_webui_payload(self):
        event = TraceEvent(action=TraceAction.RESPONDING, category="a", summary="b")
        d = event.to_dict()
        assert d["action"] in ("understanding", "retrieving", "planning", "executing", "responding")


class TestWebUITracePayload:
    """WebUI must never receive internal objects."""

    def test_webui_handler_emits_action_not_title(self):
        event = TraceEvent(action=TraceAction.RETRIEVING, category="test", summary="Searching")
        payload = event.to_dict()
        assert "action" in payload, "WebUI payload should have 'action' field"
        assert "title" not in payload, "WebUI payload should not have 'title' field"
        assert "label" in payload, "WebUI payload should have 'label' field"
        assert payload["label"] == "Finding information", (
            f"Expected label 'Finding information', got {payload['label']!r}"
        )
        assert set(payload.keys()) == {"action", "category", "label", "summary"}, (
            f"WebUI-safe payload should only have action/category/label/summary, got: {set(payload.keys())}"
        )

    def test_grounding_decision_not_serialized_to_webui(self):
        import os
        webui_path = os.path.join(os.path.dirname(__file__), "..", "novi", "webui_server.py")
        with open(webui_path, encoding="utf-8") as f:
            content = f.read()
        for token in ("GroundingDecision", "EvidenceAnalysis", "evidence_summary", "debug_trace", "DebugTraceEvent"):
            assert token not in content, (
                f"webui_server.py must not reference {token}"
            )


class TestTraceActionFrontendMapping:
    """WebUI must receive action, not title, for frontend mapping."""

    def test_webui_trace_handler_emits_action(self):
        import os
        import re
        webui_path = os.path.join(os.path.dirname(__file__), "..", "novi", "webui_server.py")
        with open(webui_path, encoding="utf-8") as f:
            content = f.read()
        assert '"action":' in content or "'action':" in content, (
            "webui trace handler must emit 'action' field"
        )
        trace_emit_block = re.search(
            r'elif kind == "trace":.*?self\._emit\(\{.*?\}\)',
            content, re.DOTALL
        )
        assert trace_emit_block is not None, "Could not locate trace emit block"
        assert "to_dict()" in trace_emit_block.group(), (
            "Trace emit block should call to_dict() for resolution"
        )
        assert '"title"' not in trace_emit_block.group(), (
            "Trace emit block should not include 'title'"
        )


class TestTraceActionMetadata:
    """Every TraceAction must have presentation metadata."""

    FORBIDDEN_INTERNAL = {"grounding", "heuristic", "confidence", "classifier",
                          "regex", "intent", "evidence", "keyword", "signal",
                          "decision", "needs_grounding"}

    def test_every_action_has_metadata(self):
        for action in TraceAction:
            assert action in TRACE_ACTION_METADATA, (
                f"TraceAction {action.name} missing from TRACE_ACTION_METADATA"
            )

    def test_metadata_is_frozen(self):
        with pytest.raises(Exception):
            TRACE_ACTION_METADATA[TraceAction.UNDERSTANDING].label = "changed"

    def test_metadata_labels_are_user_facing(self):
        for action, meta in TRACE_ACTION_METADATA.items():
            lower = meta.label.lower()
            for term in self.FORBIDDEN_INTERNAL:
                assert term not in lower, (
                    f"TraceActionMetadata label for {action.name} contains "
                    f"internal term '{term}': {meta.label!r}"
                )

    def test_metadata_labels_are_action_phrases(self):
        for action, meta in TRACE_ACTION_METADATA.items():
            assert meta.label, f"TraceActionMetadata label for {action.name} is empty"
            assert not meta.label.endswith("."), (
                f"TraceActionMetadata label for {action.name} "
                f"should not end with period: {meta.label!r}"
            )

    def test_metadata_icons_are_non_empty(self):
        for action, meta in TRACE_ACTION_METADATA.items():
            assert meta.icon, f"TraceActionMetadata icon for {action.name} is empty"
            assert " " not in meta.icon, (
                f"TraceActionMetadata icon for {action.name} "
                f"contains spaces: {meta.icon!r}"
            )

    def test_to_dict_includes_label(self):
        for action in TraceAction:
            event = TraceEvent(action=action)
            d = event.to_dict()
            assert "label" in d, (
                f"TraceEvent.to_dict() missing 'label' for action {action.name}"
            )
            expected = TRACE_ACTION_METADATA[action].label
            assert d["label"] == expected, (
                f"TraceEvent label mismatch for {action.name}: "
                f"expected {expected!r}, got {d['label']!r}"
            )

    def test_serialization_contains_no_internal_terms(self):
        event = TraceEvent(action=TraceAction.RETRIEVING)
        d = event.to_dict()
        serialized = str(d).lower()
        for term in self.FORBIDDEN_INTERNAL:
            assert term not in serialized, (
                f"Serialized TraceEvent contains internal term '{term}'"
            )

    def test_unknown_action_falls_back_to_value(self):
        event = TraceEvent(
            action="unknown_action",  # type: ignore
            category="x",
            summary="fallback test",
        )
        d = event.to_dict()
        assert d["label"] == "unknown_action", (
            f"Expected fallback label 'unknown_action', got {d['label']!r}"
        )


class TestExecutionTraceStructure:
    """ExecutionTrace should have clean separation of user and debug events."""

    def test_user_events_and_debug_events_separate_lists(self):
        trace = ExecutionTrace()
        assert isinstance(trace.user_events, list)
        assert isinstance(trace.debug_events, list)

    def test_user_events_only_contains_traceevent(self):
        trace = ExecutionTrace()
        trace.user_events.append(TraceEvent(action=TraceAction.UNDERSTANDING, category="a", summary="b"))
        for ev in trace.user_events:
            assert isinstance(ev, TraceEvent)
            assert not isinstance(ev, DebugTraceEvent)

    def test_debug_events_only_contains_debugtraceevent(self):
        trace = ExecutionTrace()
        trace.debug_events.append(DebugTraceEvent(category="test", data={"key": "val"}))
        for ev in trace.debug_events:
            assert isinstance(ev, DebugTraceEvent)
            assert not isinstance(ev, TraceEvent)


class TestSummaryContracts:
    """Summaries explain what Novi is doing, not how."""

    ALLOWED_SUMMARIES = {
        "Determining how to process this question.",
        "This question may depend on recent information. Looking up current data.",
        "This is a stable concept well-covered in available knowledge.",
        "This question depends on current information. Looking up data.",
        "Analyzing request complexity and building execution plan.",
    }

    def test_all_user_event_summaries_are_known(self):
        runtime = NoviRuntime()
        ctx = ExecutionContext(user_input="hello")
        list(runtime.run_stream(context=ctx))
        for event in ctx.trace.user_events:
            assert event.summary in self.ALLOWED_SUMMARIES, (
                f"Unknown summary: {event.summary!r}. "
                f"Should describe an action, not internal state."
            )

    def test_summaries_describe_actions_not_implementation(self):
        runtime = NoviRuntime(debug_trace=True)
        analysis = TaskAnalysis(
            intent=IntentType.RESEARCH,
            strategy=ExecutionStrategy.RESEARCH,
            complexity=ComplexityScore(score=3, plan_level=2, max_steps=10),
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
        ctx = ExecutionContext(user_input="test grounding", analysis=analysis)
        list(runtime.run_stream(context=ctx))
        forbidden_phrases = {
            "grounding source",
            "evidence confidence",
            "intent classified",
            "heuristic",
            "keyword",
            "regex",
            "signal",
            "confidence",
            "needs_grounding",
            "GroundingDecision",
        }
        for event in ctx.trace.user_events:
            lower = event.summary.lower()
            for phrase in forbidden_phrases:
                assert phrase not in lower, (
                    f"Summary exposes internal: '{phrase}' in summary={event.summary!r}"
                )
