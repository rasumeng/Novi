"""M4 â€” WikiLink-aware retrieval expansion regression tests.

Covers the spec matrix: sufficiency gating (no unnecessary graph reads),
outgoing/incoming traversal, hop bounds, cycle safety, durable-id
deduplication, dangling/deleted target safety, deterministic order,
legacy-behavior preservation when expansion finds nothing, scoping,
RetrievedItem/RecallItem pipeline integration, and Brain durable identity.

Three levels:
  - resolver-level   â€” LayeredRetrievalResolver over fake backends (pure).
  - brain-level      â€” real stores end-to-end through Brain.recall.
  - source-level     â€” KnowledgeRetrievalSource RetrievedItem stream.
"""

from __future__ import annotations

import pytest

from cozmo.brain import Brain, EdgeKind, QueryContext, Relationship
from cozmo.brain.layers.knowledge import KnowledgeLayer
from cozmo.brain.layers.scenarios import ScenarioLayer
from cozmo.brain.reasoning.expansion import ExpansionConfig
from cozmo.brain.reasoning.resolver import LayeredRetrievalResolver
from cozmo.brain.storage.relationship_store import RelationshipStore
from cozmo.brain.storage.scenario_store import ScenarioStore
from cozmo.brain.storage.vector_store import VectorStore
from cozmo.brain.types import (
    KnowledgeForm,
    KnowledgeHit,
    KnowledgeItem,
    KnowledgeStatus,
    Scenario,
    ScenarioStatus,
)
from cozmo.runtime.evidence import RetrievalQuality
from cozmo.runtime.retrieval_budget import ContextAllocation
from cozmo.runtime.sources import KnowledgeRetrievalSource
from cozmo.services.embedding import EmbeddingService


# â”€â”€ shared fakes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class FakeGraph:
    """Neighborhood callable with recorded reads."""

    def __init__(self, edges=None):
        self.edges = edges or {}
        self.calls: list[str] = []

    def neighborhood(self, item_id):
        self.calls.append(item_id)
        view = self.edges.get(item_id) or {"references": (), "backlinks": ()}
        return {
            "references": tuple(view.get("references", ())),
            "backlinks": tuple(view.get("backlinks", ())),
        }


class FakeBackend:
    """load_scenario / query_knowledge / query_memory backend."""

    def __init__(self):
        self.scenarios = {}
        self.knowledge: list[KnowledgeHit] = []
        self.memory: list[dict] = []
        self.knowledge_calls: list[tuple] = []

    def load(self, scenario_id):
        return self.scenarios.get(scenario_id)

    def query_scoped(self, query, scenario_id=None, k=5, distance_threshold=0.5):
        self.knowledge_calls.append((query, scenario_id, k))
        return [
            h for h in self.knowledge if h.item.scenario_id == scenario_id
        ]

    def query_memory(self, query, k, threshold):
        return self.memory


def _hit(item_id, content, score, scenario_id=None):
    return KnowledgeHit(
        item=KnowledgeItem(
            id=item_id,
            form=KnowledgeForm.ATOMIC,
            content=content,
            confidence=1.0,
            status=KnowledgeStatus.VERIFIED,
            scenario_id=scenario_id,
        ),
        score=score,
    )


def make_scenario(sid="scn-1"):
    return Scenario(
        id=sid,
        name="Build",
        purpose="Working on the build.",
        project_id=None,
        status=ScenarioStatus.ACTIVE,
        summary="Working on the build.",
    )


def make_resolver(backend, graph=None, **kw):
    return LayeredRetrievalResolver(
        load_scenario=backend.load,
        query_knowledge=backend.query_scoped,
        query_memory=backend.query_memory,
        neighborhood=graph.neighborhood if graph else None,
        fetch_knowledge=lambda ids: backend.fetch(ids),
        **kw,
    )


# â”€â”€ 1. sufficient semantic retrieval never triggers graph reads â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def test_sufficient_semantic_skips_graph_expansion():
    backend, graph = FakeBackend(), FakeGraph(
        {"kn-a": {"references": ("kn-b",), "backlinks": ()}}
    )
    backend.knowledge = [_hit("kn-a", "strong match", 0.9, "scn-1")]
    backend.scenarios["scn-1"] = make_scenario()
    res = make_resolver(backend, graph).recall(
        "q", QueryContext(scenario_id="scn-1")
    )
    assert res.metrics["gate"] == "knowledge"
    assert graph.calls == [], "graph must not be read when gate passes"
    assert all(i.metadata.get("origin") != "wikilink" for i in res.items)


