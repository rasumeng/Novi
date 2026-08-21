"""M3 — WikiLink resolution + knowledge relationship edges regression tests.

Covers the spec: resolution (deterministic, no fuzzy), actual durable-identity
edges, backlinks, re-index diffing/idempotency, lifecycle (dangling/target-
created-deleted-recreated/source-deleted), aliases, Obsidian compatibility, and
retrieval-preparation (relationship lookup returns actual knowledge identity;
traversal needs no second storage system).
"""

import shutil
import yaml
from pathlib import Path
from unittest.mock import patch

from cozmo.brain import Brain, EdgeKind, KnowledgeStatus, Turn
from cozmo.brain.layers.knowledge import KnowledgeLayer
from cozmo.brain.layers.scenarios import ScenarioLayer
from cozmo.brain.reasoning.extraction import ExtractedClaim, ExtractionResult
from cozmo.brain.storage.markdown_store import MarkdownStore
from cozmo.brain.storage.relationship_store import RelationshipStore
from cozmo.brain.storage.scenario_store import ScenarioStore
from cozmo.brain.storage.vector_store import VectorStore
from cozmo.brain.wikilinks import (
    ResolutionStatus,
    build_note_index,
    parse_wikilinks,
)
from cozmo.services.embedding import EmbeddingService


# ── fakes ────────────────────────────────────────────────────────────────────


class FakeEmbed(EmbeddingService):
    def __init__(self, dim: int = 64):
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


class StubMemory:
    def __init__(self):
        self.facts = []

    def store_fact(self, statement):
        self.facts.append(statement)


class RecordingIndex:
    def __init__(self):
        self.indexed = []

    def index_file(self, path):
        self.indexed.append(Path(path))

    def search(self, query, k=5, rerank=True):
        return []


class StubExtractor:
    def extract(self, turns):
        return ExtractionResult(
            claims=(
                ExtractedClaim("The user prefers python over java.", 0.9, ("preference",)),
                ExtractedClaim("The build uses uv.", 0.8, ("tool",)),
            ),
            summary="Summary of the conversation.",
            name="Conversation title",
        )


class _StubConfig:
    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        node = self._data
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def snapshot(self):
        return dict(self._data)


def _build_brain(tmp_path, *, kb=None, persist=None, markdown=None, index=None):
    kb = Path(kb) if kb else tmp_path / "kb"
    persist = Path(persist) if persist else tmp_path / "brain"
    store = VectorStore(persist_dir=persist, embed_model=FakeEmbed())
    scenario_store = ScenarioStore(persist_dir=persist)
    markdown_store = markdown or MarkdownStore(knowledge_dir=kb)
    rels = RelationshipStore(persist_dir=tmp_path / "rels")
    brain = Brain(
        memory=StubMemory(),
        knowledge_index=index or RecordingIndex(),
        markdown_store=markdown_store,
        knowledge_layer=KnowledgeLayer(store),
        scenario_layer=ScenarioLayer(scenario_store),
        relationship_store=rels,
    )
    return brain, markdown_store, rels


def _note(kb: Path, rel: str, body: str = "body", **fm) -> Path:
    """Write a Markdown note with explicit frontmatter (incl. id/aliases)."""
    path = kb / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = dict(fm)
    header = "---\n"
    if meta:
        header += yaml.dump(meta, sort_keys=False, allow_unicode=True)
    header += "---\n\n"
    path.write_text(header + body, encoding="utf-8")
    return path


def _refs(rels, source_id):
    return {e.target_id for e in rels.outgoing(source_id, kind=EdgeKind.REFERENCES)}


# ── Resolution ───────────────────────────────────────────────────────────────


def test_wikilink_resolves_to_actual_knowledge_identity(tmp_path):
    """A resolved link targets the Brain item id, not note:<Title>."""
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "python.md", "Python is a language.",
          type="atomic", title="Python", id="kn-python")
    # Learner creates the source note + extracts the link.
    report = brain.learn("See [[Python]].")
    rel = report["markdown"]["path"]
    # Now resolve the source note's link against the full index.
    brain.sync_wikilinks()
    edges = _refs(rels, report["item_id"])
    assert "kn-python" in edges
    assert "note:Python" not in edges


def test_wikilink_resolves_by_canonical_title(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "b.md", "b", type="atomic", title="Python", id="kn-python")
    src = _note(tmp_path / "kb", "src.md", "link [[Python]]", type="atomic",
                title="Src", id="kn-src")
    brain.sync_wikilinks()
    assert _refs(rels, "kn-src") == {"kn-python"}


def test_wikilink_resolves_by_normalized_title(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "go-services.md", "g", type="atomic",
          title="Go Services", id="kn-go")
    src = _note(tmp_path / "kb", "src.md", "see [[go services]]",
                type="atomic", title="Src", id="kn-src")
    brain.sync_wikilinks()
    assert _refs(rels, "kn-src") == {"kn-go"}


