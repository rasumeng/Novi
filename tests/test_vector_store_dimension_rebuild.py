"""Opening a store must not trigger a dimension rebuild when dims match.

Regression: every boot created a ``knowledge_items_old_dim768_<stamp>``
backup and re-embedded the whole corpus (~39 docs, ~183s of embedding calls)
even though the stored width and the configured width were both 768.
"""

import json
import tempfile
from pathlib import Path

import pyarrow as pa


def _make_table(db, name, dim, rows=3, embed_model="nomic-embed-text:v1.5"):
    schema = pa.schema([
        pa.field("id", pa.string()),
        pa.field("text", pa.string()),
        pa.field("metadata", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), dim)),
    ])
    data = [
        {"id": f"r{i}", "text": f"doc {i}",
         "metadata": json.dumps({"embed_model": embed_model}),
         "vector": [0.1] * dim}
        for i in range(rows)
    ]
    return db.create_table(name, schema=schema, data=data)


def test_matching_dimension_does_not_rebuild(tmp_path):
    import lancedb

    from novi.memory.lancedb_store import LanceStore

    db = lancedb.connect(str(tmp_path / "lance"))
    _make_table(db, "novi_memories", 768)

    store = LanceStore(
        uri=str(tmp_path / "lance"), table_name="novi_memories",
        embed_func=lambda text: [0.1] * 768, embed_dim=768,
        embed_model="nomic-embed-text:v1.5")

    names = set(store._db.table_names())
    backups = [n for n in names if "old_dim" in n]
    assert backups == [], f"unexpected rebuild backups: {backups}"
    assert store._stored_vector_dim() == 768
    assert store.embed_dim == 768


def test_rebuild_still_happens_on_a_real_mismatch(tmp_path):
    """Guard the other direction: a genuine change must still be handled."""
    import lancedb

    from novi.memory.lancedb_store import LanceStore

    db = lancedb.connect(str(tmp_path / "lance"))
    _make_table(db, "novi_memories", 384)

    store = LanceStore(
        uri=str(tmp_path / "lance"), table_name="novi_memories",
        embed_func=lambda text: [0.1] * 768, embed_dim=768,
        embed_model="nomic-embed-text:v1.5")

    backups = [n for n in set(store._db.table_names()) if "old_dim" in n]
    assert backups, "a real 384 -> 768 change must still back up and rebuild"
    assert store._stored_vector_dim() == 768