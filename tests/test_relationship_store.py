"""Phase D — RelationshipStore (SQLite edges) tests."""

from novi.brain.storage.relationship_store import RelationshipStore
from novi.brain.types import EdgeKind, Relationship


def rel(source, target, kind=EdgeKind.DERIVED_FROM):
    return Relationship(source_id=source, target_id=target, kind=kind)


def make_store(tmp_path):
    return RelationshipStore(persist_dir=str(tmp_path))


def test_add_and_traverse_outgoing(tmp_path):
    store = make_store(tmp_path)
    store.add_many(
        [
            rel("kn-1", "conv-1"),
            rel("kn-2", "conv-1"),
            rel("kn-2", "scn-1", EdgeKind.OBSERVED_IN),
        ]
    )
    out = store.outgoing("kn-2")
    assert {(r.target_id, r.kind) for r in out} == {
        ("conv-1", EdgeKind.DERIVED_FROM),
        ("scn-1", EdgeKind.OBSERVED_IN),
    }
    assert store.count() == 3


def test_outgoing_kind_filter(tmp_path):
    store = make_store(tmp_path)
    store.add_many(
        [
            rel("kn-1", "conv-1"),
            rel("kn-1", "scn-1", EdgeKind.OBSERVED_IN),
        ]
    )
    out = store.outgoing("kn-1", kind=EdgeKind.OBSERVED_IN)
    assert [(r.target_id, r.kind) for r in out] == [("scn-1", EdgeKind.OBSERVED_IN)]


def test_incoming(tmp_path):
    store = make_store(tmp_path)
    store.add_many(
        [
            rel("kn-1", "conv-1"),
            rel("kn-2", "conv-1"),
            rel("kn-3", "conv-2"),
        ]
    )
    incoming = store.incoming("conv-1")
    assert {r.source_id for r in incoming} == {"kn-1", "kn-2"}
    assert store.incoming("conv-2")[0].source_id == "kn-3"


def test_incoming_kind_filter(tmp_path):
    store = make_store(tmp_path)
    store.add_many(
        [
            rel("kn-1", "scn-1", EdgeKind.OBSERVED_IN),
            rel("kn-2", "scn-1", EdgeKind.DERIVED_FROM),
        ]
    )
    inc = store.incoming("scn-1", kind=EdgeKind.OBSERVED_IN)
    assert [r.source_id for r in inc] == ["kn-1"]


def test_empty_queries(tmp_path):
    store = make_store(tmp_path)
    assert store.outgoing("kn-x") == ()
    assert store.incoming("conv-x") == ()
    assert store.count() == 0


def test_add_many_empty_noop(tmp_path):
    store = make_store(tmp_path)
    store.add_many([])
    assert store.count() == 0


def test_list_orders_by_created_desc(tmp_path):
    store = make_store(tmp_path)
    store.add(rel("kn-1", "conv-1"))
    store.add(rel("kn-2", "conv-1"))
    store.add(rel("kn-3", "conv-1"))
    ids = [r.source_id for r in store.list()]
    assert ids == ["kn-3", "kn-2", "kn-1"]


def test_reopen_retains_edges(tmp_path):
    dirpath = str(tmp_path / "edges")
    store = RelationshipStore(persist_dir=dirpath)
    store.add(rel("kn-1", "conv-1"))
    store.close()
    reopened = RelationshipStore(persist_dir=dirpath)
    assert reopened.outgoing("kn-1")[0].target_id == "conv-1"
    reopened.close()