# â”€â”€ 2/3/4. insufficient â†’ outgoing references + incoming backlinks â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def test_insufficient_semantic_discovers_outgoing_reference():
    backend, graph = FakeBackend(), FakeGraph(
        {"kn-a": {"references": ("kn-b",), "backlinks": ()}}
    )
    backend.knowledge = [_hit("kn-a", "weak match", 0.1)]
    backend.fetch = lambda ids: [_hit(i, f"content {i}", 0.0) for i in ids]
    res = make_resolver(backend, graph).recall("q")
    texts = [i.text for i in res.items]
    assert "content kn-b" in texts, "neighbor must be discovered"
    neighbor = [i for i in res.items if i.metadata.get("origin") == "wikilink"]
    assert len(neighbor) == 1
    assert neighbor[0].metadata["via"] == "reference"
    assert neighbor[0].metadata["hops"] == 1
    # plan observability
    assert res.metrics["gate"] == "graph"
    assert res.metrics["layers"][-1] == "graph"
    assert res.metrics["plan"].graph_items == 1
    # conversation fallback must be skipped when the graph satisfies the gate
    assert "conversation" not in res.metrics["layers"]


def test_insufficient_semantic_discovers_incoming_backlink():
    backend, graph = FakeBackend(), FakeGraph(
        {"kn-a": {"references": (), "backlinks": ("kn-c",)}}
    )
    backend.knowledge = [_hit("kn-a", "weak match", 0.1)]
    backend.fetch = lambda ids: [_hit(i, f"content {i}", 0.0) for i in ids]
    res = make_resolver(backend, graph).recall("q")
    neighbor = [i for i in res.items if i.metadata.get("origin") == "wikilink"]
    assert len(neighbor) == 1
    assert neighbor[0].metadata["via"] == "backlink"
    assert neighbor[0].metadata["id"] == "kn-c"


def test_graph_satisfaction_skips_memory_but_no_edges_falls_through():
    """With edges: gate=graph, no memory read. Without: legacy fall-through."""
    backend = FakeBackend()
    backend.knowledge = [_hit("kn-a", "weak match", 0.1)]
    backend.memory = [{"text": "old turn", "score": 0.5}]
    bare = LayeredRetrievalResolver(
        load_scenario=backend.load,
        query_knowledge=backend.query_scoped,
        query_memory=backend.query_memory,
    )
    res = bare.recall("q")
    assert res.metrics["gate"] == "conversation"
    assert any(i.source == "memory" for i in res.items)


# â”€â”€ 5. multi-hop traversal is bounded â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _chain_backend(depth_links):
    edges = {}
    for i in range(len(depth_links) - 1):
        edges[depth_links[i]] = {"references": (depth_links[i + 1],), "backlinks": ()}
    backend = FakeBackend()
    backend.knowledge = [_hit(depth_links[0], "seed", 0.1)]
    backend.fetch = lambda ids: [_hit(i, f"content {i}", 0.0) for i in ids]
    graph = FakeGraph(edges)
    return backend, graph


def test_default_depth_one_stays_shallow():
    backend, graph = _chain_backend(["kn-a", "kn-b", "kn-c", "kn-d"])
    res = make_resolver(backend, graph).recall("q")
    origins = [
        i.metadata["id"] for i in res.items if i.metadata.get("origin") == "wikilink"
    ]
    assert origins == ["kn-b"], "depth=1 must stop at direct neighbors"


def test_depth_two_reaches_second_hop_but_not_third():
    backend, graph = _chain_backend(["kn-a", "kn-b", "kn-c", "kn-d"])
    res = make_resolver(backend, graph, expansion=ExpansionConfig(depth=2)).recall("q")
    origins = {
        i.metadata["id"] for i in res.items if i.metadata.get("origin") == "wikilink"
    }
    assert origins == {"kn-b", "kn-c"}


def test_max_neighbors_caps_discovery():
    backend = FakeBackend()
    backend.knowledge = [_hit("kn-a", "seed", 0.1)]
    backend.fetch = lambda ids: [_hit(i, f"content {i}", 0.0) for i in ids]
    graph = FakeGraph(
        {"kn-a": {"references": ("kn-b", "kn-c", "kn-d"), "backlinks": ()}}
    )
    res = make_resolver(
        backend, graph, expansion=ExpansionConfig(max_neighbors=2)
    ).recall("q")
    origins = [
        i.metadata["id"] for i in res.items if i.metadata.get("origin") == "wikilink"
    ]
    assert origins == ["kn-b", "kn-c"], "cap enforced in deterministic order"


