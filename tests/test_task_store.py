"""Milestone 5 Phase 1 — Task ownership layer tests.

TaskStore persists Task state; the Orchestrator creates/loads a Task at the
execution-pipeline boundary and references it from ExecutionPlan.task_id.
"""

import pytest

from cozmo.orchestrator.task_types import TaskStatus
from cozmo.orchestrator.task_store import TaskStore


@pytest.fixture
def store(tmp_path):
    return TaskStore(persist_dir=str(tmp_path / "tasks"))


@pytest.fixture
def orchestrator(tmp_path):
    from cozmo.orchestrator.orchestrator import Orchestrator

    return Orchestrator(task_store=TaskStore(persist_dir=str(tmp_path / "tasks")))


# ── TaskStore persistence ───────────────────────────────────────────────────


def test_save_and_load_roundtrip(store):
    task = store.get_or_create(
        conversation_id="conv-1", goal_text="Refactor auth"
    )
    loaded = store.load(task.id)
    assert loaded is not None
    assert loaded.id == task.id
    assert loaded.conversation_id == "conv-1"
    assert loaded.raw_goal == "Refactor auth"
    assert loaded.status is TaskStatus.NEW
    assert loaded.goal is not None
    assert loaded.goal.text == "Refactor auth"


def test_survives_store_recreation(tmp_path):
    d = str(tmp_path / "tasks")
    store1 = TaskStore(persist_dir=d)
    task = store1.get_or_create(
        conversation_id="conv-1", goal_text="Refactor auth"
    )

    store2 = TaskStore(persist_dir=d)
    loaded = store2.get(task.id)
    assert loaded is not None
    assert loaded.conversation_id == "conv-1"
    assert loaded.raw_goal == "Refactor auth"


def test_load_missing_returns_none(store):
    assert store.load("task-does-not-exist") is None
    assert store.get("task-does-not-exist") is None


def test_update_persists(store):
    task = store.get_or_create(
        conversation_id="conv-1", goal_text="goal a"
    )
    task.status = TaskStatus.EXECUTING
    task.result = "partial"
    assert store.update(task) is True

    loaded = store.get(task.id)
    assert loaded.status is TaskStatus.EXECUTING
    assert loaded.result == "partial"


def test_delete_removes(store):
    task = store.get_or_create(
        conversation_id="conv-1", goal_text="goal a"
    )
    assert store.delete(task.id) is True
    assert store.get(task.id) is None


def test_list_and_list_by_conversation(store):
    t1 = store.get_or_create(
        conversation_id="conv-1", goal_text="goal a"
    )
    t2 = store.get_or_create(
        conversation_id="conv-1", goal_text="goal b"
    )
    t3 = store.get_or_create(
        conversation_id="conv-2", goal_text="goal c"
    )

    assert {t.id for t in store.list()} == {t1.id, t3.id}
    assert {t.id for t in store.list_by_conversation("conv-1")} == {t1.id}


# ── get_or_create semantics ─────────────────────────────────────────────────


def test_get_or_create_creates_fresh_task(store):
    task = store.get_or_create(
        conversation_id="conv-1", goal_text="goal a"
    )
    assert task.id.startswith("task-")
    assert task.status is TaskStatus.NEW
    assert task.created_at
    assert task.updated_at


def test_get_or_create_reuses_active_task_for_conversation(store):
    first = store.get_or_create(
        conversation_id="conv-1", goal_text="goal a"
    )
    second = store.get_or_create(
        conversation_id="conv-1", goal_text="goal b"
    )
    assert second.id == first.id


def test_get_or_create_new_task_after_terminal(store):
    task = store.get_or_create(
        conversation_id="conv-1", goal_text="goal a"
    )
    task.status = TaskStatus.COMPLETED
    store.update(task)

    next_task = store.get_or_create(
        conversation_id="conv-1", goal_text="goal b"
    )
    assert next_task.id != task.id


def test_get_or_create_without_conversation_always_creates(store):
    a = store.get_or_create(
        conversation_id="", goal_text="goal a"
    )
    b = store.get_or_create(
        conversation_id="", goal_text="goal b"
    )
    assert a.id != b.id


# ── Orchestrator boundary ───────────────────────────────────────────────────


def test_plan_creates_task_and_links_conversation(orchestrator):
    plan = orchestrator.plan("Fix the auth bug", conversation_id="conv-7")

    assert plan.task_id
    assert orchestrator.task_store.get(plan.task_id) is not None
    task = orchestrator.task_store.get(plan.task_id)
    assert task.conversation_id == "conv-7"
    assert task.goal is not None
    assert task.goal.text == "Fix the auth bug"
    assert task.created_at


def test_plan_reuses_task_across_same_conversation(orchestrator):
    plan1 = orchestrator.plan("Fix the auth bug", conversation_id="conv-7")
    plan2 = orchestrator.plan("Continue fixing auth", conversation_id="conv-7")

    assert plan1.task_id == plan2.task_id


def test_plan_task_id_matches_store_task(orchestrator):
    plan = orchestrator.plan("Research vector dbs", conversation_id="conv-9")

    stored = orchestrator.task_store.get(plan.task_id)
    assert stored is not None
    assert stored.goal.text == "Research vector dbs"
    assert stored.conversation_id == "conv-9"
    assert plan.task_id == stored.id


def test_plan_without_task_store_leaves_task_id_empty():
    from cozmo.orchestrator.orchestrator import Orchestrator

    bare = Orchestrator()
    plan = bare.plan("hello there", conversation_id="conv-1")
    assert plan.task_id == ""


def test_plan_sets_task_id_in_context(orchestrator):
    plan = orchestrator.plan("Summarize the notes", conversation_id="conv-3")
    assert plan.context.get("task_id") == plan.task_id
