"""Phase B — ConversationStore tests.

Store is intentionally dumb: append / retrieve / list / close. These tests
pin the contract: raw-turn round-trips, Brain-supplied conversation ids,
append-only semantics, isolation, and newt-order listing.
"""

from datetime import datetime, timezone

import pytest

from novi.brain.storage.conversation_store import ConversationStore
from novi.brain.types import Turn


def make_store(tmp_path):
    return ConversationStore(persist_dir=str(tmp_path))


def test_append_and_get_round_trip(tmp_path):
    store = make_store(tmp_path)
    turn = Turn(user="u1", assistant="a1")
    store.append(turn, "conv-1")
    rec = store.get("conv-1")
    assert rec is not None
    assert rec.id == "conv-1"
    assert rec.turn_count == 1
    assert rec.title == ""
    assert rec.scenario_id is None
    assert rec.project_id is None


def test_turns_round_trip_order_and_fields(tmp_path):
    store = make_store(tmp_path)
    store.append(Turn(user="u1", assistant="a1"), "conv-1")
    store.append(Turn(user="u2", assistant="a2"), "conv-1")
    turns = store.turns("conv-1")
    assert [t.user for t in turns] == ["u1", "u2"]
    assert [t.assistant for t in turns] == ["a1", "a2"]
    assert all(t.conversation_id == "conv-1" for t in turns)


def test_turns_preserves_tool_outputs(tmp_path):
    store = make_store(tmp_path)
    store.append(
        Turn(user="u", assistant="a", tool_outputs=("o1", "o2")), "conv-1"
    )
    (turn,) = store.turns("conv-1")
    assert turn.tool_outputs == ("o1", "o2")


def test_turn_count_increments(tmp_path):
    store = make_store(tmp_path)
    store.append(Turn(user="u1", assistant="a1"), "conv-1")
    store.append(Turn(user="u2", assistant="a2"), "conv-1")
    store.append(Turn(user="u3", assistant="a3"), "conv-1")
    assert store.get("conv-1").turn_count == 3
    assert len(store.turns("conv-1")) == 3


def test_updated_at_moves_on_append(tmp_path):
    store = make_store(tmp_path)
    store.append(Turn(user="u1", assistant="a1"), "conv-1")
    first = store.get("conv-1").updated_at
    store.append(Turn(user="u2", assistant="a2"), "conv-1")
    second = store.get("conv-1").updated_at
    assert second >= first


def test_conversations_are_isolated(tmp_path):
    store = make_store(tmp_path)
    store.append(Turn(user="u1", assistant="a1"), "conv-1")
    store.append(Turn(user="u2", assistant="a2"), "conv-2")
    assert store.turns("conv-1")[0].user == "u1"
    assert store.turns("conv-2")[0].user == "u2"
    assert store.get("conv-1").turn_count == 1
    assert store.get("conv-2").turn_count == 1


def test_unknown_conversation_returns_none(tmp_path):
    store = make_store(tmp_path)
    assert store.get("conv-missing") is None
    assert store.turns("conv-missing") == ()


def test_turns_limit(tmp_path):
    store = make_store(tmp_path)
    for i in range(5):
        store.append(Turn(user=f"u{i}", assistant=f"a{i}"), "conv-1")
    assert len(store.turns("conv-1", limit=3)) == 3


def test_list_conversations_newest_first(tmp_path):
    store = make_store(tmp_path)
    store.append(Turn(user="u1", assistant="a1"), "conv-1")
    store.append(Turn(user="u2", assistant="a2"), "conv-2")
    store.append(Turn(user="u3", assistant="a3"), "conv-3")
    store.append(Turn(user="u4", assistant="a4"), "conv-1")
    listed = [rec.id for rec in store.list_conversations()]
    assert listed == ["conv-1", "conv-3", "conv-2"]


def test_list_conversations_turn_counts(tmp_path):
    store = make_store(tmp_path)
    store.append(Turn(user="u1", assistant="a1"), "conv-1")
    store.append(Turn(user="u2", assistant="a2"), "conv-2")
    store.append(Turn(user="u3", assistant="a3"), "conv-1")
    by_id = {rec.id: rec.turn_count for rec in store.list_conversations()}
    assert by_id == {"conv-1": 2, "conv-2": 1}


def test_threaded_appends_do_not_corrupt(tmp_path):
    import threading

    store = make_store(tmp_path)
    errors = []

    def worker(n):
        try:
            for i in range(20):
                store.append(Turn(user=f"w{n}u{i}", assistant="a"), "conv-1")
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert store.get("conv-1").turn_count == 80
    assert len(store.turns("conv-1")) == 80


def test_reopened_store_retains_data(tmp_path):
    dirpath = str(tmp_path / "convs")
    store = ConversationStore(persist_dir=dirpath)
    store.append(Turn(user="u1", assistant="a1"), "conv-1")
    store.close()
    reopened = ConversationStore(persist_dir=dirpath)
    assert reopened.get("conv-1").turn_count == 1
    assert reopened.turns("conv-1")[0].user == "u1"
    reopened.close()


def test_timestamp_persisted(tmp_path):
    store = make_store(tmp_path)
    ts = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)
    store.append(Turn(user="u", assistant="a", timestamp=ts), "conv-1")
    (turn,) = store.turns("conv-1")
    assert turn.timestamp == ts
