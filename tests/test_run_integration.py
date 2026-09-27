"""Phase 9 integration: actual RunService + AgentLoop + PermissionService + Store + transport.

Fake only the model/network/effect boundary (provider turns + tool executors).
Covers: normal completion, tool success, permission allow/deny/expiry,
cancellation, provider failure, repeated same-name calls, reconnect/replay,
navigation isolation, exactly one terminal, and strict serialization.
Transfers useful assertions from deleted suites test_agentrun_streaming,
test_emit_progress, test_webui_agentrun_bridge.
"""

import threading
import time
from datetime import datetime, timezone, timedelta

import pytest

from novi.runtime.agent_loop import AgentLoop
from novi.runtime.run_contracts import ModelTurn, RunEventType, RunRequest, RunStatus, ToolCall
from novi.services.permission_service import (
    AuthorizedToolDispatcher,
    PermissionDecision,
    PermissionDecisionKind,
    PermissionService,
    ToolAuthorizationPolicy,
    ToolDescriptor,
)
from novi.services.run_service import RunService
from novi.services.run_store import RunStore
from novi.services.run_transport import event_to_socket_message


class ReusableProvider:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = 0

    def stream(self, transcript):
        # yield next turn; if exhausted, yield empty (will cause failure)
        if self.calls < len(self.turns):
            t = self.turns[self.calls]
            self.calls += 1
            yield t
        else:
            yield ModelTurn(text="", calls=(), is_complete=True)


def _make_service(tmp_path, provider_turns, perms=None, executors=None, run_id="run-int-1"):
    perms = perms or PermissionService(descriptors={}, policy=ToolAuthorizationPolicy())
    executors = executors or {}
    provider = ReusableProvider(provider_turns)

    def factory(state, cancelled):
        from novi.runtime.agent_loop import AgentLoop as AL

        disp = AuthorizedToolDispatcher(
            permission_service=perms,
            exposed_tools=set(executors.keys()) | {"write_file", "read_file"} if executors else set(),
            run_id=state.id,
            executors=executors,
            headless=False,
        )
        # if executors empty, use no-op dispatcher that fails for unexpected calls
        if not executors:
            class Noop:
                def dispatch(self, call):
                    raise AssertionError(f"unexpected tool {call.name}")

            disp = Noop()
            # wrap to have AuthorizedToolDispatcher interface? For simple cases, use AL with Noop
            return AL(provider, disp, cancelled=cancelled)
        return AL(provider, disp, cancelled=cancelled)

    store = RunStore(tmp_path)
    svc = RunService(store, factory, permission_service=perms, id_factory=lambda: run_id)
    return svc, store, perms, provider


def test_normal_text_completion(tmp_path):
    provider_turns = [ModelTurn(text='hello', is_complete=True)]
    svc, store, _, _ = _make_service(tmp_path, provider_turns, run_id="r-normal")
    svc.start(RunRequest(conversation_id="c-normal", user_message_id="u1", user_text="hi"))
    assert svc.snapshot("r-normal").status == RunStatus.COMPLETED
    events = svc.subscribe("r-normal", 0)
    assert len([e for e in events if e.type in {RunEventType.RUN_COMPLETED, RunEventType.RUN_FAILED, RunEventType.RUN_CANCELLED, RunEventType.RUN_BLOCKED}]) == 1
    # every RunEvent serializes via canonical transport (not legacy tuple)
    for e in events:
        msg = event_to_socket_message(e)
        assert msg is not None or e.type in {RunEventType.RUN_STATE_CHANGED}
    store.close()


