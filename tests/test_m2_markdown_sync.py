"""M2 — Markdown ↔ Brain knowledge synchronization regression tests.

Covers the durable synchronization layer:

* Brain → Markdown: ``Brain.learn`` write-through, configured workspace,
  deterministic identity, idempotency, metadata/provenance preservation.
* Markdown → Brain: reconciliation of user-authored notes, edit detection
  without duplication, formatting-only tolerance, deletion never hard-deletes.
* Durability: LanceDB (the retrieval index) can be lost/rebuild while the
  Markdown + Brain durable representation survives.
* Failure behavior: a Markdown write failure is surfaced, never silently
  reported as synced.
* Extraction integration: extracted knowledge enters the SAME markdown path
  as ``Brain.learn`` (one writer).
* Relationship prep: WikiLinks materialize ``references`` edges (M2 minimal).
"""

import shutil
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from cozmo.brain import (
    Brain,
    EdgeKind,
    KnowledgeItem,
    KnowledgeStatus,
    Turn,
)
from cozmo.brain.layers.knowledge import KnowledgeLayer
from cozmo.brain.layers.scenarios import ScenarioLayer
from cozmo.brain.reasoning.extraction import ExtractedClaim, ExtractionResult
from cozmo.brain.storage.markdown_store import MarkdownStore
from cozmo.brain.storage.relationship_store import RelationshipStore
from cozmo.brain.storage.scenario_store import ScenarioStore
from cozmo.brain.storage.vector_store import VectorStore
from cozmo.services.embedding import EmbeddingService


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
    """KnowledgeIndex-like fake that records index_file calls."""

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


def _build_brain(
    tmp_path,
    *,
    kb=None,
    persist=None,
    markdown=None,
    index=None,
):
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
    return brain


def _read_frontmatter(path):
    from cozmo.memory.okf import parse_okf_file

    meta, body = parse_okf_file(path)
    return meta, body


def _rmtree(path):
    for _ in range(5):
        try:
            shutil.rmtree(path, ignore_errors=True)
            return
        except Exception:
            time.sleep(0.05)


# ── Brain → Markdown ──────────────────────────────────────────────────────


def test_learn_creates_markdown(tmp_path):
    kb = tmp_path / "kb"
    brain = _build_brain(tmp_path, kb=kb)
    brain.learn("The user prefers python over java.")

    files = list(kb.rglob("*.md"))
    assert len(files) == 1
    meta, body = _read_frontmatter(files[0])
    assert meta["id"].startswith("kn-")
    assert meta["status"] == KnowledgeStatus.VERIFIED.value
    assert meta["confidence"] == 1.0
    assert meta["type"] == "atomic"
    assert body == "The user prefers python over java."


def test_learn_respects_configured_workspace(tmp_path):
    configured = tmp_path / "configured-kb"
    with patch(
        "cozmo.brain.storage.markdown_store.get_configuration",
        return_value=_StubConfig({"workspace": {"knowledge": str(configured)}}),
    ):
        md = MarkdownStore()  # no explicit dir → config decides
    assert md.knowledge_dir == configured.resolve()

    brain = _build_brain(tmp_path, markdown=md)
    brain.learn("The build uses uv.")
    assert not (tmp_path / "knowledge").exists()
    assert not (tmp_path / "kb").exists()
    assert len(list(configured.rglob("*.md"))) == 1


def test_learn_deterministic_identity(tmp_path):
    kb = tmp_path / "kb"
    brain = _build_brain(tmp_path, kb=kb)
    r1 = brain.learn("The user prefers python over java.")
    r2 = brain.learn("The user prefers python over java.")

    assert r1["markdown"]["path"] == r2["markdown"]["path"]
    assert len(list(kb.rglob("*.md"))) == 1
    assert r1["markdown"]["created"] is True
    assert r2["markdown"]["created"] is False


def test_repeated_learn_is_idempotent(tmp_path):
    kb = tmp_path / "kb"
    brain = _build_brain(tmp_path, kb=kb)
    brain.learn("The user prefers python.")
    brain.learn("The user prefers python.")

    files = list(kb.rglob("*.md"))
    assert len(files) == 1
    # Brain keeps append-only history (two verified items), markdown one mirror
    assert brain._knowledge_layer.store.count() == 2
    meta, _ = _read_frontmatter(files[0])
    assert meta["status"] == KnowledgeStatus.VERIFIED.value