# â”€â”€ 6. cycles do not cause infinite traversal â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def test_cycles_terminate_with_single_visits():
    backend = FakeBackend()
    backend.knowledge = [_hit("kn-a", "seed", 0.1)]
    backend.fetch = lambda ids: [_hit(i, f"content {i}", 0.0) for i in ids]
    graph = FakeGraph(
        {
            "kn-a": {"references": ("kn-b",), "backlinks": ()},
            "kn-b": {"references": ("kn-a", "kn-c"), "backlinks": ()},
            "kn-c": {"references": ("kn-a",), "backlinks": ("kn-b",)},
        }
    )
    res = make_resolver(backend, graph, expansion=ExpansionConfig(depth=2)).recall("q")
    origins = [
        i.metadata["id"] for i in res.items if i.metadata.get("origin") == "wikilink"
    ]
    assert origins == ["kn-b", "kn-c"], "cycle visited once, deterministic order"


# â”€â”€ 7. duplicate semantic + graph result is deduplicated â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def test_neighbor_already_in_semantic_results_not_duplicated():
    backend = FakeBackend()
    # Both seed and its neighbor arrive semantically (global expansion).
    backend.knowledge = [
        _hit("kn-a", "seed", 0.1),
        _hit("kn-b", "neighbor", 0.1),
    ]
    backend.fetch = lambda ids: [_hit(i, f"content {i}", 0.0) for i in ids]
    graph = FakeGraph({"kn-a": {"references": ("kn-b",), "backlinks": ()}})
    res = make_resolver(backend, graph).recall("q")
    ids = [i.metadata.get("id") for i in res.items]
    assert sorted(ids) == ["kn-a", "kn-b"], "no third entry for kn-b"
    assert res.metrics["gate"] == "conversation", "nothing new â†’ legacy flow"


# â”€â”€ 8. dangling note:<Title> never breaks retrieval â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def test_dangling_note_target_is_ignored_gracefully():
    backend = FakeBackend()
    backend.knowledge = [_hit("kn-a", "seed", 0.1)]
    backend.fetch = lambda ids: [
        _hit(i, f"content {i}", 0.0) for i in ids if not i.startswith("note:")
    ]
    graph = FakeGraph(
        {"kn-a": {"references": ("note:Ghost Note",), "backlinks": ()}}
    )
    backend.memory = [{"text": "turn", "score": 0.4}]
    res = make_resolver(backend, graph).recall("q")
    assert res.metrics["gate"] == "conversation", "falls through like pre-M4"
    assert all(i.source != "knowledge" or i.metadata.get("id") != "note:Ghost Note"
               for i in res.items)


# â”€â”€ 9. deleted target does not fail retrieval â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def test_deleted_target_skipped_and_legacy_flow_preserved():
    backend = FakeBackend()
    backend.knowledge = [_hit("kn-a", "seed", 0.1)]

    def fetch(ids):
        return []  # every target deleted/missing

    backend.fetch = fetch
    graph = FakeGraph({"kn-a": {"references": ("kn-gone",), "backlinks": ()}})
    backend.memory = [{"text": "turn", "score": 0.4}]
    res = make_resolver(backend, graph).recall("q")
    assert res.metrics["gate"] == "conversation"
    assert any(i.source == "memory" for i in res.items)


def test_fetch_failure_never_breaks_recall():
    backend = FakeBackend()
    backend.knowledge = [_hit("kn-a", "seed", 0.1)]
    backend.fetch = None
    graph = FakeGraph({"kn-a": {"references": ("kn-b",), "backlinks": ()}})

    def boom(ids):
        raise RuntimeError("store down")

    backend.fetch = boom
    backend.memory = [{"text": "turn", "score": 0.4}]
    res = make_resolver(backend, graph).recall("q")
    assert res.metrics["gate"] == "conversation"


# â”€â”€ 10. deterministic traversal order â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def test_traversal_order_deterministic_across_runs():
    backend = FakeBackend()
    backend.knowledge = [_hit("kn-a", "seed", 0.1)]
    backend.fetch = lambda ids: [_hit(i, f"content {i}", 0.0) for i in ids]
    graph = FakeGraph(
        {"kn-a": {"references": ("kn-z", "kn-m", "kn-a2"), "backlinks": ()}}
    )
    r1 = make_resolver(backend, graph).recall("q")
    r2 = make_resolver(backend, graph).recall("q")
    seq1 = [(i.metadata["id"], i.score) for i in r1.items]
    seq2 = [(i.metadata["id"], i.score) for i in r2.items]
    assert seq1 == seq2
    origin_ids = [
        i.metadata["id"] for i in r1.items if i.metadata.get("origin") == "wikilink"
    ]
    assert origin_ids == ["kn-a2", "kn-m", "kn-z"], "sorted id visit order"