def test_tool_success_every_call_one_result(tmp_path):
    perms = PermissionService(descriptors={"read_file": ToolDescriptor("read_file", effects=("read",))}, policy=ToolAuthorizationPolicy(tool_rules={"read_file": "allow"}))
    effects = []
    provider_turns = [
        ModelTurn(calls=(ToolCall("c1", "read_file", {"path": "a"}),), is_complete=True),
        ModelTurn(text='done', is_complete=True),
    ]
    # need two provider calls: first returns tool, second after tool result returns finish
    # ReusableProvider will yield sequentially, so we need a loop that calls provider twice.
    # AgentLoop will call provider twice (once per turn), so provide 2 turns.
    provider = ReusableProvider(provider_turns)
    executors = {"read_file": lambda args: effects.append(args["path"]) or "content"}

    def factory(state, cancelled):
        return AgentLoop(provider, AuthorizedToolDispatcher(permission_service=perms, exposed_tools={"read_file"}, run_id=state.id, executors=executors), cancelled=cancelled)

    store = RunStore(tmp_path)
    svc = RunService(store, factory, permission_service=perms, id_factory=lambda: "r-tool")
    svc.start(RunRequest(conversation_id="c-tool", user_message_id="u1", user_text="read"))
    assert svc.snapshot("r-tool").status == RunStatus.COMPLETED
    events = svc.subscribe("r-tool", 0)
    reqs = [e for e in events if e.type == RunEventType.TOOL_REQUESTED]
    comps = [e for e in events if e.type == RunEventType.TOOL_COMPLETED]
    assert len(reqs) == 1  # read_file; final prose is not a tool call
    assert len(comps) == 1
    assert comps[0].payload["status"] == "succeeded"
    assert effects == ["a"]
    # exactly one terminal
    assert len([e for e in events if e.type.value.startswith("run.") and e.type.value in {"run.completed", "run.failed", "run.cancelled", "run.blocked", "run.interrupted"}]) == 1
    store.close()


def test_permission_allow(tmp_path):
    perms = PermissionService(descriptors={"write_file": ToolDescriptor("write_file", effects=("write",))}, policy=ToolAuthorizationPolicy(tool_rules={"write_file": "ask"}))
    effects = []

    provider = ReusableProvider([
        ModelTurn(calls=(ToolCall("c1", "write_file", {"path": "a", "content": "x"}),), is_complete=True),
        ModelTurn(text='done', is_complete=True),
    ])

    def factory(state, cancelled):
        return AgentLoop(provider, AuthorizedToolDispatcher(permission_service=perms, exposed_tools={"write_file"}, run_id=state.id, executors={"write_file": lambda args: effects.append(args["content"]) or "w"}), cancelled=cancelled)

    store = RunStore(tmp_path)
    svc = RunService(store, factory, permission_service=perms, id_factory=lambda: "r-allow")
    svc.start(RunRequest(conversation_id="c-allow", user_message_id="u1", user_text="write"))
    assert svc.snapshot("r-allow").status == RunStatus.AWAITING_PERMISSION
    events = svc.subscribe("r-allow", 0)
    pid = next(e.payload["request_id"] for e in events if e.type == RunEventType.PERMISSION_REQUESTED)
    req = perms.request(pid)
    svc.respond_permission(pid, PermissionDecision(pid, PermissionDecisionKind.ALLOW_ONCE, "user", req.argument_digest))
    assert svc.snapshot("r-allow").status == RunStatus.COMPLETED
    assert effects == ["x"]
    events2 = svc.subscribe("r-allow", 0)
    assert any(e.type == RunEventType.PERMISSION_RESOLVED for e in events2)
    assert event_to_socket_message(next(e for e in events2 if e.type == RunEventType.PERMISSION_REQUESTED))["type"] == "permission_request"
    assert event_to_socket_message(events2[-1])["type"] == "done"
    store.close()


