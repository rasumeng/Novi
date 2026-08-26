"""M5 â€” Unified retrieval + graph-aware context assembly regression tests.

Covers: superseded filtering, durable-identity deduplication, bounded
ranking deltas (memory semantics / scenario affinity / graph distance),
context-budget selection (minimum sufficient context), UnifiedRetriever
composition + metrics, executor integration with legacy fallback, and
architecture guards for the unified modules.

Pure unit/integration tests: no network, no model, no real stores.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from novi.runtime.evidence import RetrievalQuality
from novi.runtime.retrieval import RetrievalExecutor
from novi.runtime.retrieval_budget import ContextAllocation
from novi.runtime.result_merger import MergeWeights, RankAdjustments, ResultMerger
from novi.runtime.sources.base import MergedRetrievalResult, RetrievedItem, RetrievalResult
from novi.runtime.unified_retrieval import CANONICAL_ORDER, SourceBinding, UnifiedRetriever

BUDGET = ContextAllocation()


def _item(source, text, score=0.0, item_id=None, metadata=None, id=None):
    return RetrievedItem(
        id=id or item_id or f"{source}-{abs(hash(text)) % 100000}",
        text=text,
        source=source,
        score=score,
        metadata=dict(metadata or {}),
    )


def _result(source, items, quality=RetrievalQuality.SUFFICIENT):
    return RetrievalResult(source=source, items=items, quality=quality)


# â”€â”€ superseded filtering (Â§2.1 preserved at candidate boundary) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestSupersededFiltering:
    def test_superseded_dropped_before_ranking(self):
        results = [_result("knowledge", [
            _item("knowledge", "live knowledge", 0.9,
                  metadata={"status": "verified"}),
            _item("knowledge", "dead claim", 0.99,
                  metadata={"status": "superseded"}),
        ])]
        merged = ResultMerger().merge(results, "knowledge", BUDGET)
        texts = [it.text for it in merged.items]
        assert "dead claim" not in texts
        assert "live knowledge" in texts
        assert merged.metrics["filtered_superseded"] == 1

    def test_all_superseded_leaves_empty_pool(self):
        results = [_result("knowledge", [
            _item("knowledge", "dead", 0.9, metadata={"status": "superseded"}),
        ])]
        merged = ResultMerger().merge(results, "q", BUDGET)
        assert merged.items == ()
        assert merged.metrics["filtered_superseded"] == 1

    def test_status_case_insensitive(self):
        results = [_result("knowledge", [
            _item("knowledge", "x", metadata={"status": "SUPERSEDED"}),
        ])]
        assert ResultMerger().merge(results, "q", BUDGET).items == ()

    def test_items_without_status_untouched(self):
        results = [_result("memory", [
            _item("memory", "plain memory row"),
        ])]
        merged = ResultMerger().merge(results, "q", BUDGET)
        assert len(merged.items) == 1
        assert "filtered_superseded" not in merged.metrics


# â”€â”€ durable-identity deduplication (Â§6) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestIdentityDeduplication:
    def test_chunks_of_same_note_collapse_by_item_id(self):
        results = [_result("knowledge", [
            _item("knowledge", "note chunk one", id="a.md::0",
                  metadata={"item_id": "kn-1", "path": "a.md"}),
            _item("knowledge", "note chunk two", id="a.md::1",
                  metadata={"item_id": "kn-1", "path": "a.md"}),
        ])]
        merged = ResultMerger().merge(results, "note", BUDGET)
        assert len(merged.items) == 1
        assert merged.metrics["identity_dedup_removed"] == 1

    def test_graph_twin_collapses_into_semantic(self):
        results = [_result("knowledge", [
            _item("knowledge", "semantic hit text", id="a.md::0",
                  metadata={"item_id": "kn-1", "origin": "semantic"}),
            _item("knowledge", "same note via graph", id="kn-1",
                  metadata={"item_id": "kn-1", "origin": "wikilink", "hops": 1}),
        ])]
        merged = ResultMerger().merge(results, "text", BUDGET)
        assert len(merged.items) == 1
        origins = {it.metadata["origin"] for it in merged.items}
        assert origins == {"semantic"}, "first in priority order wins"

    def test_recall_row_and_chunk_share_identity(self):
        """Resolver rows carry kn- ids in metadata['id']; chunks carry it in
        metadata['item_id'] â€” one identity across surfaces."""
        results = [
            _result("memory", [
                _item("memory", "python decorators explained", id="kn-7",
                      metadata={"id": "kn-7", "kind": "knowledge"}),
            ]),
            _result("knowledge", [
                _item("knowledge", "python decorators explained", id="p.md::0",
                      metadata={"item_id": "kn-7", "path": "p.md"}),
            ]),
        ]
        merged = ResultMerger().merge(results, "decorators", BUDGET)
        assert len(merged.items) == 1
        assert sorted(merged.items[0].metadata["attributed_sources"]) == [
            "knowledge", "memory"
        ]

    def test_same_content_different_durable_ids_kept_apart(self):
        """Spec §6: text similarity alone never merges distinct knowledge."""
        results = [_result("knowledge", [
            _item("knowledge", "identical wording here", id="x.md::0",
                  metadata={"item_id": "kn-a"}),
            _item("knowledge", "identical wording here", id="y.md::0",
                  metadata={"item_id": "kn-b"}),
        ])]
        merged = ResultMerger().merge(results, "wording", BUDGET)
        assert len(merged.items) == 2, "distinct durable ids = distinct knowledge"

    def test_legacy_chunk_discovered_twice_merged_by_content_rule(self):
        """No durable identity → Phase 9.5 content rule still applies."""
        results = [
            _result("knowledge", [
                _item("knowledge", "python decorators ground truth", id="kb.md::0"),
            ]),
            _result("memory", [
                _item("memory", "python decorators ground truth", id="conv-9",
                      metadata={"type": "fact"}),
            ]),
        ]
        merged = ResultMerger().merge(results, "decorators", BUDGET)
        assert len(merged.items) == 1
        assert sorted(merged.items[0].metadata["attributed_sources"]) == [
            "knowledge", "memory"
        ]

    def test_distinct_similar_content_with_ids_kept(self):
        results = [_result("knowledge", [
            _item("knowledge", "python decorators are useful wrappers", id="x::0",
                  metadata={"item_id": "kn-a"}),
            _item("knowledge", "python decorators wrap functions nicely", id="y::0",
                  metadata={"item_id": "kn-b"}),
        ])]
        merged = ResultMerger().merge(results, "decorators", BUDGET)
        assert len(merged.items) == 2, "similar â‰  same; identities differ"

    def test_missing_identity_falls_back_to_content_rule(self):
        results = [
            _result("memory", [_item("memory", "shared plain content")]),
            _result("knowledge", [_item("knowledge", "shared plain content")]),
        ]
        merged = ResultMerger().merge(results, "content", BUDGET)
        assert len(merged.items) == 1


# â”€â”€ ranking deltas (Â§7 â€” bounded adjustments on the pinned base) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestRankingDeltas:
    def test_base_formula_unchanged_without_metadata(self):
        results = [_result("memory", [
            _item("memory", "python decorators are useful", score=0.9),
        ])]
        merged = ResultMerger().merge(results, "python decorators", BUDGET)
        assert merged.items[0].score == pytest.approx(1.0)
        meta = merged.items[0].metadata
        assert meta["delta_memory"] == 0.0
        assert meta["delta_affinity"] == 0.0
        assert meta["delta_hops"] == 0.0

    def _equal_base_merger(self):
        """Î³-only weights: equal base for texts sharing the query term, so
        the bounded deltas alone decide the order."""
        return ResultMerger(MergeWeights(alpha=0.0, beta=0.0, gamma=1.0))

    def test_confidence_status_lifts_near_tie(self):
        merger = self._equal_base_merger()
        weak = _item("knowledge", "alpha candidate entry", 0.5,
                     metadata={"confidence": 0.4, "status": "candidate"})
        strong = _item("knowledge", "beta verified proof", 0.5,
                       metadata={"confidence": 1.0, "status": "verified"})
        merged = merger.merge(
            [_result("knowledge", [weak, strong])], "entry proof", BUDGET)
        by_text = {it.text: it.score for it in merged.items}
        assert by_text["beta verified proof"] > by_text["alpha candidate entry"]

    def test_same_scenario_affinity_bonus_cross_stays_neutral(self):
        merger = self._equal_base_merger()
        cross = _item("knowledge", "alpha foreign entry", 0.5,
                      metadata={"scenario_affinity": "cross"})
        same = _item("knowledge", "beta local proof", 0.5,
                     metadata={"scenario_affinity": "same"})
        merged = merger.merge(
            [_result("knowledge", [cross, same])], "entry proof", BUDGET)
        by_text = {it.text: it.score for it in merged.items}
        assert by_text["beta local proof"] > by_text["alpha foreign entry"]
        # Cross-scenario explicit links are never discarded:
        assert all(score > 0.3 for score in by_text.values())

    def test_multi_hop_neighbors_demoted_not_dropped(self):
        merger = self._equal_base_merger()
        hop1 = _item("knowledge", "alpha direct entry", 0.5,
                     metadata={"hops": 1})
        hop3 = _item("knowledge", "beta distant proof", 0.5,
                     metadata={"hops": 3})
        merged = merger.merge([_result("knowledge", [hop3, hop1])], "entry proof", BUDGET)
        scores = {it.text: it.score for it in merged.items}
        assert scores["alpha direct entry"] > scores["beta distant proof"]
        assert scores["beta distant proof"] > 0.3, "demoted, never discarded"

    def test_adjustments_validation(self):
        RankAdjustments(memory=0.0, affinity=1.0, hop_penalty=0.5)
        with pytest.raises(ValueError):
            RankAdjustments(hop_penalty=1.5)

    def test_ranking_deterministic(self):
        merger = ResultMerger()
        items = [
            _item("memory", "alpha fact", 0.8,
                  metadata={"confidence": 0.9, "status": "verified"}),
            _item("knowledge", "beta fact", 0.7,
                  metadata={"item_id": "kn-2", "origin": "wikilink", "hops": 1}),
        ]
        a = merger.merge([_result("memory", [items[0]]), _result("knowledge", [items[1]])], "fact", BUDGET)
        b = merger.merge([_result("memory", [items[0]]), _result("knowledge", [items[1]])], "fact", BUDGET)
        assert [(i.id, i.score) for i in a.items] == [(i.id, i.score) for i in b.items]


# â”€â”€ context budget selection (Â§10 â€” minimum sufficient context) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestContextBudgetSelection:
    def _ranked(self):
        return (
            _item("memory", "python decorators explained fully", 0.95,
                  metadata={"item_id": "kn-1"}),
            _item("knowledge", "decorators wrap python functions", 0.85,
                  metadata={"item_id": "kn-2"}),
            _item("knowledge", "unrelated long tail filler content", 0.40,
                  metadata={"item_id": "kn-3"}),
            _item("knowledge", "wikilink neighbor deep detail", 0.35,
                  metadata={"item_id": "kn-4", "origin": "wikilink", "hops": 1}),
        )

    def test_early_stop_on_coverage_minimum_sufficient(self):
        picked = ResultMerger().select(
            self._ranked(), "python decorators", BUDGET
        )
        assert len(picked) == 2, "stop once coverage target is met"
        joined = " ".join(it.text for it in picked).lower()
        assert "python" in joined and "decorators" in joined

    def test_char_budget_respected(self):
        alloc = ContextAllocation(max_results=10, max_context_chars=60)
        picked = ResultMerger().select(self._ranked(), "nothing matching", alloc)
        chars = sum(len(it.text) for it in picked)
        assert chars <= 60 + len(picked[0].text), "budget bounds the pick"
        assert picked, "always returns something when pool non-empty"

    def test_max_results_cap(self):
        alloc = ContextAllocation(max_results=2, max_context_chars=10000)
        picked = ResultMerger().select(self._ranked(), "filler", alloc)
        assert len(picked) <= 2

    def test_no_key_terms_fills_to_cap(self):
        alloc = ContextAllocation(max_results=3, max_context_chars=10000)
        picked = ResultMerger().select(self._ranked(), "", alloc)
        assert len(picked) == 3, "sufficiency unmeasurable â†’ take ranked head"

    def test_graph_candidates_compete_no_auto_inclusion(self):
        """A wikilink neighbor ranks low â†’ it loses its budget slot fairly."""
        alloc = ContextAllocation(max_results=1, max_context_chars=10000)
        picked = ResultMerger().select(self._ranked(), "deep detail", alloc)
        assert len(picked) == 1
        assert picked[0].metadata.get("origin") != "wikilink"

    def test_select_does_not_mutate_input(self):
        ranked = self._ranked()
        before = [(i.id, i.score) for i in ranked]
        ResultMerger().select(ranked, "python", BUDGET)
        assert [(i.id, i.score) for i in ranked] == before


# â”€â”€ UnifiedRetriever composition (Â§9 wiring, single merger) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class _StaticSource:
    def __init__(self, name, items, quality=RetrievalQuality.SUFFICIENT, fail=False):
        self.id = name
        self._items = items
        self._quality = quality
        self._fail = fail
        self.calls = []

    def retrieve(self, query, budget):
        self.calls.append(query)
        if self._fail:
            raise RuntimeError(f"{self.id} down")
        return RetrievalResult(source=self.id, items=self._items, quality=self._quality)


class TestUnifiedRetriever:
    def _bindings(self, graph=False):
        kb = [
            _item("knowledge", "python decorators explained", id="a.md::0",
                  metadata={"path": "a.md", "item_id": "kn-1", "title": "Decorators"}),
        ]
        if graph:
            kb.append(_item(
                "knowledge", "decorator registry neighbor", id="g.md::0",
                metadata={"path": "g.md", "item_id": "kn-3",
                          "origin": "wikilink", "hops": 1}))
        return [
            SourceBinding("knowledge", _StaticSource("knowledge", kb)),
            SourceBinding("memory", _StaticSource("memory", [
                _item("memory", "user prefers python decorators", id="mem-1",
                      metadata={"type": "fact", "confidence": 1.0,
                                "status": "verified"}),
            ])),
        ]

    def test_gate_reported_sufficient_when_no_graph_origin(self):
        outcome = UnifiedRetriever().retrieve(
            "python decorators", BUDGET, self._bindings(graph=False))
        assert outcome.merged.metrics["gate"] == "sufficient_semantic"
        assert outcome.merged.metrics["graph_candidates"] == 0

    def test_gate_reported_expanded_with_graph_candidates(self):
        outcome = UnifiedRetriever().retrieve(
            "decorators", BUDGET, self._bindings(graph=True))
        assert outcome.merged.metrics["gate"] == "expanded_graph"
        assert outcome.merged.metrics["graph_candidates"] == 1

    def test_metrics_recorded(self):
        outcome = UnifiedRetriever().retrieve(
            "python decorators", ContextAllocation(max_results=5), self._bindings())
        m = outcome.merged.metrics
        assert set(m["per_source"]) == {"knowledge", "memory"}
        assert m["discovery_latency_ms"] >= 0
        assert m["ranking_latency_ms"] >= 0
        assert m["selection_latency_ms"] >= 0
        assert m["selected_items"] >= 1
        assert 0.0 <= m["context_coverage"] <= 1.0
        assert m["source_contribution"]["knowledge"] >= 1

    def test_failed_source_degrades_gracefully(self):
        bindings = self._bindings() + [
            SourceBinding("project", _StaticSource("project", [], fail=True)),
        ]
        outcome = UnifiedRetriever().retrieve("python decorators", BUDGET, bindings)
        assert outcome.selected, "healthy sources still produce context"
        assert outcome.merged.metrics["per_source"]["project"]["quality"] != "sufficient"

    def test_binding_order_irrelevant_to_outcome(self):
        a = UnifiedRetriever().retrieve("decorators", BUDGET, self._bindings(True))
        b = UnifiedRetriever().retrieve(
            "decorators", BUDGET, list(reversed(self._bindings(True))))
        assert [(i.id, round(i.score, 6)) for i in a.merged.items] == \
            [(i.id, round(i.score, 6)) for i in b.merged.items]

    def test_canonical_order_defined(self):
        assert CANONICAL_ORDER == ("identity", "scenario", "knowledge", "memory", "project")

    def test_single_merger_used(self):
        merger = ResultMerger()
        retriever = UnifiedRetriever(merger=merger)
        assert retriever._merger is merger


# â”€â”€ executor integration (Â§15 migration: add â†’ integrate, no removals) â”€â”€â”€â”€â”€â”€


class _FakeKnowledgeIndex:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def search(self, query, k=5, rerank=True):
        self.calls.append((query, k))
        return self.rows[:k]


class TestExecutorIntegration:
    def _rows(self):
        return [{
            "id": "kb.md::0",
            "text": "python decorators ground truth",
            "score": 0.9,
            "metadata": {"path": "kb.md", "title": "KB",
                         "item_id": "kn-1"},
        }]

    def test_single_source_keeps_legacy_path_byte_identical(self):
        idx = _FakeKnowledgeIndex(self._rows())
        exe = RetrievalExecutor(knowledge_source=__import__(
            "novi.runtime.sources", fromlist=["KnowledgeRetrievalSource"]
        ).KnowledgeRetrievalSource(idx))
        out = exe.retrieve_knowledge("python decorators")
        assert out.startswith("- **KB** (kb.md, score=0.90):")
        assert idx.calls == [("python decorators", 5)]

    def test_multi_source_uses_unified_merge(self):
        from novi.runtime.sources import KnowledgeRetrievalSource

        idx = _FakeKnowledgeIndex(self._rows())
        memory = MagicMock()
        memory.query.return_value = [{
            "id": "conv-1",
            "text": "user prefers python historically",
            "score": 0.8,
            "metadata": {"type": "fact"},
        }]
        exe = RetrievalExecutor(
            knowledge_source=KnowledgeRetrievalSource(idx),
            memory=memory,
        )
        out = exe.retrieve_knowledge("python decorators")
        assert "- **KB** (kb.md" in out, "knowledge chunk rendered from merged pool"
        assert memory.query.called, "memory source consulted through unified path"

    def test_multi_source_cross_source_duplicate_deduplicated(self):
        """Same fact via memory + knowledge renders once (unified dedup)."""
        from novi.runtime.sources import KnowledgeRetrievalSource

        idx = _FakeKnowledgeIndex([{
            "id": "kb.md::0",
            "text": "user prefers python historically today",
            "score": 0.9,
            "metadata": {"path": "kb.md", "title": "KB", "item_id": "kn-1"},
        }])
        memory = MagicMock()
        memory.query.return_value = [{
            "id": "conv-1",
            "text": "user prefers python historically",
            "score": 0.8,
            "metadata": {"type": "fact"},
        }]
        exe = RetrievalExecutor(
            knowledge_source=KnowledgeRetrievalSource(idx),
            memory=memory,
        )
        out = exe.retrieve_knowledge("python")
        assert out.count("user prefers python") == 1

    def test_no_brain_no_memory_legacy_fallback_preserved(self):
        from novi.runtime.sources import KnowledgeRetrievalSource

        idx = _FakeKnowledgeIndex([])
        exe = RetrievalExecutor(knowledge_source=KnowledgeRetrievalSource(idx))
        assert exe.retrieve_knowledge("anything") == ""
        assert idx.calls == [("anything", 5)], "single-source legacy path"

    def test_knowledge_failure_returns_empty_string(self):
        from novi.runtime.sources import KnowledgeRetrievalSource

        class Broken:
            def search(self, query, k=5, rerank=True):
                raise RuntimeError("kb down")

        exe = RetrievalExecutor(knowledge_source=KnowledgeRetrievalSource(Broken()))
        assert exe.retrieve_knowledge("q") == ""


# â”€â”€ architecture guards (Â§16) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestArchitectureGuards:
    _FORBIDDEN = (
        "langgraph", "lancedb", "sqlite3", "LanceStore", "VectorStore",
        "RelationshipStore", "MarkdownStore", "MemoryManager",
    )
    _UNIFIED_MODULES = (
        Path("novi/runtime/unified_retrieval.py"),
        Path("novi/runtime/result_merger.py"),
    )

    @pytest.mark.parametrize("rel", _UNIFIED_MODULES)
    def test_unified_modules_are_pure_orchestration(self, rel):
        root = Path(__file__).resolve().parent.parent
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        for forbidden in self._FORBIDDEN:
            assert not any(forbidden in mod for mod in imported), (
                f"{rel} imports {forbidden} â€” unified layer must stay pure "
                f"(no storage, no LangGraph)"
            )

    def test_retrieval_orchestration_performs_no_storage_writes(self):
        """No store-shaped writes from the unified orchestration surface."""
        root = Path(__file__).resolve().parent.parent
        write_shape = re.compile(
            r"\b(store|table|conn)\w*\.(add|add_many|update|delete|insert)\b"
        )
        for rel in self._UNIFIED_MODULES:
            source = (root / rel).read_text(encoding="utf-8")
            bad = [
                line.strip() for line in source.splitlines()
                if write_shape.search(line) and not line.strip().startswith("#")
            ]
            assert not bad, f"{rel}: suspicious storage write: {bad[:2]}"
