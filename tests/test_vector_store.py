"""Phase D — VectorStore (typed-column LanceDB) tests.

KnowledgeItems persist with promoted typed columns; filters are column
predicates (scenario_id, source_kind, form IN, list_contains on tags), not
metadata string-matching. Rows still carry a metadata dict for consumers.
"""

import pytest

from novi.brain.storage.vector_store import VectorStore
from novi.brain.types import KnowledgeForm, KnowledgeItem, KnowledgeStatus
from novi.services.embedding import EmbeddingService


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
    return VectorStore(persist_dir=tmp_path, embed_model=FakeEmbed())


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
    assert meta["source_kind"] == "extraction"


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


def test_query_scenario_column_predicate(store):
    store.add_many(
        [
            item(id="kn-1", scenario_id="scn-1"),
            item(id="kn-2", scenario_id="scn-2"),
        ]
    )
    results = store.query("anything", k=5, scenario_id="scn-1")
    assert [r["id"] for r in results] == ["kn-1"]


def test_query_source_kind_column_predicate(store):
    store.add_many(
        [item(id="kn-1"), item(id="kn-2", content="Another claim here.")]
    )
    store.add(item(id="kn-3", content="Learned fact.", scenario_id="scn-9"), source_kind="learn")
    results = store.query("anything", k=5, source_kind="learn")
    assert [r["id"] for r in results] == ["kn-3"]


def test_query_tag_list_contains(store):
    store.add_many(
        [
            item(id="kn-1", tags=("preference",)),
            item(id="kn-2", tags=("project",)),
        ]
    )
    results = store.query("anything", k=5, tags=("preference",))
    assert [r["id"] for r in results] == ["kn-1"]


def test_query_form_in_predicate(store):
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


def test_query_combined_predicates(store):
    store.add_many(
        [
            item(id="kn-1", tags=("preference",), scenario_id="scn-1"),
            item(id="kn-2", tags=("preference",), scenario_id="scn-2"),
            item(id="kn-3", tags=("project",), scenario_id="scn-1"),
        ]
    )
    results = store.query("anything", k=5, scenario_id="scn-1", tags=("preference",))
    assert [r["id"] for r in results] == ["kn-1"]


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
    dirpath = str(tmp_path / "brain")
    store = VectorStore(persist_dir=dirpath, embed_model=FakeEmbed())
    store.add(item())
    store.close()
    reopened = VectorStore(persist_dir=dirpath, embed_model=FakeEmbed())
    assert reopened.count() == 1
    assert reopened.get("kn-1") is not None
    reopened.close()


def test_item_from_row_restores_item(store):
    store.add(item())
    row = store.get("kn-1")
    restored = VectorStore.item_from_row(row)
    assert restored.id == "kn-1"
    assert restored.form is KnowledgeForm.ATOMIC
    assert restored.status is KnowledgeStatus.CANDIDATE
    assert restored.tags == ("preference",)
    assert restored.sources == ("conv-1",)
    assert restored.scenario_id == "scn-1"


def test_typed_columns_are_top_level(tmp_path):
    store = VectorStore(persist_dir=tmp_path, embed_model=FakeEmbed())
    store.add(item())
    row = store.get("kn-1")
    assert row["scenario_id"] == "scn-1"
    assert row["form"] == "atomic"
    assert row["tags"] == ["preference"]
