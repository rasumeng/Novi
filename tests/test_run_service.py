from pathlib import Path
import threading

from novi.runtime.agent_loop import AgentLoop
from novi.runtime.run_contracts import (
    ModelTurn, RunRequest, RunStatus, ToolCall,
)
from novi.services.permission_service import (
    AuthorizedToolDispatcher, PermissionDecision, PermissionDecisionKind,
    PermissionService, ToolAuthorizationPolicy, ToolDescriptor,
)
from novi.services.run_service import RunBusyError, RunService
from novi.services.run_store import RunStore


class Provider:
    def __init__(self, turns):
        self.turns = list(turns)

    def stream(self, transcript):
        yield self.turns.pop(0)


class LoopFactory:
    def __init__(self, providers, permissions=None, effects=None):
        self.providers = list(providers)
        self.permissions = permissions
        self.effects = effects if effects is not None else []

    def __call__(self, state, cancelled):
        provider = Provider(self.providers.pop(0))
        if self.permissions:
            dispatcher = AuthorizedToolDispatcher(permission_service=self.permissions,
                exposed_tools={"write_file"}, run_id=state.id,
                executors={"write_file": lambda args: self.effects.append(args["content"]) or "written"})
        else:
            class Dispatcher:
                def dispatch(self, call):
                    raise AssertionError(f"unexpected external call: {call.name}")
            dispatcher = Dispatcher()
        return AgentLoop(provider, dispatcher, cancelled=cancelled)


def request(conversation="c"):
    return RunRequest(conversation_id=conversation, user_message_id=f"u-{conversation}", user_text="do it")


def test_start_persists_one_run_and_replayable_terminal(tmp_path):
    store = RunStore(tmp_path)
    factory = LoopFactory([[ModelTurn(calls=(ToolCall("f", "finish_task",
        {"summary": "done", "outcome": "completed"}),))]])
    service = RunService(store, factory, id_factory=lambda: "r1")
    run_id = service.start(request())
    assert run_id == "r1"
    assert service.snapshot(run_id).status is RunStatus.COMPLETED
    events = service.subscribe(run_id, 0)
    assert events[0].type.value == "run.started"
    assert events[-1].type.value == "run.completed"
    assert len([event for event in events if event.type.value.startswith("run.") and
                event.type.value in {"run.completed", "run.failed", "run.cancelled", "run.blocked"}]) == 1
    assert service.subscribe(run_id, events[-2].sequence)[0].sequence == events[-1].sequence
    store.close()


def test_next_run_inherits_canonical_conversation_transcript(tmp_path):
    store = RunStore(tmp_path)
    factory = LoopFactory([
        [ModelTurn(calls=(ToolCall("f1", "finish_task",
            {"summary": "first", "outcome": "completed"}),))],
        [ModelTurn(calls=(ToolCall("f2", "finish_task",
            {"summary": "second", "outcome": "completed"}),))],
    ])
    service = RunService(store, factory, id_factory=iter(["r1", "r2"]).__next__)
    service.start(RunRequest(conversation_id="same", user_message_id="u1", user_text="one"))
    second_id = service.start(RunRequest(
        conversation_id="same", user_message_id="u2", user_text="two"))

    texts = [block.text for message in service.snapshot(second_id).transcript
             for block in message.blocks if block.text]
    assert texts == ["one", "first", "two", "second"]
    store.close()