def test_learn_metadata_preserved(tmp_path):
    kb = tmp_path / "kb"
    brain = _build_brain(tmp_path, kb=kb)
    brain.learn("The user prefers python over java.", source="preference")

    files = list(kb.rglob("*.md"))
    meta, _ = _read_frontmatter(files[0])
    assert "preference" in meta["tags"]
    assert "identity" in meta["tags"]
    assert meta["status"] == "verified"
    assert meta["confidence"] == 1.0
    assert meta["importance"] == 0.0
    assert meta["source_kind"] == "explicit"
    assert isinstance(meta["timestamp"], str)
    assert isinstance(meta["updated"], str)


def test_learn_provenance_user_authored(tmp_path):
    kb = tmp_path / "kb"
    brain = _build_brain(tmp_path, kb=kb)
    brain.learn("The build uses uv.", source="knowledge")

    files = list(kb.rglob("*.md"))
    meta, _ = _read_frontmatter(files[0])
    assert meta["source_kind"] == "user_authored"


def test_learn_report_surfaces_markdown(tmp_path):
    brain = _build_brain(tmp_path)
    report = brain.learn("A durable claim here.")
    assert report["ok"] is True
    assert report["item_id"].startswith("kn-")
    assert report["markdown"]["written"] is True
    assert report["markdown"]["created"] is True
    assert report["markdown"]["path"].endswith(".md")


def test_learn_indexes_affected_file(tmp_path):
    kb = tmp_path / "kb"
    index = RecordingIndex()
    brain = _build_brain(tmp_path, kb=kb, index=index)
    report = brain.learn("The build uses uv.")
    assert len(index.indexed) == 1
    assert index.indexed[0].resolve() == (kb / report["markdown"]["path"]).resolve()


# ── Markdown → Brain ──────────────────────────────────────────────────────


def test_reconcile_detects_new_knowledge(tmp_path):
    kb = tmp_path / "kb"
    brain = _build_brain(tmp_path, kb=kb)
    note = kb / "notes" / "go-services.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\ntype: Learning\ntitle: Go services\n---\n\nThe user likes Go for services.\n",
        encoding="utf-8",
    )

    report = brain.reconcile_markdown()
    assert report.new >= 1
    assert report.scanned == 1

    items = brain._knowledge_layer.list_objects()
    assert any(i.content == "The user likes Go for services." for i in items)
    meta, _ = _read_frontmatter(note)
    assert meta["id"].startswith("kn-")  # note now carries a Brain identity
    assert len(list(kb.rglob("*.md"))) == 1  # no second file


def test_reconcile_edit_does_not_duplicate(tmp_path):
    kb = tmp_path / "kb"
    brain = _build_brain(tmp_path, kb=kb)
    brain.learn("The user prefers python.")
    files = list(kb.rglob("*.md"))
    assert len(files) == 1
    old_meta, _ = _read_frontmatter(files[0])
    old_id = old_meta["id"]

    # A real user edit: frontmatter (identity) intact, body changed.
    files[0].write_text(
        "---\ntype: atomic\ntitle: Python\nid: %s\nidentity: %s\n---\n\n"
        "The user prefers python 3.12.\n" % (old_meta["id"], old_meta["identity"]),
        encoding="utf-8",
    )

    report = brain.reconcile_markdown()
    assert report.edited == 1

    files = list(kb.rglob("*.md"))
    assert len(files) == 1  # edit re-identified the same file — no duplicate

    meta, body = _read_frontmatter(files[0])
    assert body == "The user prefers python 3.12."
    new_id = meta["id"]
    assert new_id != old_id

    by_id = {i.id: i for i in brain._knowledge_layer.list_objects()}
    assert by_id[old_id].status is KnowledgeStatus.SUPERSEDED
    assert by_id[new_id].content == "The user prefers python 3.12."

    edges = brain._relationship_store.outgoing(new_id)
    supersedes = [e for e in edges if e.kind is EdgeKind.SUPERSEDES]
    assert len(supersedes) == 1
    assert supersedes[0].target_id == old_id