def test_wikilink_resolves_by_path(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "notes/deep.md", "d", type="atomic",
          title="Deep Thing", id="kn-deep")
    _note(tmp_path / "kb", "src.md", "see [[notes/deep]]",
          type="atomic", title="Src", id="kn-src")
    brain.sync_wikilinks()
    assert _refs(rels, "kn-src") == {"kn-deep"}


def test_wikilink_resolves_by_path_with_ext(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "deep.md", "d", type="atomic", title="Deep", id="kn-deep")
    _note(tmp_path / "kb", "src.md", "see [[deep.md]]",
          type="atomic", title="Src", id="kn-src")
    brain.sync_wikilinks()
    assert _refs(rels, "kn-src") == {"kn-deep"}


def test_wikilink_resolves_by_note_identity_field(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "deep.md", "d", type="atomic", title="Deep",
          id="kn-deep", identity="sha-abc-123")
    _note(tmp_path / "kb", "src.md", "see [[sha-abc-123]]",
          type="atomic", title="Src", id="kn-src")
    brain.sync_wikilinks()
    assert _refs(rels, "kn-src") == {"kn-deep"}


def test_wikilink_alias_resolves_to_canonical_target(tmp_path):
    """[[Model Runtime|runtime]] → canonical kn id; alias is not an identity."""
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "mr.md", "m", type="atomic", title="Model Runtime", id="kn-mr")
    _note(tmp_path / "kb", "src.md", "use [[Model Runtime|runtime]]",
          type="atomic", title="Src", id="kn-src")
    brain.sync_wikilinks()
    edges = _refs(rels, "kn-src")
    assert edges == {"kn-mr"}            # canonical target, not "runtime"
    assert "note:runtime" not in edges


def test_wikilink_resolves_by_alias_exact(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "mr.md", "m", type="atomic", title="Model Runtime",
          id="kn-mr", aliases=["runtime"])
    _note(tmp_path / "kb", "src.md", "use [[runtime]]",
          type="atomic", title="Src", id="kn-src")
    brain.sync_wikilinks()
    assert _refs(rels, "kn-src") == {"kn-mr"}


def test_wikilink_ambiguous_title_does_not_guess(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "docs/memory.md", "d", type="atomic", title="Memory", id="kn-1")
    _note(tmp_path / "kb", "notes/memory.md", "n", type="atomic", title="Memory", id="kn-2")
    _note(tmp_path / "kb", "src.md", "two [[Memory]] notes", type="atomic", id="kn-src")
    report = brain.sync_wikilinks()
    # No edge created — ambiguous, left unresolved.
    assert _refs(rels, "kn-src") == set()
    assert "Memory" in report.ambiguous


def test_wikilink_unresolved_is_dangling(tmp_path):
    """No matching note → note:<Title> edge (dangling, recoverable)."""
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "src.md", "future [[Future Project]]",
          type="atomic", title="Src", id="kn-src")
    brain.sync_wikilinks()
    assert _refs(rels, "kn-src") == {"note:Future Project"}


# ── Relationships: outgoing / incoming / dedup / deletion / stale ───────────


def test_outgoing_reference_is_edge_to_knowledge(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "b.md", "b", type="atomic", title="B", id="kn-b")
    _note(tmp_path / "kb", "a.md", "see [[B]]", type="atomic", title="A", id="kn-a")
    brain.sync_wikilinks()
    edges = rels.outgoing("kn-a", kind=EdgeKind.REFERENCES)
    assert len(edges) == 1
    assert edges[0].target_id == "kn-b"


def test_backlinks_incoming_discovery(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "b.md", "b", type="atomic", title="B", id="kn-b")
    _note(tmp_path / "kb", "a.md", "see [[B]]", type="atomic", title="A", id="kn-a")
    brain.sync_wikilinks()
    assert set(brain.backlinks("kn-b")) == {"kn-a"}
    assert brain.backlinks("kn-b") == tuple(brain.backlinks("kn-b"))


def test_duplicate_prevention_sync_is_idempotent(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "b.md", "b", type="atomic", title="B", id="kn-b")
    _note(tmp_path / "kb", "a.md", "see [[B]]", type="atomic", title="A", id="kn-a")
    brain.sync_wikilinks()
    first = rels.count()
    report2 = brain.sync_wikilinks()
    assert rels.count() == first
    assert report2.added == 0
    assert report2.removed == 0


def test_removing_wikilink_removes_edge(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "b.md", "b", type="atomic", title="B", id="kn-b")
    a = _note(tmp_path / "kb", "a.md", "see [[B]]", type="atomic", title="A", id="kn-a")
    brain.sync_wikilinks()
    assert _refs(rels, "kn-a") == {"kn-b"}
    # User edits the source note to drop the link.
    a.write_text("---\ntype: atomic\ntitle: A\nid: kn-a\n---\n\nno links now.",
                 encoding="utf-8")
    brain.sync_wikilinks()
    assert _refs(rels, "kn-a") == set()