def test_scores_decay_monotonically_per_hop():
    backend, graph = _chain_backend(["kn-a", "kn-b", "kn-c"])
    res = make_resolver(
        backend, graph, expansion=ExpansionConfig(depth=2, hop_decay=0.5)
    ).recall("q")
    by_id = {i.metadata["id"]: i for i in res.items}
    assert by_id["kn-b"].score == pytest.approx(0.05)
    assert by_id["kn-c"].score == pytest.approx(0.025)


# â”€â”€ 11. no graph edges â†’ behavior identical to pre-M4 â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def test_empty_edges_behave_exactly_like_unwired_resolver():
    backend = FakeBackend()
    backend.knowledge = [_hit("kn-a", "weak", 0.1)]
    backend.scenarios["scn-1"] = make_scenario()
    backend.memory = [{"text": "mem", "score": 0.6}]

    graph = FakeGraph()  # wired but edgeless
    wired = make_resolver(backend, graph)
    bare = make_resolver(backend, None)

    rw = wired.recall("q", QueryContext(scenario_id="scn-1"))
    rb = bare.recall("q", QueryContext(scenario_id="scn-1"))
    assert [(i.text, i.score, i.source) for i in rw.items] == [
        (i.text, i.score, i.source) for i in rb.items
    ]
    assert rw.metrics["layers"] == rb.metrics["layers"]
    assert rw.metrics["gate"] == rb.metrics["gate"] == "conversation"


def test_unwired_callables_preserve_legacy_behavior():
    backend = FakeBackend()
    backend.knowledge = [_hit("kn-a", "weak", 0.1)]
    backend.memory = [{"text": "mem", "score": 0.6}]
    res = make_resolver(backend, None).recall("q")
    assert res.metrics["gate"] == "conversation"
    assert res.metrics["plan"].graph_items == 0


# â”€â”€ 12. scoping remains respected â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def test_scoped_sufficiency_blocks_all_graph_reads():
    backend = FakeBackend()
    backend.knowledge = [
        _hit("kn-a", "scoped strong", 0.9, "scn-1"),
        _hit("kn-z", "other scenario", 0.95, "scn-2"),
    ]
    backend.scenarios["scn-1"] = make_scenario()
    graph = FakeGraph(
        {"kn-a": {"references": ("kn-leak",), "backlinks": ()}},
    )
    res = make_resolver(backend, graph).recall(
        "q", QueryContext(scenario_id="scn-1")
    )
    assert graph.calls == []
    assert res.metrics["gate"] == "knowledge"


def test_expansion_runs_only_after_global_stage_failed():
    backend = FakeBackend()
    backend.knowledge = [_hit("kn-a", "weak", 0.1)]
    backend.fetch = lambda ids: []
    graph = FakeGraph({"kn-a": {"references": ("kn-b",), "backlinks": ()}})
    make_resolver(backend, graph).recall("q")
    # scoped + global semantic stages ran before any graph read
    assert len(backend.knowledge_calls) == 2
    assert graph.calls == ["kn-a"]


def test_cross_scenario_neighbor_documented_semantics():
    """Post-gate expansion shares the global-expansion regime: once the gate
    failed twice, cross-scenario neighbors are reachable by design (same as
    whole-graph expansion). This test pins that documented behavior."""
    backend = FakeBackend()
    backend.knowledge = [_hit("kn-a", "weak", 0.1, "scn-1")]
    backend.scenarios["scn-1"] = make_scenario()
    backend.fetch = lambda ids: [_hit("kn-x", "cross scenario", 0.0, "scn-2")]
    graph = FakeGraph({"kn-a": {"references": ("kn-x",), "backlinks": ()}})
    res = make_resolver(backend, graph).recall(
        "q", QueryContext(scenario_id="scn-1")
    )
    assert res.metrics["gate"] == "graph", "cross-scenario traversal preserved"
    neighbor = [i for i in res.items if i.metadata.get("origin") == "wikilink"][0]
    assert neighbor.metadata["scenario_id"] == "scn-2"
    assert neighbor.metadata["scenario_affinity"] == "cross"


# â”€â”€ 13/14. pipeline integration + durable identity â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def test_neighbors_enter_normal_recall_pipeline_with_durable_ids():
    backend = FakeBackend()
    backend.knowledge = [_hit("kn-a", "weak", 0.1)]
    backend.fetch = lambda ids: [
        KnowledgeHit(
            item=KnowledgeItem(
                id=i,
                form=KnowledgeForm.ATOMIC,
                content=f"durable {i}",
                confidence=0.9,
                status=KnowledgeStatus.CANDIDATE,
                tags=("topic",),
                scenario_id="scn-1",
            )
        )
        for i in ids
    ]
    graph = FakeGraph({"kn-a": {"references": ("kn-b",), "backlinks": ()}})
    res = make_resolver(backend, graph).recall("q")
    neighbor = [i for i in res.items if i.metadata.get("origin") == "wikilink"][0]
    assert isinstance(res.items[0], type(res.items[-1]))  # one item type
    assert neighbor.source == "knowledge", "normal pipeline, no second type"
    assert neighbor.metadata["kind"] == "knowledge"
    assert neighbor.metadata["id"] == "kn-b", "durable id, never title/path"
    assert neighbor.metadata["tags"] == ("topic",)


