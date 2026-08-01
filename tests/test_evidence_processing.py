"""Phase 7 tests — Evidence Processing contracts, pipeline, and wiring.

Covers: immutable contracts, pluggable source ranking, fact extraction
(heuristic + injected hook), conflict detection severity, context compression,
aggregate confidence, EvidenceProcessor orchestration, and the observational
ExecutionContext seam.
"""

import pytest
from datetime import datetime, timedelta, timezone

from cozmo.evidence import (
    Conflict,
    ConflictDetector,
    ConfidenceAssessor,
    ContextCompressor,
    EvidenceConfig,
    EvidenceContext,
    EvidenceProcessor,
    Fact,
    FactExtractor,
    RankingConfig,
    Source,
    SourceRanking,
)
from cozmo.evidence.conflicts import MAJOR, MINOR
from cozmo.runtime.evidence import EvidenceBundle
from cozmo.tools.search_pipeline import SearchResult

CURRENT = datetime.now(timezone.utc)


def _bundle(query="capital of France", results=None) -> EvidenceBundle:
    return EvidenceBundle(
        query=query,
        results=results or [
            SearchResult(
                title="France Facts",
                url="https://en.wikipedia.org/wiki/France",
                snippet="France is a country in western Europe.",
                full_text="The capital of France is Paris and it is a major European city. "
                "France is bordered by Spain to the south and Belgium to the northeast.",
                freshness="2025-01-15",
            ),
            SearchResult(
                title="Paris Travel Guide",
                url="https://example.com/paris",
                snippet="Paris is the largest city in France.",
                full_text="Paris is the capital and most populous city of France. "
                "The capital of France is Paris and it is a major European city.",
                freshness="2 days ago",
            ),
        ],
        merged_text="raw merged text",
        source_count=2,
    )


# ── Contracts ──────────────────────────────────────────────────────────

class TestContracts:
    def test_evidence_context_frozen(self):
        ctx = EvidenceContext(query="q")
        with pytest.raises(Exception):
            ctx.query = "other"

    def test_fact_frozen(self):
        f = Fact(statement="s")
        with pytest.raises(Exception):
            f.confidence = 0.9

    def test_source_frozen(self):
        s = Source(url="u")
        with pytest.raises(Exception):
            s.authority = 1.0

    def test_conflict_frozen(self):
        c = Conflict(severity=MAJOR)
        with pytest.raises(Exception):
            c.severity = MINOR

    def test_context_collections_are_tuples(self):
        ctx = EvidenceContext(
            query="q",
            facts=(Fact(statement="a"),),
            sources=(Source(url="u"),),
            conflicts=(Conflict(),),
        )
        assert isinstance(ctx.facts, tuple)
        assert isinstance(ctx.sources, tuple)
        assert isinstance(ctx.conflicts, tuple)

    def test_default_fallback_false(self):
        ctx = EvidenceContext(query="q")
        assert ctx.fallback is False


# ── SourceRanking ──────────────────────────────────────────────────────

class TestSourceRanking:
    def test_default_weights_rank_by_relevance_first(self):
        ranker = SourceRanking()
        sources = [
            Source(url="low", relevance=0.1, authority=0.9),
            Source(url="high", relevance=0.9, authority=0.5),
        ]
        ranked = ranker.rank(sources, RankingConfig())
        assert [s.url for s in ranked] == ["high", "low"]

    def test_authority_weight_dominates(self):
        ranker = SourceRanking()
        config = RankingConfig(weights={"relevance": 0.0, "authority": 1.0})
        sources = [
            Source(url="low", relevance=0.9, authority=0.1),
            Source(url="high", relevance=0.1, authority=0.9),
        ]
        ranked = ranker.rank(sources, config)
        assert [s.url for s in ranked] == ["high", "low"]

    def test_freshness_recency_scores(self):
        ranker = SourceRanking()
        config = RankingConfig(weights={"relevance": 0.0, "freshness": 1.0})
        sources = [
            Source(url="old", freshness=CURRENT - timedelta(days=300)),
            Source(url="new", freshness=CURRENT - timedelta(days=2)),
            Source(url="none", freshness=None),
        ]
        ranked = ranker.rank(sources, config)
        assert ranked[0].url == "new"
        assert ranked[-1].url == "none"

    def test_register_custom_scorer(self):
        ranker = SourceRanking()
        ranker.register_scorer("test_boost", lambda s, c: 5.0 if s.url == "x" else 0.0)
        config = RankingConfig(weights={"test_boost": 1.0})
        sources = [Source(url="x", relevance=0.0), Source(url="y", relevance=0.0)]
        ranked = ranker.rank(sources, config)
        assert ranked[0].url == "x"

    def test_rank_empty(self):
        assert SourceRanking().rank([], RankingConfig()) == []


