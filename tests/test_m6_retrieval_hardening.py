"""Post-M5 retrieval hardening — superseded knowledge cannot leak.

Closes the last active leak paths found by the post-M5 audit:

  1. ``VectorStore.query`` (the semantic primitive behind the resolver's
     scoped/global stages) returned superseded rows; tiering masked this
     only when the ``tiered`` flag was on. Exclusion now lives at the store
     read boundary, mirroring ``tier_hits``: read-path only, never a DELETE,
     with an explicit ``include_superseded`` escape hatch for history/audit.
  2. Knowledge-index chunks carried no lifecycle status, so superseded notes
     kept serving stale chunks. Chunk metadata now mirrors frontmatter
     ``status`` at index time and ``search`` drops superseded rows. Rows
     indexed before the mirror existed lack the key and pass through until
     their next mtime-triggered re-index (documented, self-healing).

Append-only semantics untouched: rows are never mutated or deleted by these
paths; ``list_all``-based consumers (reflection, projection, inspect_memory)
keep full history visibility by design.
"""

from __future__ import annotations

import inspect

import pytest

from novi.brain import Brain, QueryContext
from novi.brain.layers.knowledge import KnowledgeLayer
from novi.brain.layers.scenarios import ScenarioLayer
from novi.brain.reasoning.resolver import LayeredRetrievalResolver
from novi.brain.storage.relationship_store import RelationshipStore
from novi.brain.storage.scenario_store import ScenarioStore
from novi.brain.storage.vector_store import VectorStore
from novi.brain.types import (
    EdgeKind,
    KnowledgeForm,
    KnowledgeItem,
    KnowledgeStatus,
    Relationship,
)
from novi.memory.knowledge_index import KnowledgeIndex
from novi.services.embedding import EmbeddingService


class FakeEmbed(EmbeddingService):
    """Constant vectors: everything mutually 'similar'."""

    def __init__(self, dim: int = 16):
        super().__init__({"embedding": {"model": "fake-embed"}})
        self._dim = dim

    @property
    def model_name(self):
        return "fake-embed"

    def encode(self, text, normalize=True):
        return [0.01] * self._dim

    @property
    def dimension(self):
        return self._dim


def _item(item_id, content, status=KnowledgeStatus.VERIFIED, confidence=0.9):
    return KnowledgeItem(
        id=item_id,
        form=KnowledgeForm.ATOMIC,
        content=content,
        confidence=confidence,
        status=status,
    )


# ── 1. store boundary ────────────────────────────────────────────────────────


def _store(tmp_path):
    store = VectorStore(persist_dir=tmp_path / "v", embed_model=FakeEmbed())
    store.add_many([
        _item("kn-live", "live claim about python"),
        _item("kn-dead", "dead claim about python",
              status=KnowledgeStatus.SUPERSEDED),
    ])
    return store


def test_query_excludes_superseded_by_default(tmp_path):
    store = _store(tmp_path)
    rows = store.query("python claim", k=10, distance_threshold=None)
    assert [r["id"] for r in rows] == ["kn-live"], "superseded must not leak"


def test_include_superseded_flag_restores_history_reads(tmp_path):
    store = _store(tmp_path)
    rows = store.query(
        "python claim", k=10, distance_threshold=None, include_superseded=True
    )
    assert {r["id"] for r in rows} == {"kn-live", "kn-dead"}


def test_supersede_after_add_reflects_immediately(tmp_path):
    store = VectorStore(persist_dir=tmp_path / "v", embed_model=FakeEmbed())
    store.add_many([_item("kn-a", "alpha fact"), _item("kn-b", "beta fact")])
    assert len(store.query("fact", k=10, distance_threshold=None)) == 2
    store.update_status("kn-a", KnowledgeStatus.SUPERSEDED)
    rows = store.query("fact", k=10, distance_threshold=None)
    assert [r["id"] for r in rows] == ["kn-b"], "read-path exclusion is live"
    # History intact — nothing deleted:
    assert store.count() == 2
    assert store.get("kn-a") is not None


def test_point_lookups_unchanged(tmp_path):
    """get/get_many stay policy-free point lookups; Brain owns fetch policy."""
    store = _store(tmp_path)
    assert store.get("kn-dead")["id"] == "kn-dead"
    ids = {r["id"] for r in store.get_many(["kn-live", "kn-dead"])}
    assert ids == {"kn-live", "kn-dead"}


def test_query_signature_pins_the_escape_hatch():
    sig = inspect.signature(VectorStore.query)
    assert "include_superseded" in sig.parameters
    assert sig.parameters["include_superseded"].default is False


# ── 2. knowledge-index chunk mirror ──────────────────────────────────────────


class _KiEmbed(FakeEmbed):
    def __init__(self, dim: int = 384):
        super().__init__(dim)


def _make_index(tmp_path, files: dict[str, str]) -> KnowledgeIndex:
    kd = tmp_path / "knowledge"
    kd.mkdir(exist_ok=True)
    for name, content in files.items():
        (kd / name).write_text(content, encoding="utf-8")
    ki = KnowledgeIndex(
        knowledge_dir=kd,
        persist_dir=str(tmp_path / "idx"),
        embed_model=_KiEmbed(),
    )
    ki.index_all(force=True)
    return ki