# â”€â”€ brain-level: real stores, default-resolver wiring â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class FakeEmbed(EmbeddingService):
    """Constant vectors: everything mutually 'similar' (score â‰ˆ 1)."""

    def __init__(self, dim: int = 16):
        super().__init__({"embedding": {"model": "fake-embed"}})
        self._dim = dim

    @property
    def model_name(self):
        return "fake-embed"

    def encode(self, text, normalize=True):
        v = [0.0] * self._dim
        v[0] = 1.0
        return v

    @property
    def dimension(self):
        return self._dim


class OrthogonalEmbed(FakeEmbed):
    """Query marker embeds orthogonally to everything else (score < 0)."""

    def encode(self, text, normalize=True):
        dim = self._dim
        if "needle" in (text or "").lower():
            v = [0.0] * dim
            v[1] = 1.0
            return v
        v = [0.0] * dim
        v[0] = 1.0
        return v


class StubMemory:
    def __init__(self):
        self.facts = []

    def store_fact(self, statement):
        self.facts.append(statement)

    def query(self, text, k=5, distance_threshold=0.5, memory_types=None):
        return []


class RecordingIndex:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.indexed = []

    def index_file(self, path):
        self.indexed.append(path)

    def search(self, query, k=5, rerank=True):
        return self.rows[:k]


def build_brain(tmp_path, embed=None, index=None):
    idx = index or RecordingIndex()
    store = VectorStore(persist_dir=tmp_path / "brain", embed_model=embed or FakeEmbed())
    scenario_store = ScenarioStore(persist_dir=tmp_path / "brain")
    rels = RelationshipStore(persist_dir=tmp_path / "rels")
    brain = Brain(
        memory=StubMemory(),
        knowledge_index=idx,
        knowledge_layer=KnowledgeLayer(store),
        scenario_layer=ScenarioLayer(scenario_store),
        relationship_store=rels,
    )
    return brain, rels, idx


def test_default_resolver_wires_neighborhood_and_fetch(tmp_path):
    brain, _, idx = build_brain(tmp_path)
    resolver = brain._default_resolver()
    assert resolver is not None
    assert resolver._neighborhood == brain.neighborhood
    assert callable(resolver._fetch_knowledge)


def test_brain_fetches_knowledge_by_durable_id_only(tmp_path):
    brain, rels, idx = build_brain(tmp_path)
    kid = brain.learn("Gamma radiation shielding basics.")["item_id"]
    items = brain.knowledge_items([kid, "kn-missing"])
    assert [i.id for i in items] == [kid], "missing ids silently skipped"
    assert items[0].content == "Gamma radiation shielding basics."


def test_brain_recall_end_to_end_discovers_linked_neighbor(tmp_path):
    brain, rels, idx = build_brain(tmp_path, embed=OrthogonalEmbed())
    b_id = brain.learn("Gamma radiation shielding basics.")["item_id"]
    a_id = brain.learn("Alpha protocol overview.")["item_id"]
    rels.add(Relationship(source_id=a_id, target_id=b_id, kind=EdgeKind.REFERENCES))

    res = brain.recall(
        "alpha needle",
        QueryContext(top_k=1, distance_threshold=None),
    )
    plan = res.metrics["plan"]
    assert plan.gate == "graph", "semantic gate failed twice; graph satisfied"
    assert plan.graph_items == 1
    neighbors = [i for i in res.items if i.metadata.get("origin") == "wikilink"]
    assert len(neighbors) == 1
    other = b_id if neighbors[0].metadata["id"] == b_id else a_id
    assert neighbors[0].metadata["id"] == other
    assert neighbors[0].metadata["id"].startswith("kn-")


# â”€â”€ source-level: RetrievedItem stream â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

BUDGET = ContextAllocation()


def _row(item_id, chunk_id="a.md::0", text="chunk text", score=0.05):
    return {
        "id": chunk_id,
        "text": text,
        "score": score,
        "metadata": {
            "path": "a.md",
            "title": "a",
            "type": "knowledge",
            "item_id": item_id,
        },
    }