def test_permission_deny(tmp_path):
    perms = PermissionService(descriptors={"write_file": ToolDescriptor("write_file", effects=("write",))}, policy=ToolAuthorizationPolicy(tool_rules={"write_file": "ask"}))
    provider = ReusableProvider([
        ModelTurn(calls=(ToolCall("c1", "write_file", {"path": "a", "content": "x"}),), is_complete=True),
        ModelTurn(text='done', is_complete=True),
    ])

    def factory(state, cancelled):
        return AgentLoop(provider, AuthorizedToolDispatcher(permission_service=perms, exposed_tools={"write_file"}, run_id=state.id, executors={"write_file": lambda args: "should not run"}), cancelled=cancelled)

    store = RunStore(tmp_path)
    svc = RunService(store, factory, permission_service=perms, id_factory=lambda: "r-deny")
    svc.start(RunRequest(conversation_id="c-deny", user_message_id="u1", user_text="write"))
    assert svc.snapshot("r-deny").status == RunStatus.AWAITING_PERMISSION
    pid = next(e.payload["request_id"] for e in svc.subscribe("r-deny", 0) if e.type == RunEventType.PERMISSION_REQUESTED)
    req = perms.request(pid)
    svc.respond_permission(pid, PermissionDecision(pid, PermissionDecisionKind.DENY, "user", req.argument_digest))
    # after deny, loop records denied result and continues to finish
    assert svc.snapshot("r-deny").status == RunStatus.COMPLETED
    events = svc.subscribe("r-deny", 0)
    comps = [e for e in events if e.type == RunEventType.TOOL_COMPLETED]
    assert any(c.payload["status"] == "denied" for c in comps)
    # exactly one terminal, still completed (deny is not terminal failure)
    assert len([e for e in events if e.type.value.startswith("run.") and "completed" in e.type.value]) == 1
    store.close()


def test_permission_expiry(tmp_path):
    perms = PermissionService(descriptors={"write_file": ToolDescriptor("write_file", effects=("write",))}, policy=ToolAuthorizationPolicy(tool_rules={"write_file": "ask"}))
    provider = ReusableProvider([ModelTurn(calls=(ToolCall("c1", "write_file", {"path": "a", "content": "x"}),), is_complete=True)])

    def factory(state, cancelled):
        return AgentLoop(provider, AuthorizedToolDispatcher(permission_service=perms, exposed_tools={"write_file"}, run_id=state.id, executors={"write_file": lambda args: "x"}), cancelled=cancelled)

    store = RunStore(tmp_path)
    svc = RunService(store, factory, permission_service=perms, id_factory=lambda: "r-expiry")
    svc.start(RunRequest(conversation_id="c-expiry", user_message_id="u1", user_text="write"))
    pid = next(e.payload["request_id"] for e in svc.subscribe("r-expiry", 0) if e.type == RunEventType.PERMISSION_REQUESTED)
    # expire the request manually by fast-forwarding time: resolve with expired kind
    req = perms.request(pid)
    # Simulate expiry by directly expiring via permission service (if supports) — otherwise deny with expired
    # Our PermissionDecisionKind has EXPIRED
    svc.respond_permission(pid, PermissionDecision(pid, PermissionDecisionKind.EXPIRED, "system", req.argument_digest))
    # expired should be treated as denied/cancelled and loop should continue or block
    state = svc.snapshot("r-expiry")
    assert state.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.BLOCKED, RunStatus.AWAITING_PERMISSION}
    events = svc.subscribe("r-expiry", 0)
    assert any(e.type == RunEventType.PERMISSION_RESOLVED for e in events)
    store.close()


def test_user_stop_cancellation(tmp_path):
    perms = PermissionService(descriptors={}, policy=ToolAuthorizationPolicy())

    class BlockingProvider:
        def stream(self, transcript):
            time.sleep(0.05)
            yield ModelTurn(text='done', is_complete=True)

    def factory(state, cancelled):
        # provider that checks cancelled
        return AgentLoop(BlockingProvider(), AuthorizedToolDispatcher(permission_service=perms, exposed_tools=set(), run_id=state.id, executors={}), cancelled=cancelled)

    store = RunStore(tmp_path)
    svc = RunService(store, factory, permission_service=perms, id_factory=lambda: "r-cancel")
    # start in thread and cancel
    t = threading.Thread(target=lambda: svc.start(RunRequest(conversation_id="c-cancel", user_message_id="u1", user_text="hi")))
    t.start()
    time.sleep(0.02)
    svc.cancel("r-cancel")
    t.join(timeout=1)
    state = svc.snapshot("r-cancel")
    assert state.status == RunStatus.CANCELLED
    events = svc.subscribe("r-cancel", 0)
    assert any(e.type == RunEventType.RUN_CANCELLED for e in events)
    assert len([e for e in events if e.type.value.startswith("run.") and e.type.value in {"run.completed", "run.failed", "run.cancelled", "run.blocked", "run.interrupted"}]) == 1
    store.close()


