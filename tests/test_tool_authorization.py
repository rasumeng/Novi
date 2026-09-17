from novi.runtime.run_contracts import ToolCall, ToolResultStatus
from novi.services.permission_service import (
    AuthorizedToolDispatcher, PermissionDecision, PermissionDecisionKind,
    PermissionService, ToolAuthorizationPolicy, ToolDescriptor,
)


def test_dispatcher_executes_only_after_matching_approval():
    effects = []
    svc = PermissionService(
        descriptors={"write": ToolDescriptor("write", effects=("write",))},
        policy=ToolAuthorizationPolicy(tool_rules={"write": "ask"}))
    dispatcher = AuthorizedToolDispatcher(
        permission_service=svc, exposed_tools={"write"}, run_id="r",
        executors={"write": lambda args: effects.append(args["value"]) or "ok"})
    call = ToolCall("c1", "write", {"value": 1})

    pending = dispatcher.dispatch(call)
    assert pending.status is ToolResultStatus.PENDING_PERMISSION
    assert effects == []
    request = svc.pending_for_call("r", "c1")
    svc.resolve(request.id, PermissionDecision(
        request_id=request.id, kind=PermissionDecisionKind.ALLOW_ONCE,
        origin="user", argument_digest=request.argument_digest))
    result = dispatcher.dispatch(call)
    assert result.status is ToolResultStatus.SUCCEEDED
    assert effects == [1]


def test_denial_and_execution_failure_have_distinct_results():
    denied_service = PermissionService(
        descriptors={"write": ToolDescriptor("write", effects=("write",))},
        policy=ToolAuthorizationPolicy(tool_rules={"write": "deny"}))
    denied_dispatcher = AuthorizedToolDispatcher(
        permission_service=denied_service, exposed_tools={"write"}, run_id="r",
        executors={"write": lambda args: "should not run"})
    assert denied_dispatcher.dispatch(ToolCall("c1", "write", {})).status is ToolResultStatus.DENIED

    allowed_service = PermissionService(
        descriptors={"write": ToolDescriptor("write", effects=("write",))},
        policy=ToolAuthorizationPolicy(tool_rules={"write": "allow"}))
    failed_dispatcher = AuthorizedToolDispatcher(
        permission_service=allowed_service, exposed_tools={"write"}, run_id="r",
        executors={"write": lambda args: (_ for _ in ()).throw(RuntimeError("boom"))})
    failed = failed_dispatcher.dispatch(ToolCall("c2", "write", {}))
    assert failed.status is ToolResultStatus.FAILED
    assert "boom" in (failed.error or "")