# ── FactExtractor ──────────────────────────────────────────────────────

class TestFactExtractor:
    def test_heuristic_picks_query_relevant_sentences(self):
        text = (
            "The capital of France is Paris and it is a major European city. "
            "The northern lights appear in high latitude regions near the poles. "
            "France has a long coastline along the Atlantic ocean."
        )
        facts, fallback = FactExtractor().extract(text, "capital of France")
        assert fallback is False
        statements = {f.statement for f in facts}
        assert any("capital of France is Paris" in s for s in statements)
        assert not any("northern lights" in s for s in statements)
        assert all(f.confidence >= 0.5 for f in facts)

    def test_min_confidence_filters(self):
        extractor = FactExtractor(min_confidence=0.99)
        text = "The capital of France is Paris and it is a major European city."
        # Partial overlap → heuristic confidence 0.825 < 0.99 → filtered
        facts, fallback = extractor.extract(text, "capital France Paris river")
        assert fallback is True
        assert not facts

    def test_fallback_true_on_empty(self):
        facts, fallback = FactExtractor().extract("")
        assert facts == ()
        assert fallback is True

    def test_injected_classifier_confidences(self):
        def classifier(sentences, query):
            return [0.1, 0.9]

        text = (
            "First sentence that is quite long and describes nothing useful. "
            "Second sentence which is long enough and very relevant to the topic."
        )
        extractor = FactExtractor(extractor=classifier)
        facts, fallback = extractor.extract(text, "q")
        assert fallback is False
        assert len(facts) == 1
        assert facts[0].confidence == pytest.approx(0.9)

    def test_injected_classifier_categories(self):
        def classifier(sentences, query):
            return [(0.9, "location"), (0.1, "fact")]

        text = (
            "Sentence one is long enough to count as a candidate fact here. "
            "Sentence two is long enough to count as another candidate fact here."
        )
        facts, _ = FactExtractor(extractor=classifier).extract(text, "q")
        assert facts[0].category == "location"

    def test_classifier_failure_falls_back_to_heuristics(self):
        def broken(sentences, query):
            raise RuntimeError("llm down")

        text = "The capital of France is Paris and it is a major European city."
        facts, fallback = FactExtractor(extractor=broken).extract(text, "capital of France")
        assert fallback is False
        assert facts

    def test_dedup_merges_duplicate_statements(self):
        text = (
            "The capital of France is Paris and it is a major European city. "
            "The capital of France is Paris and it is a major European city."
        )
        facts, _ = FactExtractor().extract(text, "capital France Paris")
        assert len(facts) == 1


# ── ConflictDetector ───────────────────────────────────────────────────

class TestConflictDetector:
    def test_major_contradiction(self):
        facts = (
            Fact(statement="The capital of France is Paris and it is a large city.", confidence=0.9),
            Fact(statement="The capital of France is not Paris and it is in Lyon.", confidence=0.7),
        )
        conflicts = ConflictDetector().detect(facts)
        assert len(conflicts) == 1
        assert conflicts[0].severity == MAJOR
        assert conflicts[0].resolution is not None

    def test_minor_contradiction_low_overlap(self):
        facts = (
            Fact(statement="Paris is a great city.", confidence=0.9),
            Fact(statement="Paris is not great.", confidence=0.7),
        )
        conflicts = ConflictDetector().detect(facts)
        assert len(conflicts) == 1
        assert conflicts[0].severity == MINOR

    def test_same_polarity_no_conflict(self):
        facts = (
            Fact(statement="The capital of France is Paris and it is a major city.", confidence=0.9),
            Fact(statement="The capital of France is Paris and it has many museums.", confidence=0.8),
        )
        assert ConflictDetector().detect(facts) == ()

    def test_unrelated_statements_no_conflict(self):
        facts = (
            Fact(statement="The capital of France is Paris and it is a large city.", confidence=0.9),
            Fact(statement="The weather is not rainy in the desert today.", confidence=0.7),
        )
        assert ConflictDetector().detect(facts) == ()

    def test_empty_facts(self):
        assert ConflictDetector().detect(()) == ()

    def test_resolution_prefers_higher_confidence(self):
        facts = (
            Fact(statement="The capital of France is Paris and it is a large city.", confidence=0.9),
            Fact(statement="The capital of France is not Paris and it is in Lyon.", confidence=0.5),
        )
        conflict = ConflictDetector().detect(facts)[0]
        assert "Paris" in conflict.resolution


# ── ContextCompressor ──────────────────────────────────────────────────

