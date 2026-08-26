"""Embedding-dimension migration tests.

Reopening a LanceDB-backed store with a different embedding dimension than
the table was created at used to crash every write with an Arrow cast error
(Cannot cast to FixedSizeList(N): value has length M). The stores must now
rebuild the table at the configured dimension, backing up old rows.
"""

from pathlib import Path

from novi.brain.storage.vector_store import VectorStore
from novi.brain.types import KnowledgeForm, KnowledgeItem, KnowledgeStatus
from novi.memory.lancedb_store import LanceStore
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


# ── LanceStore (knowledge_index / memories flat tables) ──────────────────

def test_lancestore_rebuilds_on_dimension_change(tmp_path):
    uri = str(tmp_path / "lancedb")

    old = LanceStore(uri=uri, table_name="knowledge_index",
                     embed_func=lambda t: FakeEmbed(dim=4).encode(t),
                     embed_dim=4, embed_model="old-model", vector_index=False)
    old.add_texts(["hello world"], [{"path": "a.md"}], ids=["a.md::0"])

    new = LanceStore(uri=uri, table_name="knowledge_index",
                     embed_func=lambda t: FakeEmbed(dim=8).encode(t),
                     embed_dim=8, embed_model="new-model", vector_index=False)

    assert new.count() == 0
    new.add_texts(["fresh text"], [{"path": "b.md"}], ids=["b.md::0"])
    assert new.count() == 1

    names = set(new._db.table_names())
    assert any(n.startswith("knowledge_index_old_dim4_") for n in names)


def test_lancestore_empty_table_rebuilds_without_backup(tmp_path):
    uri = str(tmp_path / "lancedb")

    LanceStore(uri=uri, table_name="t", embed_func=lambda t: [0.0] * 4,
               embed_dim=4, vector_index=False)
    rebuilt = LanceStore(uri=uri, table_name="t", embed_func=lambda t: [0.0] * 8,
                         embed_dim=8, vector_index=False)

    assert rebuilt.count() == 0
    assert set(rebuilt._db.table_names()) == {"t"}


# ── VectorStore (Brain typed knowledge table) ─────────────────────────────

def _item(kid: str) -> KnowledgeItem:
    return KnowledgeItem(
        id=kid,
        form=KnowledgeForm.ATOMIC,
        content=f"fact {kid}",
        confidence=0.9,
        status=KnowledgeStatus.CANDIDATE,
        tags=("t",),
        sources=("conv-1",),
        scenario_id="scn-1",
    )


def test_vectorstore_rebuilds_on_dimension_change(tmp_path):
    persist = tmp_path / "brain"

    old = VectorStore(persist_dir=persist, embed_model=FakeEmbed(dim=16))
    old.add(_item("kn-1"))
    assert old.get("kn-1") is not None

    new = VectorStore(persist_dir=persist, embed_model=FakeEmbed(dim=32))

    assert new.count() == 0
    assert new.get("kn-1") is None

    new.add(_item("kn-2"))
    assert new.get("kn-2") is not None

    db_dir = Path(persist) / "lancedb"
    import lancedb
    names = set(lancedb.connect(str(db_dir)).table_names())
    assert any(n.startswith("knowledge_items_old_dim16_") for n in names)


def test_vectorstore_same_dimension_reopens_in_place(tmp_path):
    """No dimension change: normal reopen path — data intact, no backups."""
    import lancedb

    persist = tmp_path / "brain"

    store = VectorStore(persist_dir=persist, embed_model=FakeEmbed(dim=16))
    store.add(_item("kn-1"))

    reopened = VectorStore(persist_dir=persist, embed_model=FakeEmbed(dim=16))
    assert reopened.count() == 1
    assert reopened.get("kn-1") is not None
    assert not any(
        n.startswith("knowledge_items_old_dim")
        for n in lancedb.connect(str(Path(persist) / "lancedb")).table_names()
    )