def test_unrelated_relationships_preserved_across_sync(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "b.md", "b", type="atomic", title="B", id="kn-b")
    _note(tmp_path / "kb", "a.md", "see [[B]]", type="atomic", title="A", id="kn-a")
    brain.sync_wikilinks()
    # A manually-authored supersedes provenance edge must remain untouched.
    from cozmo.brain.types import Relationship
    rels.add_many([Relationship(source_id="kn-a", target_id="kn-b",
                                 kind=EdgeKind.SUPERSEDES)])
    report = brain.sync_wikilinks()
    assert rels.has("kn-a", "kn-b", EdgeKind.REFERENCES)
    assert rels.has("kn-a", "kn-b", EdgeKind.SUPERSEDES)  # preserved


def test_stale_edge_upgrades_when_target_recreated(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "src.md", "see [[B]]", type="atomic", title="Src", id="kn-src")
    brain.sync_wikilinks()
    assert _refs(rels, "kn-src") == {"note:B"}
    # A matching note appears later → dangling upgrades to durable identity.
    _note(tmp_path / "kb", "b.md", "b", type="atomic", title="B", id="kn-b")
    brain.sync_wikilinks()
    assert _refs(rels, "kn-src") == {"kn-b"}


# ── Lifecycle ───────────────────────────────────────────────────────────────


def test_dangling_link_recoverable_when_note_appears(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "src.md", "see [[Future Project]]",
          type="atomic", title="Src", id="kn-src")
    brain.sync_wikilinks()
    assert _refs(rels, "kn-src") == {"note:Future Project"}
    _note(tmp_path / "kb", "future-project.md", "p", type="atomic",
          title="Future Project", id="kn-future")
    brain.sync_wikilinks()
    assert _refs(rels, "kn-src") == {"kn-future"}


def test_target_deleted_becomes_dangling(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "b.md", "b", type="atomic", title="B", id="kn-b")
    _note(tmp_path / "kb", "src.md", "see [[B]]", type="atomic", title="Src", id="kn-src")
    brain.sync_wikilinks()
    assert _refs(rels, "kn-src") == {"kn-b"}
    (tmp_path / "kb" / "b.md").unlink()
    brain.sync_wikilinks()
    assert _refs(rels, "kn-src") == {"note:B"}


def test_source_deleted_removes_outgoing_references(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "b.md", "b", type="atomic", title="B", id="kn-b")
    a = _note(tmp_path / "kb", "a.md", "see [[B]]", type="atomic", title="A", id="kn-a")
    brain.sync_wikilinks()
    assert _refs(rels, "kn-a") == {"kn-b"}
    a.unlink()
    brain.sync_wikilinks()
    assert _refs(rels, "kn-a") == set()


def test_repeated_indexing_is_idempotent(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "b.md", "b", type="atomic", title="B", id="kn-b")
    _note(tmp_path / "kb", "src.md", "see [[B]]", type="atomic", title="Src", id="kn-src")
    counts = []
    for _ in range(3):
        brain.sync_wikilinks()
        counts.append(rels.count())
    assert counts[0] == counts[1] == counts[2]


def test_wikilink_creation_time_resolution_via_learn(tmp_path):
    """Brain.learn write-through resolves links at mirror creation (M2 path
    upgraded to real resolution, not note:<Title> when a match exists)."""
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "rust.md", "r", type="atomic", title="Rust", id="kn-rust")
    report = brain.learn("See [[Rust]] and [[Missing]].")
    edges = _refs(rels, report["item_id"])
    assert "kn-rust" in edges            # resolved to durable identity
    assert "note:Missing" in edges       # dangling preserved as M2 form


def test_ambiguous_at_creation_time_is_left_unresolved(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "docs/m.md", "d", type="atomic", title="M", id="kn-1")
    _note(tmp_path / "kb", "notes/m.md", "n", type="atomic", title="M", id="kn-2")
    report = brain.learn("Two [[M]] notes.")
    # Creation-time sync sees ambiguity -> no edge for that link.
    assert "note:M" not in _refs(rels, report["item_id"])


# ── Obsidian compatibility ──────────────────────────────────────────────────


