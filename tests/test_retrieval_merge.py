"""Tests for ResultMerger — Phase 9.5 deterministic multi-source merge.

Covers cross-source normalization (α/β/γ, clamping, source priorities,
positional rank, query overlap), deduplication with attribution, provenance
metadata, quality grading, allocation preservation, and determinism.

Pure unit tests: no network, no backend, no model, no stores.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from cozmo.runtime.evidence import RetrievalQuality
from cozmo.runtime.retrieval_budget import ContextAllocation
from cozmo.runtime.result_merger import MergeWeights, ResultMerger
from cozmo.runtime.sources.base import MergedRetrievalResult, RetrievedItem, RetrievalResult

BUDGET = ContextAllocation()
NORMALIZATION = "weighted_source_position_overlap"


def _item(source, text, score=0.0, item_id=None, metadata=None):
    return RetrievedItem(
        id=item_id or f"{source}-{text[:12]}",
        text=text,
        source=source,
        score=score,
        metadata=dict(metadata or {}),
    )


def _result(source, items, quality=RetrievalQuality.SUFFICIENT):
    return RetrievalResult(source=source, items=items, quality=quality)


class TestMergedRetrievalResultContract:
    def test_frozen(self):
        m = ResultMerger().merge([], "q", BUDGET)
        assert isinstance(m, MergedRetrievalResult)
        with pytest.raises(FrozenInstanceError):
            m.query = "other"

    def test_field_types(self):
        m = ResultMerger().merge([], "q", BUDGET)
        assert isinstance(m.items, tuple)
        assert isinstance(m.source_results, tuple)
        assert isinstance(m.quality, RetrievalQuality)
        assert isinstance(m.allocation_used, ContextAllocation)
        assert isinstance(m.metrics, dict)


class TestMergeWeights:
    def test_defaults_sum_to_one(self):
        MergeWeights()

    def test_weights_must_sum_to_one(self):
        with pytest.raises(ValueError):
            MergeWeights(alpha=0.5, beta=0.5, gamma=0.5)


class TestNormalization:
    def test_formula_exact_full_score(self):
        results = [_result("memory", [_item("memory", "python decorators are useful", score=0.9)])]
        merged = ResultMerger().merge(results, "python decorators", BUDGET)
        # 0.4*1.0 + 0.4*1.0 + 0.2*1.0 = 1.0
        assert merged.items[0].score == pytest.approx(1.0)
        assert merged.items[0].metadata["merge_score"] == 1.0

    def test_clamp_upper(self):
        merger = ResultMerger(source_prior={"memory": 5.0})
        results = [_result("memory", [_item("memory", "python decorators are useful")])]
        merged = merger.merge(results, "python decorators", BUDGET)
        assert merged.items[0].score == 1.0

    def test_clamp_lower(self):
        merger = ResultMerger(source_prior={"memory": -2.0})
        results = [_result("memory", [_item("memory", "python decorators are useful")])]
        merged = merger.merge(results, "python decorators", BUDGET)
        assert merged.items[0].score == 0.0

    def test_source_priorities(self):
        merger = ResultMerger(MergeWeights(alpha=0.5, beta=0.5, gamma=0.0))
        results = [
            _result("web", [_item("web", "tomorrow weather forecast")]),
            _result("memory", [_item("memory", "blue sky morning walk")]),
        ]
        merged = merger.merge(results, "python", BUDGET)
        assert len(merged.items) == 2
        assert merged.items[0].source == "memory"
        assert merged.items[0].score == pytest.approx(1.0)  # 0.5*1.0 + 0.5*1.0
        assert merged.items[1].source == "web"
        assert merged.items[1].score == pytest.approx(0.7)  # 0.5*0.4 + 0.5*1.0

    def test_positional_ranking(self):
        merger = ResultMerger(MergeWeights(alpha=0.5, beta=0.5, gamma=0.0))
        results = [_result("web", [
            _item("web", "first item alpha delta"),
            _item("web", "second item beta gamma"),
        ])]
        merged = merger.merge(results, "python", BUDGET)
        assert merged.items[0].metadata["source_rank"] == 1
        assert merged.items[1].metadata["source_rank"] == 2
        assert merged.items[0].score > merged.items[1].score
        assert merged.items[0].score == pytest.approx(0.7)  # 0.2 + 0.5*1.0
        assert merged.items[1].score == pytest.approx(0.45)  # 0.2 + 0.5*0.5

    def test_overlap_scoring(self):
        merger = ResultMerger(MergeWeights(alpha=0.0, beta=0.0, gamma=1.0))
        results = [_result("web", [
            _item("web", "python decorators are the topic"),
            _item("web", "nothing about anything here"),
        ])]
        merged = merger.merge(results, "python decorators", BUDGET)
        assert merged.items[0].text.startswith("python")
        assert merged.items[0].score == pytest.approx(1.0)
        assert merged.items[1].score == pytest.approx(0.0)

    def test_empty_query_neutral_overlap(self):
        results = [_result("memory", [_item("memory", "anything at all")])]
        merged = ResultMerger().merge(results, "", BUDGET)
        assert merged.items[0].score == pytest.approx(1.0)  # 0.4 + 0.4 + 0.2*1.0


class TestDeterminism:
    def test_identical_inputs_identical_outputs(self):
        results = [
            _result("memory", [_item("memory", "remembered fact", score=0.9)]),
            _result("knowledge", [_item("knowledge", "documented fact", score=0.8)]),
        ]
        merger = ResultMerger()
        a = merger.merge(results, "fact", BUDGET)
        b = merger.merge(results, "fact", BUDGET)
        assert a == b

    def test_ordering_is_score_desc(self):
        results = [_result("web", [
            _item("web", "low relevance web result"),
            _item("web", "python decorators high relevance"),
        ])]
        merged = ResultMerger().merge(results, "python decorators", BUDGET)
        scores = [it.score for it in merged.items]
        assert scores == sorted(scores, reverse=True)

    def test_source_order_does_not_matter(self):
        memory_r = _result("memory", [_item("memory", "remembered fact")])
        web_r = _result("web", [_item("web", "fresh web fact")])
        merger = ResultMerger(MergeWeights(alpha=0.5, beta=0.5, gamma=0.0))
        m1 = merger.merge([memory_r, web_r], "fact", BUDGET)
        m2 = merger.merge([web_r, memory_r], "fact", BUDGET)
        assert [(it.source, it.score) for it in m1.items] == \
            [(it.source, it.score) for it in m2.items]


class TestDeduplication:
    def test_removes_duplicate_across_sources(self):
        results = [
            _result("memory", [_item("memory", "python is a programming language")]),
            _result("knowledge", [_item("knowledge", "python is a programming language")]),
        ]
        merged = ResultMerger().merge(results, "python programming", BUDGET)
        assert len(merged.items) == 1
        assert merged.metrics["dedup_removed"] == 1

    def test_attribution_preserved(self):
        results = [
            _result("knowledge", [_item("knowledge", "python is a programming language")]),
            _result("memory", [_item("memory", "python is a programming language")]),
        ]
        merged = ResultMerger().merge(results, "python programming", BUDGET)
        assert len(merged.items) == 1
        assert merged.items[0].source == "memory"  # higher-priority source keeps canonical slot
        assert merged.items[0].metadata["attributed_sources"] == ["knowledge", "memory"]

    def test_attribution_deterministic(self):
        results = [
            _result("knowledge", [_item("knowledge", "shared content here")]),
            _result("memory", [_item("memory", "shared content here")]),
        ]
        merger = ResultMerger()
        a = merger.merge(results, "content", BUDGET)
        b = merger.merge(results, "content", BUDGET)
        assert a.items[0].metadata["attributed_sources"] == b.items[0].metadata["attributed_sources"]

    def test_keeps_distinct_items(self):
        results = [_result("web", [
            _item("web", "one distinct topic"),
            _item("web", "a completely different topic"),
        ])]
        merged = ResultMerger().merge(results, "python", BUDGET)
        assert len(merged.items) == 2


class TestProvenanceMetadata:
    def test_metadata_provenance(self):
        item = _item("memory", "python decorators are useful", score=0.9)
        merged = ResultMerger().merge([_result("memory", [item])], "python decorators", BUDGET)
        meta = merged.items[0].metadata
        assert meta["original_score"] == 0.9
        assert meta["merge_score"] == 1.0
        assert meta["source_rank"] == 1
        assert meta["normalization"] == NORMALIZATION

    def test_original_metadata_preserved(self):
        item = _item("memory", "remembered fact", metadata={"type": "preference", "frequency": 3})
        merged = ResultMerger().merge([_result("memory", [item])], "fact", BUDGET)
        assert merged.items[0].metadata["type"] == "preference"
        assert merged.items[0].metadata["frequency"] == 3


class TestQuality:
    def test_empty_results(self):
        merged = ResultMerger().merge([_result("web", [])], "q", BUDGET)
        assert merged.quality == RetrievalQuality.EMPTY
        assert merged.items == ()

    def test_no_sources(self):
        merged = ResultMerger().merge([], "q", BUDGET)
        assert merged.quality == RetrievalQuality.EMPTY

    def test_failed_sources_only(self):
        merged = ResultMerger().merge(
            [_result("web", [], quality=RetrievalQuality.FAILED)], "q", BUDGET
        )
        assert merged.quality == RetrievalQuality.FAILED

    def test_mixed_failed_empty_is_empty(self):
        results = [
            _result("web", [], quality=RetrievalQuality.FAILED),
            _result("knowledge", [], quality=RetrievalQuality.EMPTY),
        ]
        merged = ResultMerger().merge(results, "q", BUDGET)
        assert merged.quality == RetrievalQuality.EMPTY

    def test_sufficient_when_items(self):
        merged = ResultMerger().merge([_result("memory", [_item("memory", "usable fact")])], "fact", BUDGET)
        assert merged.quality == RetrievalQuality.SUFFICIENT


class TestAllocation:
    def test_allocation_object_preserved(self):
        allocation = ContextAllocation(max_sources=2, max_results=4, max_context_chars=500)
        merged = ResultMerger().merge([], "q", allocation)
        assert merged.allocation_used is allocation
        assert (merged.allocation_used.max_sources,
                merged.allocation_used.max_results,
                merged.allocation_used.max_context_chars) == (2, 4, 500)


class TestMetrics:
    def test_metrics_recorded(self):
        results = [
            _result("memory", [_item("memory", "shared content here")]),
            _result("knowledge", [_item("knowledge", "shared content here")]),
            _result("knowledge", [_item("knowledge", "second doc fact")]),
        ]
        merged = ResultMerger().merge(results, "content", BUDGET)
        assert merged.metrics["sources_consulted"] == ["knowledge", "memory"]
        assert merged.metrics["sources_with_results"] == ["knowledge", "memory"]
        assert merged.metrics["items_total_before_dedup"] == 3
        assert merged.metrics["items_after_dedup"] == 2
        assert merged.metrics["dedup_removed"] == 1
        assert merged.metrics["normalization"] == NORMALIZATION
