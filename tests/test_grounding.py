"""Tests for GroundingDecision + 3-tier grounding pipeline."""

from unittest.mock import MagicMock

import pytest

from cozmo.orchestrator.task_types import (
    GroundingDecision,
    EvidenceAnalysis,
    EvidenceRequirements,
    EvidenceSignal,
    IntentType,
    ComplexityScore,
    TaskAnalysis,
    ExecutionStrategy,
)
from cozmo.orchestrator.evidence import EvidenceDetector
from cozmo.orchestrator.orchestrator import Orchestrator
from cozmo.capabilities import CapabilityRegistry
from cozmo.capabilities.builtin import register_builtin_capabilities


# ── GroundingDecision dataclass ──────────────────────────────────────────────


class TestGroundingDecision:
    def test_default_construction(self):
        d = GroundingDecision()
        assert d.needs_grounding is False
        assert d.confidence == 0.0
        assert d.reason == ""
        assert d.source == ""

    @pytest.mark.parametrize(
        "source,needs_grounding,confidence,reason",
        [
            ("keyword", True, 1.0, "Intent classified as research"),
            ("heuristic", True, 0.85, "temporal (high): matched 'latest'"),
            ("llm", True, 0.72, "Question depends on current game meta"),
            ("none", False, 0.0, ""),
        ],
    )
    def test_construction_with_source(self, source, needs_grounding, confidence, reason):
        d = GroundingDecision(
            needs_grounding=needs_grounding,
            confidence=confidence,
            reason=reason,
            source=source,
        )
        assert d.needs_grounding is needs_grounding
        assert d.confidence == confidence
        assert d.reason == reason
        assert d.source == source


# ── EvidenceDetector pattern detection ───────────────────────────────────────


class TestEvidencePatterns:
    """Pattern fixes: standalone 'next', 'best PvE build', gaming patterns."""

    def make_detector(self):
        return EvidenceDetector()

    @pytest.mark.parametrize(
        "query,expected_signal",
        [
            ("Who is the next Wuthering Waves character?", "temporal"),
            ("When is the next update for Genshin Impact?", "dynamic"),
            ("What is the best PvE build in Shindo Life?", "dynamic"),
            ("Should I summon the next Wuthering Waves character?", "dynamic"),
            ("Best character tier list 2026", "dynamic"),
            ("Is the RTX 5090 worth buying?", "dynamic"),
        ],
    )
    def test_signal_detected(self, query, expected_signal):
        ed = self.make_detector()
        analysis = ed.detect(query)
        types = {s.type for s in analysis.signals}
        assert expected_signal in types, f"Expected {expected_signal} signal, got {types}"
        assert analysis.requirements.external is True

    @pytest.mark.parametrize(
        "query",
        [
            "What is recursion?",
            "Explain Python decorators",
        ],
    )
    def test_no_external_signals(self, query):
        """Timeless questions should produce no external signals."""
        ed = self.make_detector()
        analysis = ed.detect(query)
        assert analysis.requirements.external is False
        assert analysis.confidence == 0.0


# ── GroundingReasoner LLM parsing ────────────────────────────────────────────