def test_index_drops_superseded_chunk_at_search_time(tmp_path):
    ki = _make_index(tmp_path, {
        "live.md": "---\ntitle: Live\nid: kn-live\n---\n\nLive python facts.",
        "dead.md": (
            "---\ntitle: Dead\nid: kn-dead\nstatus: superseded\n---\n\n"
            "Dead python facts."
        ),
    })
    results = ki.search("python facts", k=10)
    paths = {r["metadata"]["path"] for r in results}
    assert "dead.md" not in paths, "superseded chunk must not serve queries"
    assert "live.md" in paths


def test_index_rows_without_status_key_pass_through(tmp_path):
    """Legacy rows (indexed pre-mirror) keep serving until re-index."""
    ki = _make_index(tmp_path, {
        "old.md": "---\ntitle: Old\nid: kn-old\n---\n\nLegacy python facts.",
    })
    # A genuine pre-mirror row: stored without any status key.
    ki.store.add_texts(
        ["Ancient python facts."],
        [{"path": "ancient.md", "title": "Ancient", "type": "knowledge"}],
        ids=["ancient.md::0"],
    )
    results = ki.search("python facts", k=10)
    paths = {r["metadata"]["path"] for r in results}
    assert "ancient.md" in paths, "status-less legacy rows must keep serving"


def test_reindex_refreshes_status_mirror(tmp_path):
    kd = tmp_path / "knowledge"
    kd.mkdir(exist_ok=True)
    f = kd / "flip.md"
    f.write_text(
        "---\ntitle: Flip\nid: kn-flip\nstatus: verified\n---\n\nFlip facts.",
        encoding="utf-8",
    )
    ki = KnowledgeIndex(
        knowledge_dir=kd,
        persist_dir=str(tmp_path / "idx"),
        embed_model=_KiEmbed(),
    )
    ki.index_all(force=True)
    assert ki.search("flip facts", k=5)

    f.write_text(
        "---\ntitle: Flip\nid: kn-flip\nstatus: superseded\n---\n\nFlip facts.",
        encoding="utf-8",
    )
    ki.index_file(f)
    assert ki.search("flip facts", k=5) == [], "re-index heals the mirror"


# ── 3. end-to-end: non-tiered resolver path ──────────────────────────────────


class StubMemory:
    def store_fact(self, statement):
        pass

    def query(self, text, k=5, distance_threshold=0.5, memory_types=None):
        return []


class RecordingIndex:
    def index_file(self, path):
        pass

    def search(self, query, k=5, rerank=True):
        return []


def _brain(tmp_path):
    store = VectorStore(persist_dir=tmp_path / "brain", embed_model=FakeEmbed())
    brain = Brain(
        memory=StubMemory(),
        knowledge_index=RecordingIndex(),
        knowledge_layer=KnowledgeLayer(store),
        scenario_layer=ScenarioLayer(ScenarioStore(persist_dir=tmp_path / "brain")),
        relationship_store=RelationshipStore(persist_dir=tmp_path / "rels"),
    )
    return brain


def test_non_tiered_resolver_never_surfaces_superseded(tmp_path):
    """Tiering used to be the only guard; the store boundary now holds even
    with tiering off."""
    brain = _brain(tmp_path)
    a = brain.learn("Alpha protocol overview.")["item_id"]
    b = brain.learn("Beta legacy guidance.")["item_id"]
    brain._knowledge_layer.update_status(b, KnowledgeStatus.SUPERSEDED)

    resolver = LayeredRetrievalResolver(
        load_scenario=lambda sid: None,
        query_knowledge=brain._knowledge_layer.query_scoped,
        query_memory=lambda q, k, t: [],
        neighborhood=brain.neighborhood,
        fetch_knowledge=brain._fetch_knowledge_hits,
        tiered=False,  # the old leak regime
    )
    res = resolver.recall("overview guidance", QueryContext(distance_threshold=None))
    ids = [i.metadata.get("id") for i in res.items if i.source == "knowledge"]
    assert b not in ids, "superseded item leaked through non-tiered semantic path"
    assert a in ids


def test_graph_expansion_skips_superseded_neighbor_end_to_end(tmp_path):
    brain = _brain(tmp_path)
    seed = brain.learn("Hub note.")["item_id"]
    live = brain.learn("Live linked detail.")["item_id"]
    dead = brain.learn("Dead linked detail.")["item_id"]
    brain._knowledge_layer.update_status(dead, KnowledgeStatus.SUPERSEDED)
    rels = brain._relationship_store
    rels.add(Relationship(source_id=seed, target_id=live, kind=EdgeKind.REFERENCES))
    rels.add(Relationship(source_id=seed, target_id=dead, kind=EdgeKind.REFERENCES))

    items = brain.knowledge_items([seed, live, dead])
    assert {i.id for i in items} == {seed, live}, "fetch boundary still filters"