def test_reconcile_deletion_keeps_brain_history(tmp_path):
    kb = tmp_path / "kb"
    brain = _build_brain(tmp_path, kb=kb)
    report = brain.learn("The user prefers python.")
    item_id = report["item_id"]
    file_path = kb / report["markdown"]["path"]
    assert file_path.exists()

    file_path.unlink()

    reconcile = brain.reconcile_markdown()
    assert reconcile.missing_files == 1

    # The Brain item is NOT hard-deleted — it remains historical.
    rows = brain._knowledge_layer.store.list_all(limit=100)
    assert any(r["id"] == item_id for r in rows)
    item = brain._knowledge_layer.store.item_from_row(
        next(r for r in rows if r["id"] == item_id)
    )
    assert item.status is KnowledgeStatus.VERIFIED
    assert item.content == "The user prefers python."


def test_reconcile_formatting_only_no_duplicate(tmp_path):
    kb = tmp_path / "kb"
    brain = _build_brain(tmp_path, kb=kb)
    report = brain.learn("The user prefers python.")
    item_id = report["item_id"]
    file_path = kb / report["markdown"]["path"]
    original, _ = _read_frontmatter(file_path)

    # Formatting-only edit: identity untouched, only emphasis/whitespace change.
    file_path.write_text(
        "---\ntype: atomic\ntitle: Python\nid: %s\nidentity: %s\n---\n\n"
        "**The user** prefers `python`.\n" % (original["id"], original["identity"]),
        encoding="utf-8",
    )

    reconcile = brain.reconcile_markdown()
    assert reconcile.unchanged == 1
    assert reconcile.new == 0
    assert reconcile.edited == 0

    rows = brain._knowledge_layer.store.list_all(limit=100)
    assert len(rows) == 1  # no new knowledge created
    assert rows[0]["id"] == item_id


# ── Durability ────────────────────────────────────────────────────────────


def test_durable_survives_index_removal_and_rebuild(tmp_path):
    kb = tmp_path / "kb"
    persist = tmp_path / "brain-index"
    brain = _build_brain(tmp_path, kb=kb, persist=persist)
    report = brain.learn("The user prefers python for builds.")
    md_path = kb / report["markdown"]["path"]
    assert md_path.exists()
    meta_before, body_before = _read_frontmatter(md_path)

    # LanceDB (the retrieval index) is removed entirely.
    brain._knowledge_layer.store.close()
    _rmtree(persist)

    # A fresh Brain + rebuilt index reconciles the durable representation back.
    fresh = _build_brain(tmp_path, kb=kb, persist=persist)
    reconcile = fresh.reconcile_markdown()
    assert reconcile.new >= 1

    items = fresh._knowledge_layer.list_objects()
    assert any(i.content == "The user prefers python for builds." for i in items)
    # The durable claim survives: same file, same semantic identity, same body.
    # (The frontmatter ``id`` churns — a fresh Brain re-identifies the note.)
    assert md_path.exists()
    meta_after, body_after = _read_frontmatter(md_path)
    assert body_after == body_before
    assert meta_after["identity"] == meta_before["identity"]
    assert meta_after["status"] == KnowledgeStatus.VERIFIED.value


def test_markdown_readable_independently(tmp_path):
    kb = tmp_path / "kb"
    brain = _build_brain(tmp_path, kb=kb)
    brain.learn("The build uses uv.")

    files = list(kb.rglob("*.md"))
    md = MarkdownStore(knowledge_dir=kb)
    item = md.read_item(files[0])
    assert item is not None
    assert item.content == "The build uses uv."
    assert item.id.startswith("kn-")
    assert item.status is KnowledgeStatus.VERIFIED
    # No Brain/vector involvement — the file is self-contained.
    assert "The build uses uv." in files[0].read_text(encoding="utf-8")


def test_brain_state_available_independently(tmp_path):
    brain = _build_brain(tmp_path, kb=tmp_path / "unused-kb")
    report = brain.learn("The user prefers python.")
    rows = brain._knowledge_layer.store.list_all(limit=100)
    assert len(rows) == 1
    assert rows[0]["id"] == report["item_id"]
    assert rows[0]["status"] == KnowledgeStatus.VERIFIED.value
    assert rows[0]["metadata"]["confidence"] == 1.0


# ── Failure behavior ──────────────────────────────────────────────────────


class FailingMarkdownStore(MarkdownStore):
    def write_item(self, item, *, source_kind="explicit"):
        raise OSError("disk full")