def test_permission_response_resumes_same_run_without_restarting_goal(tmp_path):
    permissions = PermissionService(
        descriptors={"write_file": ToolDescriptor("write_file", effects=("write",))},
        policy=ToolAuthorizationPolicy(tool_rules={"write_file": "ask"}))
    effects = []
    factory = LoopFactory([
        [ModelTurn(calls=(ToolCall("c1", "write_file", {"path": "a", "content": "x"}),))],
    ], permissions, effects)
    store = RunStore(tmp_path)
    service = RunService(store, factory, permission_service=permissions, id_factory=lambda: "r1")
    service.start(request())
    waiting = service.snapshot("r1")
    assert waiting.status is RunStatus.AWAITING_PERMISSION
    permission = permissions.pending_for_call("r1", "c1")
    decision = PermissionDecision(permission.id, PermissionDecisionKind.ALLOW_ONCE,
                                  "user", permission.argument_digest)
    service.respond_permission(permission.id, decision)
    assert effects == ["x"]
    assert service.snapshot("r1").status is RunStatus.FAILED  # provider had no explicit finish after result
    assert service.snapshot("r1").terminal_reason.startswith("provider_error")
    events = service.subscribe("r1", 0)
    assert len([event for event in events if event.type.value == "run.started"]) == 1
    assert any(event.type.value == "permission.resolved" for event in events)
    store.close()


def test_single_flight_and_cancel_awaiting_permission(tmp_path):
    permissions = PermissionService(
        descriptors={"write_file": ToolDescriptor("write_file", effects=("write",))},
        policy=ToolAuthorizationPolicy(tool_rules={"write_file": "ask"}))
    factory = LoopFactory([[
        ModelTurn(calls=(ToolCall("c1", "write_file", {"path": "a", "content": "x"}),))
    ]], permissions)
    store = RunStore(tmp_path)
    ids = iter(["r1", "r2"])
    service = RunService(store, factory, permission_service=permissions, id_factory=lambda: next(ids))
    service.start(request("a"))
    try:
        service.start(request("b"))
        raise AssertionError("second foreground run should be rejected")
    except RunBusyError:
        pass
    service.cancel("r1")
    assert service.snapshot("r1").status is RunStatus.CANCELLED
    assert service.subscribe("r1", 0)[-1].type.value == "run.cancelled"
    store.close()


def test_restart_marks_active_run_interrupted_without_replaying_effects(tmp_path):
    store = RunStore(tmp_path)
    from novi.runtime.run_contracts import RunState
    store.create(RunState(id="r1", request=request(), status=RunStatus.RUNNING))
    service = RunService(store, LoopFactory([]), recover_on_start=True)
    assert service.snapshot("r1").status is RunStatus.INTERRUPTED
    assert service.subscribe("r1", 0)[-1].type.value == "run.interrupted"
    store.close()


def test_events_are_replayable_while_the_run_is_still_active(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    class BlockingProvider:
        def stream(self, transcript):
            entered.set()
            release.wait(timeout=2)
            yield ModelTurn(calls=(ToolCall("f", "finish_task",
                {"summary": "done", "outcome": "completed"}),))

    class Factory:
        def __call__(self, state, cancelled):
            class Dispatcher:
                def dispatch(self, call):
                    raise AssertionError("unexpected external call")
            return AgentLoop(BlockingProvider(), Dispatcher(), cancelled=cancelled)

    store = RunStore(tmp_path)
    service = RunService(store, Factory(), id_factory=lambda: "live")
    worker = threading.Thread(target=lambda: service.start(request()), daemon=True)
    worker.start()
    assert entered.wait(timeout=1)

    assert [event.type.value for event in service.subscribe("live")] == ["run.started"]

    release.set()
    worker.join(timeout=2)
    assert service.snapshot("live").status is RunStatus.COMPLETED
    service.close()


def test_prepared_run_has_id_before_execution_and_notifies_subscribers(tmp_path):
    store = RunStore(tmp_path)
    factory = LoopFactory([[ModelTurn(calls=(ToolCall("f", "finish_task",
        {"summary": "done", "outcome": "completed"}),))]])
    service = RunService(store, factory, id_factory=lambda: "prepared")
    received = []
    unsubscribe = service.listen(received.append)

    run_id = service.prepare(request())
    assert run_id == "prepared"
    assert service.snapshot(run_id).status is RunStatus.QUEUED
    service.execute(run_id)

    assert received
    assert received[0].run_id == run_id
    unsubscribe()
    service.close()