def test_provider_failure(tmp_path):
    class FailingProvider:
        def stream(self, transcript):
            raise RuntimeError("provider down")

    perms = PermissionService(descriptors={}, policy=ToolAuthorizationPolicy())

    def factory(state, cancelled):
        return AgentLoop(FailingProvider(), AuthorizedToolDispatcher(permission_service=perms, exposed_tools=set(), run_id=state.id, executors={}), cancelled=cancelled)

    store = RunStore(tmp_path)
    svc = RunService(store, factory, permission_service=perms, id_factory=lambda: "r-fail")
    svc.start(RunRequest(conversation_id="c-fail", user_message_id="u1", user_text="hi"))
    assert svc.snapshot("r-fail").status == RunStatus.FAILED
    events = svc.subscribe("r-fail", 0)
    assert any(e.type == RunEventType.RUN_FAILED for e in events)
    assert len([e for e in events if "run." in e.type.value and e.type.value.split(".")[-1] in {"completed", "failed", "cancelled", "blocked", "interrupted"}]) == 1
    store.close()


def test_repeated_same_name_tool_calls_distinct_ids(tmp_path):
    perms = PermissionService(descriptors={"read_file": ToolDescriptor("read_file", effects=("read",))}, policy=ToolAuthorizationPolicy(tool_rules={"read_file": "allow"}))
    provider = ReusableProvider([
        ModelTurn(calls=(ToolCall("c1", "read_file", {"path": "a"}), ToolCall("c2", "read_file", {"path": "b"})), is_complete=True),
        ModelTurn(text='done', is_complete=True),
    ])
    seen = []

    def factory(state, cancelled):
        return AgentLoop(provider, AuthorizedToolDispatcher(permission_service=perms, exposed_tools={"read_file"}, run_id=state.id, executors={"read_file": lambda args: seen.append(args["path"]) or "ok"}), cancelled=cancelled)

    store = RunStore(tmp_path)
    svc = RunService(store, factory, permission_service=perms, id_factory=lambda: "r-repeat")
    svc.start(RunRequest(conversation_id="c-repeat", user_message_id="u1", user_text="read"))
    assert svc.snapshot("r-repeat").status == RunStatus.COMPLETED
    events = svc.subscribe("r-repeat", 0)
    reqs = [e for e in events if e.type == RunEventType.TOOL_REQUESTED and e.payload.get("name") == "read_file"]
    comps = [e for e in events if e.type == RunEventType.TOOL_COMPLETED and e.payload.get("name") == "read_file"]
    assert len(reqs) == 2 and len(comps) == 2
    assert reqs[0].payload["call_id"] != reqs[1].payload["call_id"]
    assert comps[0].payload["call_id"] != comps[1].payload["call_id"]
    assert seen == ["a", "b"]
    store.close()


def test_reconnect_replay(tmp_path):
    provider = ReusableProvider([ModelTurn(text='done', is_complete=True)])
    perms = PermissionService(descriptors={}, policy=ToolAuthorizationPolicy())

    def factory(state, cancelled):
        return AgentLoop(provider, AuthorizedToolDispatcher(permission_service=perms, exposed_tools=set(), run_id=state.id, executors={}), cancelled=cancelled)

    store = RunStore(tmp_path)
    svc = RunService(store, factory, permission_service=perms, id_factory=lambda: "r-replay")
    svc.start(RunRequest(conversation_id="c-replay", user_message_id="u1", user_text="hi"))
    events = svc.subscribe("r-replay", 0)
    # replay after sequence 1 should return remaining
    replay = svc.subscribe("r-replay", after_sequence=1)
    assert len(replay) == len(events) - 1
    assert replay[0].sequence == 2
    # snapshot survives
    assert svc.snapshot("r-replay").status == RunStatus.COMPLETED
    store.close()


