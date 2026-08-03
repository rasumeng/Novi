"""Phase C — ScenarioStore (SQLite) tests.

Rich scenario object round-trips, lifecycle status transitions, isolation,
and reopen persistence.
"""

from datetime import datetime, timezone

from cozmo.brain.storage.scenario_store import ScenarioStore
from cozmo.brain.types import Scenario, ScenarioStatus


def make_store(tmp_path):
    return ScenarioStore(persist_dir=str(tmp_path))


def scenario(**overrides):
    fields = dict(
        id="scn-1",
        name="Fix the build",
        purpose="Recover from a failed build",
        project_id=None,
    )
    fields.update(overrides)
    return Scenario(**fields)


def test_create_and_get_round_trip(tmp_path):
    store = make_store(tmp_path)
    store.create(scenario())
    got = store.get("scn-1")
    assert got is not None
    assert got.id == "scn-1"
    assert got.name == "Fix the build"
    assert got.purpose == "Recover from a failed build"
    assert got.project_id is None
    assert got.status is ScenarioStatus.CREATED


def test_get_unknown_returns_none(tmp_path):
    assert make_store(tmp_path).get("scn-missing") is None


def test_update_persists_fields(tmp_path):
    store = make_store(tmp_path)
    store.create(scenario())
    got = store.get("scn-1")
    got.summary = "Build recovered after pinning the dependency."
    got.status = ScenarioStatus.ACTIVE
    store.update(got)
    again = store.get("scn-1")
    assert again.summary == "Build recovered after pinning the dependency."
    assert again.status is ScenarioStatus.ACTIVE


def test_set_status(tmp_path):
    store = make_store(tmp_path)
    store.create(scenario())
    store.set_status("scn-1", ScenarioStatus.COMPLETED)
    assert store.get("scn-1").status is ScenarioStatus.COMPLETED


def test_rich_fields_round_trip(tmp_path):
    store = make_store(tmp_path)
    started = datetime(2026, 8, 3, 10, 0, 0, tzinfo=timezone.utc)
    store.create(
        scenario(
            goal="Pin the broken dependency",
            summary="Build recovered",
            participants=("alice", "bob"),
            started_at=started,
            project_id="proj-9",
        )
    )
    got = store.get("scn-1")
    assert got.goal == "Pin the broken dependency"
    assert got.summary == "Build recovered"
    assert got.participants == ("alice", "bob")
    assert got.project_id == "proj-9"
    assert got.started_at == started


def test_list_orders_by_updated_desc(tmp_path):
    store = make_store(tmp_path)
    store.create(scenario(id="scn-1"))
    store.create(scenario(id="scn-2"))
    store.create(scenario(id="scn-3"))
    store.set_status("scn-1", ScenarioStatus.ACTIVE)
    ids = [s.id for s in store.list()]
    assert set(ids) == {"scn-1", "scn-2", "scn-3"}
    assert ids[0] == "scn-1"


def test_count(tmp_path):
    store = make_store(tmp_path)
    assert store.count() == 0
    store.create(scenario())
    store.create(scenario(id="scn-2"))
    assert store.count() == 2


def test_isolation_between_instances(tmp_path):
    store_a = make_store(tmp_path)
    store_b = make_store(tmp_path)
    store_a.create(scenario())
    assert store_b.get("scn-1") is not None


def test_reopen_retains_data(tmp_path):
    dirpath = str(tmp_path / "scenarios")
    store = ScenarioStore(persist_dir=dirpath)
    store.create(scenario(summary="first"))
    store.close()
    reopened = ScenarioStore(persist_dir=dirpath)
    assert reopened.get("scn-1").summary == "first"
    reopened.close()


def test_completed_at_round_trip(tmp_path):
    store = make_store(tmp_path)
    completed = datetime(2026, 8, 3, 15, 0, 0, tzinfo=timezone.utc)
    store.create(scenario(completed_at=completed))
    assert store.get("scn-1").completed_at == completed
