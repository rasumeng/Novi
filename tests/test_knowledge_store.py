"""Phase C — KnowledgeStore (LanceDB) tests.

Flat-schema storage of KnowledgeItems: add/query/delete round-trips, soft tag
and form filters. Phase D migrates to typed columns; these pin current
behavior only.
"""

import pytest

from cozmo.brain.storage.knowledge_store import KnowledgeStore
from cozmo.brain.types import KnowledgeForm, KnowledgeItem, KnowledgeStatus
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


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(persist_dir=tmp_path, embed_model=FakeEmbed())


def item(**overrides):
    fields = dict(
        id="kn-1",
        form=KnowledgeForm.ATOMIC,
        content="The user prefers Python over Java.",
        confidence=0.9,
        status=KnowledgeStatus.CANDIDATE,
        tags=("preference",),
        sources=("conv-1",),
        scenario_id="scn-1",
    )
    fields.update(overrides)
    return KnowledgeItem(**fields)


def test_add_and_get_round_trip(store):
    store.add(item())
    row = store.get("kn-1")
    assert row is not None
    assert row["id"] == "kn-1"
    meta = row["metadata"]
    assert meta["form"] == "atomic"
    assert meta["status"] == "candidate"
    assert meta["tags"] == ["preference"]
    assert meta["scenario_id"] == "scn-1"
    assert meta["sources"] == ["conv-1"]


def test_add_many_returns_ids(store):
    ids = store.add_many(
        [item(id="kn-a"), item(id="kn-b", content="The build uses uv for packaging.")]
    )
    assert ids == ["kn-a", "kn-b"]
    assert store.count() == 2


def test_query_returns_matching_items(store):
    store.add_many(
        [
            item(id="kn-1", content="The user prefers Python over Java."),
            item(id="kn-2", content="The database schema was refactored for payments."),
        ]
    )
    results = store.query("python preference", k=5)
    assert len(results) >= 1
    assert any(r["id"] == "kn-1" for r in results)


def test_query_tag_filter(store):
    store.add_many(
        [
            item(id="kn-1", tags=("preference",)),
            item(id="kn-2", tags=("project",)),
        ]
    )
    results = store.query("anything", k=5, tags=("preference",))
    assert [r["id"] for r in results] == ["kn-1"]


def test_query_form_filter(store):
    store.add_many(
        [
            item(id="kn-1", form=KnowledgeForm.ATOMIC),
            item(
                id="kn-2",
                form=KnowledgeForm.COMPOSITE,
                content="A long composite scenario summary.",
            ),
        ]
    )
    results = store.query("summary", k=5, forms=(KnowledgeForm.COMPOSITE,))
    assert [r["id"] for r in results] == ["kn-2"]


def test_get_unknown_returns_none(store):
    assert store.get("kn-missing") is None


def test_delete(store):
    store.add(item())
    assert store.delete("kn-1") is True
    assert store.get("kn-1") is None
    assert store.count() == 0


def test_count(store):
    assert store.count() == 0
    store.add(item())
    store.add(item(id="kn-2", content="Another claim here."))
    assert store.count() == 2


def test_reopen_retains_data(tmp_path):
    dirpath = str(tmp_path / "convs")
    store = KnowledgeStore(persist_dir=dirpath, embed_model=FakeEmbed())
    store.add(item())
    store.close()
    reopened = KnowledgeStore(persist_dir=dirpath, embed_model=FakeEmbed())
    assert reopened.count() == 1
    assert reopened.get("kn-1") is not None
    reopened.close()


def test_metadata_round_trip_via_item_from_row(store):
    store.add(item())
    row = store.get("kn-1")
    restored = KnowledgeStore.item_from_row(row)
    assert restored.id == "kn-1"
    assert restored.form is KnowledgeForm.ATOMIC
    assert restored.status is KnowledgeStatus.CANDIDATE
    assert restored.tags == ("preference",)
    assert restored.sources == ("conv-1",)
    assert restored.scenario_id == "scn-1"
