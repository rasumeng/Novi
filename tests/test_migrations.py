"""Phase D — flat → typed knowledge migration tests."""

from pathlib import Path
from os import fspath

from novi.brain.storage.migrations import migrate
from novi.brain.storage.vector_store import VectorStore
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


def _seed_flat(persist_dir, rows):
    persist_dir = fspath(persist_dir)
    embed = FakeEmbed()

    def f(text):
        return embed.encode(text)

    store = LanceStore(
        uri=str(Path(persist_dir) / "lancedb"),
        table_name="novi_knowledge",
        embed_func=f,
        embed_dim=embed.dimension,
        embed_model="fake-embed",
        vector_index=False,
    )
    texts = []
    metas = []
    ids = []
    for i, row in enumerate(rows):
        ids.append(row["id"])
        texts.append(row["text"])
        meta = {
            "kind": "knowledge",
            "form": row.get("form", "atomic"),
            "status": row.get("status", "candidate"),
            "confidence": row.get("confidence", 0.9),
            "tags": row.get("tags", []),
            "sources": row.get("sources", ["conv-1"]),
            "scenario_id": row.get("scenario_id"),
            "created_at": row.get("created_at", "2026-08-03T00:00:00"),
            "embed_model": "fake-embed",
        }
        metas.append(meta)
    store.add_texts(texts, metas, ids=ids)


def test_migrate_flat_rows_into_typed(tmp_path):
    persist_dir = str(tmp_path / "brain")
    _seed_flat(
        persist_dir,
        [
            {"id": "kn-1", "text": "User prefers Python.", "tags": ["preference"], "scenario_id": "scn-1"},
            {"id": "kn-2", "text": "Build uses uv.", "tags": ["project"], "scenario_id": "scn-1"},
        ],
    )

    result = migrate(persist_dir, embed_model=FakeEmbed())
    assert result["migrated"] == 2
    assert result["dropped_flat"] is True

    typed = VectorStore(persist_dir=persist_dir, embed_model=FakeEmbed())
    assert typed.count() == 2
    row = typed.get("kn-1")
    assert row is not None
    assert row["metadata"]["tags"] == ["preference"]
    assert row["metadata"]["scenario_id"] == "scn-1"
    typed.close()


def test_migrate_preserves_fields(tmp_path):
    persist_dir = str(tmp_path / "brain")
    _seed_flat(
        persist_dir,
        [
            {
                "id": "kn-1",
                "text": "A composite scenario summary.",
                "form": "composite",
                "status": "verified",
                "confidence": 0.8,
                "tags": ["conversation"],
                "sources": ["conv-9"],
                "scenario_id": "scn-9",
            }
        ],
    )
    migrate(persist_dir, embed_model=FakeEmbed())
    typed = VectorStore(persist_dir=persist_dir, embed_model=FakeEmbed())
    meta = typed.get("kn-1")["metadata"]
    assert meta["form"] == "composite"
    assert meta["status"] == "verified"
    assert meta["sources"] == ["conv-9"]
    assert meta["scenario_id"] == "scn-9"
    typed.close()


def test_migrate_idempotent_when_no_flat_table(tmp_path):
    persist_dir = str(tmp_path / "brain")
    result = migrate(persist_dir, embed_model=FakeEmbed())
    assert result == {"migrated": 0, "dropped_flat": False}