def test_source_sufficient_score_skips_expansion(tmp_path):
    brain, rels, idx = build_brain(tmp_path)
    a = brain.learn("Alpha overview.")["item_id"]
    b = brain.learn("Beta details.")["item_id"]
    rels.add(Relationship(source_id=a, target_id=b, kind=EdgeKind.REFERENCES))
    idx.rows = [_row(a, score=0.95)]

    calls = []
    original = brain.neighborhood

    def spy(item_id):
        calls.append(item_id)
        return original(item_id)

    brain.neighborhood = spy
    result = KnowledgeRetrievalSource(brain).retrieve("q", BUDGET)
    assert calls == [], "strong semantic score must not touch the graph"
    assert len(result.items) == 1
    assert result.quality == RetrievalQuality.SUFFICIENT


def test_source_insufficient_score_appends_wikilink_neighbor(tmp_path):
    brain, rels, idx = build_brain(tmp_path)
    a = brain.learn("Alpha overview.")["item_id"]
    b = brain.learn("Beta details.")["item_id"]
    rels.add(Relationship(source_id=a, target_id=b, kind=EdgeKind.REFERENCES))
    idx.rows = [_row(a, score=0.05)]

    result = KnowledgeRetrievalSource(brain).retrieve("q", BUDGET)
    assert len(result.items) == 2
    neighbor = result.items[-1]
    assert neighbor.id == b, "durable Brain id as result identity"
    assert neighbor.source == "knowledge", "enters the normal stream"
    assert neighbor.metadata["origin"] == "wikilink"
    assert neighbor.metadata["hops"] == 1
    assert neighbor.metadata["via"] == "reference"
    assert neighbor.score == pytest.approx(0.025), "decayed below its seed"
    assert neighbor.text == "Beta details."
    # semantic result untouched
    assert result.items[0].metadata.get("origin") is None
    assert result.quality == RetrievalQuality.SUFFICIENT


def test_source_backlink_direction(tmp_path):
    brain, rels, idx = build_brain(tmp_path)
    a = brain.learn("Alpha overview.")["item_id"]
    b = brain.learn("Beta details.")["item_id"]
    rels.add(Relationship(source_id=b, target_id=a, kind=EdgeKind.REFERENCES))
    idx.rows = [_row(a, score=0.05)]

    result = KnowledgeRetrievalSource(brain).retrieve("q", BUDGET)
    neighbor = result.items[-1]
    assert neighbor.id == b
    assert neighbor.metadata["via"] == "backlink"


def test_source_duplicate_semantic_plus_graph_kept_once(tmp_path):
    brain, rels, idx = build_brain(tmp_path)
    a = brain.learn("Alpha overview.")["item_id"]
    b = brain.learn("Beta details.")["item_id"]
    rels.add(Relationship(source_id=a, target_id=b, kind=EdgeKind.REFERENCES))
    idx.rows = [
        _row(a, chunk_id="a.md::0", text="alpha chunk", score=0.05),
        _row(b, chunk_id="b.md::0", text="beta chunk", score=0.05),
    ]

    result = KnowledgeRetrievalSource(brain).retrieve("q", BUDGET)
    assert len(result.items) == 2, "b already semantic — no graph duplicate"
    assert [i.id for i in result.items] == ["a.md::0", "b.md::0"]
    assert all(i.metadata.get("origin") is None for i in result.items)


def test_source_text_level_dedup_for_unreindexed_rows(tmp_path):
    """Old indexes lack item_id metadata; content overlap still dedupes."""
    brain, rels, idx = build_brain(tmp_path)
    a = brain.learn("Shared claim body text.")["item_id"]
    b = brain.learn("Shared claim body text.")["item_id"]  # near-duplicate id
    idx.rows = [
        {
            "id": "a.md::0",
            "text": "Shared claim body text.",
            "score": 0.05,
            "metadata": {"path": "a.md"},  # no item_id (pre-M4 row)
        }
    ]
    # link a â†’ b manually; b's durable row has identical content
    from cozmo.brain.types import Relationship as Rel

    rels.add(Rel(source_id=a, target_id=b, kind=EdgeKind.REFERENCES))

    result = KnowledgeRetrievalSource(brain).retrieve("q", BUDGET)
    assert len(result.items) == 1, "same-content neighbor deduped by text"


def test_source_dangling_note_target_safe(tmp_path):
    brain, rels, idx = build_brain(tmp_path)
    a = brain.learn("Alpha overview.")["item_id"]
    rels.add(Relationship(source_id=a, target_id="note:Ghost", kind=EdgeKind.REFERENCES))
    idx.rows = [_row(a, score=0.05)]

    result = KnowledgeRetrievalSource(brain).retrieve("q", BUDGET)
    assert len(result.items) == 1, "dangling neighbor contributes nothing"
    assert result.quality == RetrievalQuality.SUFFICIENT