class TestContextCompressor:
    def test_ratio_meets_40_percent_target(self):
        filler = " ".join(
            "This is filler sentence number %d that says nothing important at all." % i
            for i in range(40)
        )
        fact = Fact(
            statement="The capital of France is Paris and it is a major European city.",
            confidence=0.9,
        )
        result = ContextCompressor(budget_chars=1000).compress(
            filler, (fact,), "capital of France"
        )
        assert result.ratio >= 0.4
        assert result.summary
        assert result.kept_chars <= 1000 + 5

    def test_budget_respected(self):
        long_text = (
            "The capital of France is Paris and it is a major European city. " * 50
        )
        result = ContextCompressor(budget_chars=2000).compress(
            long_text, (), "capital of France"
        )
        assert result.summary
        assert result.kept_chars <= 2000

    def test_empty_input(self):
        result = ContextCompressor().compress("", (), "q")
        assert result.summary == ""
        assert result.ratio == 0.0


# ── ConfidenceAssessor ─────────────────────────────────────────────────

class TestConfidenceAssessor:
    def test_high_fact_confidence(self):
        facts = (Fact(statement="s", confidence=0.9),)
        sources = (Source(authority=0.8, relevance=0.9),)
        score = ConfidenceAssessor().assess(facts, sources)
        assert 0.7 <= score <= 1.0

    def test_conflict_penalty(self):
        assessor = ConfidenceAssessor()
        facts = (Fact(statement="s", confidence=0.9),)
        sources = (Source(authority=0.8, relevance=0.9),)
        base = assessor.assess(facts, sources)
        penalized = assessor.assess(
            facts, sources,
            (Conflict(statements=("a", "b"), severity=MAJOR),),
        )
        assert penalized < base
        assert 0.0 <= penalized <= 1.0

    def test_no_facts_no_sources_zero(self):
        assert ConfidenceAssessor().assess((), ()) == 0.0

    def test_sources_only_low_score(self):
        sources = (Source(authority=0.9, relevance=0.9),)
        assert ConfidenceAssessor().assess((), sources) < 0.3


# ── EvidenceProcessor ──────────────────────────────────────────────────

class TestEvidenceProcessor:
    def test_process_produces_structured_context(self):
        ctx = EvidenceProcessor().process(_bundle())
        assert isinstance(ctx, EvidenceContext)
        assert ctx.facts
        assert all(f.confidence > 0.0 for f in ctx.facts)
        assert ctx.confidence > 0.0
        assert ctx.fallback is False
        assert ctx.summary
        assert any("capital of France is Paris" in s for f in ctx.facts for s in [f.statement])

    def test_bundle_never_mutated(self):
        bundle = _bundle()
        original_text = bundle.merged_text
        original_count = bundle.source_count
        EvidenceProcessor().process(bundle)
        assert bundle.merged_text == original_text
        assert bundle.source_count == original_count

    def test_fallback_on_error_bundle(self):
        bundle = EvidenceBundle(query="q", error="HTTP 500")
        ctx = EvidenceProcessor().process(bundle)
        assert ctx.fallback is True
        assert ctx.facts == ()
        assert ctx.summary == ""

    def test_fallback_on_empty_bundle(self):
        bundle = EvidenceBundle(query="q")
        ctx = EvidenceProcessor().process(bundle)
        assert ctx.fallback is True

    def test_merges_duplicate_fact_sources(self):
        ctx = EvidenceProcessor().process(_bundle())
        paris_facts = [
            f for f in ctx.facts if "capital of France is Paris" in f.statement
        ]
        assert paris_facts
        assert len(paris_facts[0].sources) >= 2

    def test_sources_ranked_and_typed(self):
        ctx = EvidenceProcessor().process(_bundle())
        assert ctx.sources
        assert ctx.sources[0].source_type == "reference"  # wikipedia boosted
        assert all(0.0 <= s.authority <= 1.0 for s in ctx.sources)

    def test_compression_reduces_context(self):
        bundle = _bundle()
        bundle.results[0].full_text = (
            "The capital of France is Paris and it is a major European city. "
            + " ".join(
                "Unrelated filler about the weather in the mountains and rivers.%d" % i
                for i in range(30)
            )
        )
        ctx = EvidenceProcessor(EvidenceConfig(budget_chars=800)).process(bundle)
        assert ctx.fallback is False
        assert len(ctx.summary) < len(bundle.results[0].full_text)

    def test_result_frozen(self):
        ctx = EvidenceProcessor().process(_bundle())
        with pytest.raises(Exception):
            ctx.confidence = 0.1


# ── Wiring (observational seam) ────────────────────────────────────────

class TestWiring:
    def test_execution_context_evidence_field_defaults_none(self):
        from cozmo.runtime.execution_context import ExecutionContext

        ctx = ExecutionContext(user_input="x")
        assert ctx.evidence_context is None

    def test_execution_context_accepts_evidence_context(self):
        from cozmo.runtime.execution_context import ExecutionContext

        ev = EvidenceContext(query="q")
        ctx = ExecutionContext(user_input="x", evidence_context=ev)
        assert ctx.evidence_context is ev