class TestGroundingReasoner:
    def test_llm_returns_grounding_true(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = (
            '{"needs_grounding": true, "confidence": 0.85, "reason": "depends on upcoming release"}'
        )
        ed = EvidenceDetector(llm=mock_llm)
        result = ed.grounding_reasoner("Who is the next character?")
        assert result is not None
        assert result.needs_grounding is True
        assert result.confidence == 0.85
        assert result.source == "llm"

    def test_llm_returns_grounding_false(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = (
            '{"needs_grounding": false, "confidence": 0.95, "reason": "timeless concept"}'
        )
        ed = EvidenceDetector(llm=mock_llm)
        result = ed.grounding_reasoner("What is recursion?")
        assert result is not None
        assert result.needs_grounding is False
        assert result.confidence == 0.95

    def test_llm_returns_none_on_error(self):
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("LLM down")
        ed = EvidenceDetector(llm=mock_llm)
        result = ed.grounding_reasoner("Who is the next character?")
        assert result is None

    def test_llm_returns_none_when_unavailable(self):
        ed = EvidenceDetector()  # no llm
        result = ed.grounding_reasoner("Who is the next character?")
        assert result is None

    def test_llm_handles_markdown_wrapped_json(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = (
            '```json\n{"needs_grounding": true, "confidence": 0.9, "reason": "testing"}\n```'
        )
        ed = EvidenceDetector(llm=mock_llm)
        result = ed.grounding_reasoner("test")
        assert result is not None
        assert result.needs_grounding is True

    def test_llm_clamps_confidence(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = (
            '{"needs_grounding": true, "confidence": 1.5, "reason": "overconfident"}'
        )
        ed = EvidenceDetector(llm=mock_llm)
        result = ed.grounding_reasoner("test")
        assert result.confidence == 1.0


# ── Orchestrator._resolve_grounding ──────────────────────────────────────────


class TestResolveGrounding:
    # The orchestrator needs a capability_registry for construction,
    # but _resolve_grounding doesn't use it.
    @pytest.fixture
    def orch(self, orch_factory):
        return orch_factory()

    def test_research_intent_keyword_path(self, orch):
        """RESEARCH intent → keyword path → needs_grounding=true."""
        evidence = EvidenceAnalysis()  # no signals
        grounding = orch._resolve_grounding(
            IntentType.RESEARCH, evidence, "latest news"
        )
        assert grounding.needs_grounding is True
        assert grounding.source == "keyword"
        assert grounding.confidence == 1.0

    def test_heuristic_high_confidence_path(self, orch):
        """Strong signals (conf>=0.7, external) → heuristic path."""
        evidence = EvidenceAnalysis(
            requirements=EvidenceRequirements(external=True),
            confidence=0.80,
            signals=[EvidenceSignal(type="temporal", strength="high", detail="matched 'latest'")],
            reasons=["temporal (high): matched 'latest'"],
        )
        grounding = orch._resolve_grounding(
            IntentType.CONVERSATION, evidence, "latest news"
        )
        assert grounding.needs_grounding is True
        assert grounding.source == "heuristic"
        assert grounding.confidence == 0.80

    def test_heuristic_not_external_no_grounding(self, orch):
        """High confidence but not external → no grounding."""
        evidence = EvidenceAnalysis(
            requirements=EvidenceRequirements(external=False),
            confidence=0.80,
        )
        grounding = orch._resolve_grounding(
            IntentType.CONVERSATION, evidence, "something confident but internal"
        )
        assert grounding.needs_grounding is False
        assert grounding.source == "none"

    def test_llm_path_called_for_medium_confidence(self, orch):
        """Medium confidence (0 < conf < 0.7) → LLM path."""
        # Set up mock on evidence_detector
        mock_llm = MagicMock()
        orch.evidence_detector.llm = mock_llm
        mock_llm.invoke.return_value = (
            '{"needs_grounding": true, "confidence": 0.65, "reason": "game build changes with meta"}'
        )
        evidence = EvidenceAnalysis(
            requirements=EvidenceRequirements(external=True),
            confidence=0.40,
            signals=[EvidenceSignal(type="comparative", strength="medium", detail="matched 'best'")],
        )
        grounding = orch._resolve_grounding(
            IntentType.CONVERSATION, evidence, "Best build in Shindo Life?"
        )
        assert grounding.needs_grounding is True
        assert grounding.source == "llm"
        assert grounding.confidence == 0.65
        mock_llm.invoke.assert_called_once()

    def test_llm_path_fallback_when_unavailable(self, orch):
        """Medium confidence but LLM unavailable → graceful heuristic fallback."""
        orch.evidence_detector.llm = None
        evidence = EvidenceAnalysis(
            requirements=EvidenceRequirements(external=True),
            confidence=0.40,
        )
        grounding = orch._resolve_grounding(
            IntentType.CONVERSATION, evidence, "Best build?"
        )
        assert grounding.needs_grounding is False
        assert grounding.source == "heuristic"

    def test_no_signals_path(self, orch):
        """Zero confidence → none path → no grounding."""
        evidence = EvidenceAnalysis()  # empty, confidence=0.0
        grounding = orch._resolve_grounding(
            IntentType.CONVERSATION, evidence, "What is recursion?"
        )
        assert grounding.needs_grounding is False
        assert grounding.source == "none"
        assert grounding.confidence == 0.0


# ── TaskAnalysis.grounding integration ────────────────────────────────────────


class TestTaskAnalysisGrounding:
    def test_grounding_populated_in_analysis(self, orch_factory):
        """TaskAnalysis.analyze() should populate grounding field."""
        orch = orch_factory()
        analysis = orch.analyze("Who is the next Wuthering Waves character?")
        assert analysis.grounding is not None
        assert isinstance(analysis.grounding, GroundingDecision)

    def test_best_pve_build_grounding_true(self, orch_factory):
        """Regression: 'best PvE build' → grounding=true."""
        orch = orch_factory()
        analysis = orch.analyze("What is the best PvE build in Shindo Life?")
        # 'best' → comparative(medium, 0.40)
        # 'build' in dynamic pattern with 'for' → dynamic(medium, 0.40)
        # Wait: \b(loadout|build|spec|class|meta)\s+(for|in|guide)\b
        # "build in Shindo Life" → 'build' then '\s+' then 'in' → matches!
        # So dynamic(medium, 0.40) fires.
        # comparative(medium, 0.40) + dynamic(medium, 0.40) = 0.40 + 0.40 = 0.80
        # 0.80 >= 0.7 AND external=True → heuristic path → grounding=true
        assert analysis.grounding.needs_grounding is True
        assert analysis.grounding.source in ("heuristic", "llm")

    def test_timeless_question_grounding_false(self, orch_factory):
        """Regression: 'what is recursion' → grounding=false."""
        orch = orch_factory()
        analysis = orch.analyze("What is recursion?")
        assert analysis.grounding.needs_grounding is False
        assert analysis.grounding.source == "none"

    def test_explain_python_decorators_grounding_false(self, orch_factory):
        """Regression: 'explain Python decorators' → grounding=false."""
        orch = orch_factory()
        analysis = orch.analyze("Explain Python decorators")
        assert analysis.grounding.needs_grounding is False
        assert analysis.grounding.source == "none"

    def test_latest_ollama_release_external(self, orch_factory):
        """'latest Ollama release' → grounding=true via heuristic."""
        orch = orch_factory()
        analysis = orch.analyze("Latest Ollama release?")
        # 'latest' → temporal(high, 0.70) → heuristic fast path
        assert analysis.grounding.needs_grounding is True
        assert analysis.grounding.source == "heuristic"


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def orch_factory():
    """Factory for a fresh Orchestrator with builtin capabilities."""

    def _make():
        registry = CapabilityRegistry()
        register_builtin_capabilities(registry)
        return Orchestrator(capability_registry=registry)

    return _make


@pytest.fixture
def orch_with_search_cap(orch_factory):
    """Orchestrator with mock LLM to exercise LLM grounding path."""
    return orch_factory()


# ── GroundingDecision on TaskAnalysis ────────────────────────────────────────


class TestTaskAnalysisField:
    def test_default_grounding_on_task_analysis(self):
        analysis = TaskAnalysis()
        assert analysis.grounding is not None
        assert analysis.grounding.needs_grounding is False

    def test_grounding_custom_on_task_analysis(self):
        analysis = TaskAnalysis(
            grounding=GroundingDecision(
                needs_grounding=True,
                confidence=0.9,
                reason="needs current info",
                source="heuristic",
            )
        )
        assert analysis.grounding.needs_grounding is True
        assert analysis.grounding.source == "heuristic"

    def test_grounding_in_dict_serialization(self):
        # Test that grounding is accessible when traversing plan context
        analysis = TaskAnalysis(
            grounding=GroundingDecision(
                needs_grounding=True,
                confidence=0.95,
                reason="Research intent",
                source="keyword",
            )
        )
        d = {
            "intent": analysis.intent.value,
            "grounding": {
                "needs_grounding": analysis.grounding.needs_grounding,
                "confidence": analysis.grounding.confidence,
                "source": analysis.grounding.source,
            },
        }
        assert d["grounding"]["needs_grounding"] is True
        assert d["grounding"]["source"] == "keyword"


# ── Runtime prompt changes ───────────────────────────────────────────────────


class TestEvidencePriorityPrompt:
    def test_base_prompt_has_evidence_priority(self):
        """prompts.py BASE_PROMPT should include evidence priority instruction."""
        from cozmo.runtime.prompts import BASE_PROMPT

        assert "primary source" in BASE_PROMPT.lower()
        assert "supplements" in BASE_PROMPT.lower()

    def test_system_prompt_has_priority_when_grounding(self):
        """When grounding_text present, system prompt has priority instruction."""
        from cozmo.runtime.runtime import CozmoRuntime

        rt = CozmoRuntime()
        prompt = rt._system_prompt(
            user_input="test",
            grounding="search result content here",
        )
        assert "primary source" in prompt.lower()
        assert "prioritize" in prompt.lower()
        assert "supplement" in prompt.lower()

    def test_system_prompt_without_grounding(self):
        """Without grounding_text, identity has evidence priority but no grounding section."""
        from cozmo.runtime.runtime import CozmoRuntime

        rt = CozmoRuntime()
        prompt = rt._system_prompt(user_input="test")
        # Identity always has the general instruction
        assert "primary source" in prompt.lower()
        # But no grounding-specific injection
        assert "Search results for the user's question" not in prompt