def test_markdown_write_failure_is_surfaced(tmp_path):
    kb = tmp_path / "kb"
    brain = _build_brain(tmp_path, kb=kb, markdown=FailingMarkdownStore(knowledge_dir=kb))
    report = brain.learn("A claim that cannot reach markdown.")

    # Brain persistence succeeded...
    assert report["ok"] is True
    assert report["item_id"].startswith("kn-")
    # ...and the Markdown failure is surfaced, not silently claimed as synced.
    assert report["markdown"]["written"] is False
    assert "error" in report["markdown"]

    items = brain._knowledge_layer.list_objects()
    assert len(items) == 1  # durable knowledge still in Brain


def test_index_failure_does_not_hide_markdown_success(tmp_path):
    class BrokenIndex(RecordingIndex):
        def index_file(self, path):
            raise RuntimeError("index down")

    brain = _build_brain(tmp_path, kb=tmp_path / "kb", index=BrokenIndex())
    report = brain.learn("A claim with a broken index.")
    assert report["markdown"]["written"] is True  # mirror written; index failure is ancillary


# ── Extraction integration (one writer) ───────────────────────────────────


def test_extraction_write_through_same_markdown_path(tmp_path):
    kb = tmp_path / "kb"
    brain = _build_brain(tmp_path, kb=kb)
    brain._extractor = StubExtractor()
    for i in range(5):
        brain.observe(Turn(user=f"u{i}", assistant=f"a{i}", conversation_id="conv-m2"))

    # 2 atomic claims + 1 composite summary → 3 mirror files (no separate path).
    files = list(kb.rglob("*.md"))
    assert len(files) == 3
    bodies = {b for _, b in (_read_frontmatter(f) for f in files)}
    assert "The user prefers python over java." in bodies
    assert "The build uses uv." in bodies
    assert "Summary of the conversation." in bodies


def test_extraction_corroboration_does_not_duplicate_files(tmp_path):
    kb = tmp_path / "kb"
    brain = _build_brain(tmp_path, kb=kb)
    brain.learn("The user prefers python over java.")
    files_before = len(list(kb.rglob("*.md")))

    # A second batch restating the same claim corroborates it → no new files.
    brain._extractor = StubExtractor()
    for i in range(5):
        brain.observe(Turn(user=f"u{i}", assistant=f"a{i}", conversation_id="conv-m2b"))

    files_after = list(kb.rglob("*.md"))
    assert len(files_after) == files_before + 2  # second claim + summary only


# ── Relationship preparation (minimal WikiLinks) ──────────────────────────


def test_wikilink_references_edges_on_create_only(tmp_path):
    kb = tmp_path / "kb"
    brain = _build_brain(tmp_path, kb=kb)
    r1 = brain.learn("Python is popular. See [[Python]] and [[Rust|the fast one]].")
    edges = brain._relationship_store.outgoing(r1["item_id"], kind=EdgeKind.REFERENCES)
    targets = {e.target_id for e in edges}
    assert targets == {"note:Python", "note:Rust"}

    # Re-learning the same claim updates the mirror, never duplicates edges.
    r2 = brain.learn("Python is popular. See [[Python]] and [[Rust|the fast one]].")
    assert r2["markdown"]["created"] is False
    edges2 = brain._relationship_store.outgoing(r2["item_id"], kind=EdgeKind.REFERENCES)
    assert len(edges2) == 0  # creation-only: no new edges for the new item


# ── correct_memory write-through ──────────────────────────────────────────


def test_correct_memory_supersession_annotates_markdown(tmp_path):
    kb = tmp_path / "kb"
    brain = _build_brain(tmp_path, kb=kb)
    report = brain.learn("The user prefers python.")
    old_id = report["item_id"]
    old_file = kb / report["markdown"]["path"]

    correction = brain.correct_memory(old_id, statement="The user prefers python 3.13.")
    assert correction["ok"] is True

    # Old claim's mirror annotated as superseded.
    meta, _ = _read_frontmatter(old_file)
    assert meta["status"] == KnowledgeStatus.SUPERSEDED.value

    # New claim has its own verified mirror.
    new_id = correction["recorded"]
    new_file = kb / (brain._markdown_store.find_for_id(new_id).relative_to(kb))
    meta2, body2 = _read_frontmatter(new_file)
    assert meta2["status"] == KnowledgeStatus.VERIFIED.value
    assert body2 == "The user prefers python 3.13."

    edges = brain._relationship_store.outgoing(new_id, kind=EdgeKind.SUPERSEDES)
    assert [e.target_id for e in edges] == [old_id]