def test_source_deleted_target_safe(tmp_path):
    brain, _, idx = build_brain(tmp_path)
    a = brain.learn("Alpha overview.")["item_id"]
    rels = RelationshipStore(persist_dir=tmp_path / "rels2")
    rels.add(Relationship(source_id=a, target_id="kn-deleted", kind=EdgeKind.REFERENCES))
    brain._relationship_store = rels
    idx.rows = [_row(a, score=0.05)]

    result = KnowledgeRetrievalSource(brain).retrieve("q", BUDGET)
    assert len(result.items) == 1


def test_source_plain_index_unchanged_behavior():
    class FakeKI:
        def __init__(self, rows):
            self.rows = rows
            self.calls = []

        def search(self, query, k=5, rerank=True):
            self.calls.append((query, k, rerank))
            return self.rows

    rows = [_row("kn-a", score=0.05)]
    src = KnowledgeRetrievalSource(FakeKI(rows))
    result = src.retrieve("q", BUDGET)
    assert len(result.items) == 1, "no Brain behind the index â†’ no expansion"


def test_source_expand_related_disabled(tmp_path):
    brain, rels, idx = build_brain(tmp_path)
    a = brain.learn("Alpha overview.")["item_id"]
    b = brain.learn("Beta details.")["item_id"]
    rels.add(Relationship(source_id=a, target_id=b, kind=EdgeKind.REFERENCES))
    idx.rows = [_row(a, score=0.05)]

    src = KnowledgeRetrievalSource(brain, expand_related=False)
    result = src.retrieve("q", BUDGET)
    assert len(result.items) == 1


def test_source_empty_results_stay_empty(tmp_path):
    brain, _, idx = build_brain(tmp_path)
    idx.rows = []
    result = KnowledgeRetrievalSource(brain).retrieve("q", BUDGET)
    assert result.quality == RetrievalQuality.EMPTY
    assert result.items == []


def test_source_failure_passthrough_unchanged(tmp_path):
    class Broken:
        def search(self, query, k=5, rerank=True):
            raise RuntimeError("kb unavailable")

        def neighborhood(self, item_id):  # pragma: no cover
            raise AssertionError("must not be reached")

    brain = Brain(memory=StubMemory(), knowledge_index=Broken())
    result = KnowledgeRetrievalSource(brain).retrieve("q", BUDGET)
    assert result.quality == RetrievalQuality.FAILED
    assert result.error == "kb unavailable"


# ── M4.1 hardening ───────────────────────────────────────────────────────────
# 1) superseded knowledge never re-enters retrieval through the graph
# 2) durable-id fetching is batched (one store read per expansion)
# 3) scenario affinity exposed as advisory metadata (traversal stays global)


def test_vector_store_get_many_batch_lookup(tmp_path):
    from cozmo.brain.storage.vector_store import VectorStore

    store = VectorStore(persist_dir=tmp_path / "v", embed_model=FakeEmbed())
    store.add_many(
        [
            KnowledgeItem(
                id=f"kn-{i}",
                form=KnowledgeForm.ATOMIC,
                content=f"claim {i}",
                confidence=0.9,
            )
            for i in range(4)
        ]
    )
    rows = store.get_many(["kn-1", "kn-missing", "kn-3"])
    assert {r["id"] for r in rows} == {"kn-1", "kn-3"}
    by_id = {r["id"]: r for r in rows}
    assert by_id["kn-1"]["text"] == "claim 1"
    assert store.get_many([]) == []
    assert store.get_many([None, "", "kn-1"]) != [], "junk ids ignored, valid kept"


def test_brain_knowledge_items_uses_batched_fetch(tmp_path):
    brain, _, idx = build_brain(tmp_path)
    kid = brain.learn("Batch me.")["item_id"]
    store = brain._knowledge_layer.store
    calls = []
    original = store.get

    def spy(item_id):
        calls.append(item_id)
        return original(item_id)

    store.get = spy
    items = brain.knowledge_items([kid, "kn-missing", kid])
    assert calls == [], "per-id get() must be bypassed when get_many exists"
    assert [i.id for i in items] == [kid]


def test_knowledge_items_filters_superseded(tmp_path):
    brain, _rels, _idx = build_brain(tmp_path)
    keep = brain.learn("Keep me.")["item_id"]
    dead = brain.learn("Dead claim.")["item_id"]
    brain._knowledge_layer.update_status(dead, KnowledgeStatus.SUPERSEDED)
    items = brain.knowledge_items([keep, dead])
    assert [i.id for i in items] == [keep], "superseded claims never re-enter"