def test_obidian_plain_wikilink_syntax_preserved(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    report = brain.learn("Discuss [[Python]] here.")
    rel = tmp_path / "kb" / report["markdown"]["path"]
    text = rel.read_text(encoding="utf-8")
    assert "[[Python]]" in text            # untouched in the file
    # And it materialized as the M2 dangling form (no target note exists).
    assert _refs(rels, report["item_id"]) == {"note:Python"}


def test_obidian_alias_syntax_preserved(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "python.md", "p", type="atomic", title="Python", id="kn-python")
    report = brain.learn("Discuss [[Python|the snake]] here.")
    rel = tmp_path / "kb" / report["markdown"]["path"]
    text = rel.read_text(encoding="utf-8")
    assert "[[Python|the snake]]" in text   # alias presentation untouched
    assert _refs(rels, report["item_id"]) == {"kn-python"}  # resolved to canonical


def test_frontmatter_preserved_by_sync(tmp_path):
    kb = tmp_path / "kb"
    brain, md, rels = _build_brain(tmp_path, kb=kb)
    a = _note(kb, "a.md", "see [[B]]", type="atomic", title="A",
              id="kn-a", tags=["user", "alpha"], identity="user-identity-key")
    b = _note(kb, "b.md", "b", type="atomic", title="B", id="kn-b", aliases=["beta"])
    brain.sync_wikilinks()
    a_fm, _ = md.parse(a)
    assert a_fm["id"] == "kn-a"
    assert a_fm["tags"] == ["user", "alpha"]        # user tags untouched
    assert a_fm["identity"] == "user-identity-key"
    assert a_fm["title"] == "A"
    b_fm, _ = md.parse(b)
    assert b_fm["aliases"] == ["beta"]              # target aliases untouched
    assert b_fm["id"] == "kn-b"


def test_user_created_links_survive_sync(tmp_path):
    """Sync never overwrites the user's WikiLink text."""
    kb = tmp_path / "kb"
    brain, md, rels = _build_brain(tmp_path, kb=kb)
    _note(kb, "b.md", "b", type="atomic", title="B", id="kn-b")
    a = _note(kb, "a.md", "manual link [[B]] plus [[C]]", type="atomic",
              title="A", id="kn-a")
    brain.sync_wikilinks()
    text = a.read_text(encoding="utf-8")
    assert "[[B]]" in text and "[[C]]" in text   # body untouched
    assert _refs(rels, "kn-a") == {"kn-b", "note:C"}  # B resolved, C dangling
    # Re-running doesn't mutate the body.
    before = a.read_text(encoding="utf-8")
    brain.sync_wikilinks()
    assert a.read_text(encoding="utf-8") == before


def test_user_note_with_wikilinks_learned_then_synced(tmp_path):
    """A user-authored note (no id) is learned, then its links resolve."""
    kb = tmp_path / "kb"
    brain, md, rels = _build_brain(tmp_path, kb=kb)
    b = _note(kb, "b.md", "b", type="atomic", title="B", id="kn-b")
    user = _note(kb, "user.md", "ref [[B]]", type="Reference", title="User")
    brain.reconcile_markdown()
    user_fm, _ = md.parse(user)
    b_fm, _ = md.parse(b)
    # reconcile learns user.md (assigns an id) and resolves its [[B]] link to
    # B's note identity (whatever id B now carries after reconciliation churn).
    assert user_fm["id"] is not None
    assert b_fm["id"] is not None
    assert _refs(rels, user_fm["id"]) == {b_fm["id"]}


# ── Retrieval preparation ───────────────────────────────────────────────────


def test_relationship_lookup_returns_actual_knowledge_identity(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "b.md", "b", type="atomic", title="B", id="kn-b")
    _note(tmp_path / "kb", "a.md", "see [[B]]", type="atomic", title="A", id="kn-a")
    brain.sync_wikilinks()
    edges = rels.outgoing("kn-a", kind=EdgeKind.REFERENCES)
    targets = {e.target_id for e in edges}
    assert "kn-b" in targets              # actual Brain identity
    assert targets == {"kn-b"}           # single edge, no note:B placeholder


def test_neighborhood_traversal_without_second_store(tmp_path):
    brain, _, rels = _build_brain(tmp_path)
    _note(tmp_path / "kb", "c.md", "c", type="atomic", title="C", id="kn-c")
    _note(tmp_path / "kb", "a.md", "see [[C]]", type="atomic", title="A", id="kn-a")
    brain.sync_wikilinks()
    nb = brain.neighborhood("kn-a")
    assert nb["references"] == ("kn-c",)
    nb2 = brain.neighborhood("kn-c")
    assert nb2["backlinks"] == ("kn-a",)
    # No second storage: the only store wired is relationship_store.
    assert rels is brain._relationship_store


def test_build_note_index_is_rebuildable_from_markdown(tmp_path):
    """The resolver index is derived from Markdown only — no second store."""
    brain, md, _ = _build_brain(tmp_path)
    _note(tmp_path / "kb", "b.md", "b", type="atomic", title="B", id="kn-b")
    index = build_note_index(md)
    res = index.resolve(parse_wikilinks("[[B]]")[0])
    assert res.status is ResolutionStatus.RESOLVED
    assert res.target_id == "kn-b"
