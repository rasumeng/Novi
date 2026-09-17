from dataclasses import replace

import pytest

from novi.runtime.run_contracts import RunEvent, RunEventType, RunRequest, RunState, RunStatus
from novi.services.run_store import InvalidRunTransitionError, RunStore, RunStoreError


def make_run(run_id: str = "r1", conversation_id: str = "c1") -> RunState:
    return RunState(
        id=run_id,
        request=RunRequest(conversation_id=conversation_id, user_message_id=f"{run_id}-user", user_text="hello"),
    )


def event(run: RunState, sequence: int, kind: RunEventType) -> RunEvent:
    return RunEvent(id=f"{run.id}-e{sequence}", sequence=sequence, run_id=run.id,
                    conversation_id=run.conversation_id, type=kind)


def test_atomic_transition_and_event_replay(tmp_path):
    store = RunStore(tmp_path)
    run = make_run()
    store.create(run)
    running = store.transition(run.id, RunStatus.RUNNING,
                               event(run, 1, RunEventType.RUN_STARTED))
    completed = store.transition(run.id, RunStatus.COMPLETED,
                                 event(run, 2, RunEventType.RUN_COMPLETED), reason="finished")
    assert completed.finished
    assert store.snapshot(run.id).terminal_reason == "finished"
    assert [item.sequence for item in store.events(run.id)] == [1, 2]
    assert [item.sequence for item in store.events(run.id, after_sequence=1)] == [2]
    with pytest.raises(InvalidRunTransitionError):
        store.transition(run.id, RunStatus.FAILED,
                         event(run, 3, RunEventType.RUN_FAILED), reason="late")
    assert running.status is RunStatus.RUNNING
    store.close()


def test_rejects_duplicate_or_gapped_sequence(tmp_path):
    store = RunStore(tmp_path)
    run = make_run()
    store.create(run)
    with pytest.raises(RunStoreError, match="expected event sequence 1"):
        store.append(event(run, 2, RunEventType.RUN_STARTED),
                     replace(run, status=RunStatus.RUNNING))
    store.close()


def test_conversation_isolation_a_b_a(tmp_path):
    store = RunStore(tmp_path)
    a1, b, a2 = make_run("a1", "A"), make_run("b1", "B"), make_run("a2", "A")
    for run in (a1, b, a2):
        store.create(run)
        store.transition(run.id, RunStatus.RUNNING,
                         event(run, 1, RunEventType.RUN_STARTED))
    assert store.snapshot(a1.id).conversation_id == "A"
    assert store.snapshot(b.id).conversation_id == "B"
    assert store.snapshot(a2.id).conversation_id == "A"
    assert [run.id for run in store.runs_for_conversation("A")] == ["a1", "a2"]
    store.close()


def test_restart_marks_unknown_active_effect_state_interrupted(tmp_path):
    store = RunStore(tmp_path)
    run = make_run()
    store.create(run)
    store.transition(run.id, RunStatus.RUNNING,
                     event(run, 1, RunEventType.RUN_STARTED))
    assert store.interrupt_active_runs() == 1
    snapshot = store.snapshot(run.id)
    assert snapshot.status is RunStatus.INTERRUPTED
    assert snapshot.terminal_reason == "process_restart_unknown_effect_state"
    assert store.events(run.id)[-1].type is RunEventType.RUN_INTERRUPTED
    store.close()


def test_large_tool_output_is_stored_as_bounded_artifact(tmp_path):
    store = RunStore(tmp_path)
    run = make_run()
    store.create(run)
    store.put_artifact("artifact-1", run.id, b"full tool output", "text/plain")
    assert store.artifact("artifact-1") == ("text/plain", b"full tool output")
    with pytest.raises(RunStoreError, match="byte limit"):
        store.put_artifact("too-large", run.id, b"x" * (store.MAX_ARTIFACT_BYTES + 1))
    store.close()