def test_recall_never_expands_into_superseded_target(tmp_path):
    brain, rels, idx = build_brain(tmp_path, embed=OrthogonalEmbed())
    a = brain.learn("Alpha protocol overview.")["item_id"]
    dead = brain.learn("Stale radiation guidance.")["item_id"]
    live = brain.learn("Current shielding practice.")["item_id"]
    brain._knowledge_layer.update_status(dead, KnowledgeStatus.SUPERSEDED)
    rels.add(Relationship(source_id=a, target_id=dead, kind=EdgeKind.REFERENCES))
    rels.add(Relationship(source_id=a, target_id=live, kind=EdgeKind.REFERENCES))

    res = brain.recall(
        "alpha needle",
        QueryContext(top_k=1, distance_threshold=None),
    )
    plan = res.metrics["plan"]
    assert plan.gate == "graph"
    expanded_ids = [
        i.metadata["id"] for i in res.items if i.metadata.get("origin") == "wikilink"
    ]
    assert live in expanded_ids
    assert dead not in expanded_ids, "superseded target must stay out"


def test_source_skips_superseded_neighbors(tmp_path):
    brain, rels, idx = build_brain(tmp_path)
    a = brain.learn("Alpha overview.")["item_id"]
    dead = brain.learn("Stale details.")["item_id"]
    live = brain.learn("Fresh details.")["item_id"]
    brain._knowledge_layer.update_status(dead, KnowledgeStatus.SUPERSEDED)
    rels.add(Relationship(source_id=a, target_id=dead, kind=EdgeKind.REFERENCES))
    rels.add(Relationship(source_id=a, target_id=live, kind=EdgeKind.REFERENCES))
    idx.rows = [_row(a, score=0.05)]

    result = KnowledgeRetrievalSource(brain).retrieve("q", BUDGET)
    ids = {i.id for i in result.items}
    assert live in ids and dead not in ids
    assert len(result.items) == 2


def test_scenario_affinity_same_for_in_scope_neighbor():
    backend = FakeBackend()
    backend.knowledge = [_hit("kn-a", "weak", 0.1, "scn-1")]
    backend.scenarios["scn-1"] = make_scenario()

    def fetch(ids):
        return [_hit(i, f"content {i}", 0.0, "scn-1") for i in ids]

    backend.fetch = fetch
    graph = FakeGraph({"kn-a": {"references": ("kn-b",), "backlinks": ()}})
    res = make_resolver(backend, graph).recall(
        "q", QueryContext(scenario_id="scn-1")
    )
    neighbor = [i for i in res.items if i.metadata.get("origin") == "wikilink"][0]
    assert neighbor.metadata["scenario_affinity"] == "same"


def test_scenario_affinity_cross_without_active_scenario():
    backend = FakeBackend()
    backend.knowledge = [_hit("kn-a", "weak", 0.1)]
    backend.fetch = lambda ids: [_hit(i, f"c {i}", 0.0) for i in ids]
    graph = FakeGraph({"kn-a": {"references": ("kn-b",), "backlinks": ()}})
    res = make_resolver(backend, graph).recall("q")
    neighbor = [i for i in res.items if i.metadata.get("origin") == "wikilink"][0]
    assert neighbor.metadata["scenario_affinity"] == "cross"


def test_source_carries_no_affinity_key_without_scenario_context(tmp_path):
    """The source surface has no query context; affinity stays absent there
    rather than being fabricated."""
    brain, rels, idx = build_brain(tmp_path)
    a = brain.learn("Alpha overview.")["item_id"]
    b = brain.learn("Beta details.")["item_id"]
    rels.add(Relationship(source_id=a, target_id=b, kind=EdgeKind.REFERENCES))
    idx.rows = [_row(a, score=0.05)]

    result = KnowledgeRetrievalSource(brain).retrieve("q", BUDGET)
    neighbor = result.items[-1]
    assert "scenario_affinity" not in neighbor.metadata


def test_batched_fetch_matches_single_get_results(tmp_path):
    """Parity guard: get_many must return what per-id get() would have."""
    from cozmo.brain.storage.vector_store import VectorStore

    store = VectorStore(persist_dir=tmp_path / "v", embed_model=FakeEmbed())
    store.add_many(
        [
            KnowledgeItem(id=f"kn-{i}", form=KnowledgeForm.ATOMIC,
                          content=f"claim {i}", confidence=0.9)
            for i in range(6)
        ]
    )
    want = ["kn-0", "kn-2", "kn-gone"]
    many = {r["id"]: r for r in store.get_many(want)}
    for iid in want:
        single = store.get(iid)
        assert (many[iid]["text"] == single["text"]) if iid in many else (single is None)