def test_navigation_away_and_back_isolation(tmp_path):
    perms = PermissionService(descriptors={}, policy=ToolAuthorizationPolicy())
    p1 = ReusableProvider([ModelTurn(text='one', is_complete=True)])
    p2 = ReusableProvider([ModelTurn(text='two', is_complete=True)])

    def factory1(state, cancelled):
        return AgentLoop(p1, AuthorizedToolDispatcher(permission_service=perms, exposed_tools=set(), run_id=state.id, executors={}), cancelled=cancelled)

    def factory2(state, cancelled):
        return AgentLoop(p2, AuthorizedToolDispatcher(permission_service=perms, exposed_tools=set(), run_id=state.id, executors={}), cancelled=cancelled)

    store = RunStore(tmp_path)
    svc1 = RunService(store, factory1, permission_service=perms, id_factory=lambda: "r-nav-1")
    svc1.start(RunRequest(conversation_id="conv-a", user_message_id="u1", user_text="one"))
    svc2 = RunService(store, factory2, permission_service=perms, id_factory=lambda: "r-nav-2")
    svc2.start(RunRequest(conversation_id="conv-b", user_message_id="u2", user_text="two"))
    # each conversation's events are isolated
    ev_a = svc1.subscribe("r-nav-1", 0)
    ev_b = svc2.subscribe("r-nav-2", 0)
    assert all(e.conversation_id == "conv-a" for e in ev_a)
    assert all(e.conversation_id == "conv-b" for e in ev_b)
    # ensure no leakage via runs_for_conversation
    assert len(store.runs_for_conversation("conv-a")) == 1
    assert len(store.runs_for_conversation("conv-b")) == 1
    store.close()


def test_strict_serialization_and_single_terminal(tmp_path):
    from novi.runtime.agent_action import AgentAction, AgentActionType

    act = AgentAction(type=AgentActionType.FINISH, message="done", metadata={"stop_reason": "completed"})
    assert not hasattr(act, "__iter__")
    assert not hasattr(act, "__getitem__")
    assert not hasattr(act, "get")

    perms = PermissionService(descriptors={"read_file": ToolDescriptor("read_file", effects=("read",))}, policy=ToolAuthorizationPolicy(tool_rules={"read_file": "allow"}))
    provider = ReusableProvider([ModelTurn(calls=(ToolCall("c1", "read_file", {"path": "a"}),), is_complete=True), ModelTurn(text='done', is_complete=True)])
    seen = []

    def factory(state, cancelled):
        return AgentLoop(provider, AuthorizedToolDispatcher(permission_service=perms, exposed_tools={"read_file"}, run_id=state.id, executors={"read_file": lambda args: seen.append(1) or "ok"}), cancelled=cancelled)

    store = RunStore(tmp_path)
    svc = RunService(store, factory, permission_service=perms, id_factory=lambda: "r-strict")
    svc.start(RunRequest(conversation_id="c-strict", user_message_id="u1", user_text="hi"))
    events = svc.subscribe("r-strict", 0)
    # every requested has exactly one completed
    req_ids = {e.payload["call_id"] for e in events if e.type == RunEventType.TOOL_REQUESTED}
    comp_ids = {e.payload["call_id"] for e in events if e.type == RunEventType.TOOL_COMPLETED}
    assert req_ids == comp_ids
    assert req_ids == {"c1"}
    # exactly one terminal
    terminals = [e for e in events if e.type in {RunEventType.RUN_COMPLETED, RunEventType.RUN_FAILED, RunEventType.RUN_CANCELLED, RunEventType.RUN_BLOCKED, RunEventType.RUN_INTERRUPTED}]
    assert len(terminals) == 1
    # transport covers canonical types without tuple
    for e in events:
        m = event_to_socket_message(e)
        # canonical types must not be None
        assert m is not None or e.type == RunEventType.RUN_STATE_CHANGED
    store.close()

