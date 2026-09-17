from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest

from novi.runtime.run_contracts import ToolCall
from novi.services.permission_service import (
    AuthorizationKind, PermissionDecision, PermissionDecisionKind,
    PermissionErrorCode, PermissionService, ToolAuthorizationPolicy, ToolDescriptor,
)


def service(*, rules=None, mcp=None, mode="manual", ttl=120):
    return PermissionService(
        descriptors={
            "read_file": ToolDescriptor("read_file", effects=("read",), path_arguments=("path",)),
            "write_file": ToolDescriptor("write_file", effects=("write",), path_arguments=("path",)),
            "github_create_issue": ToolDescriptor("github_create_issue", effects=("write",),
                                                   mcp_server="github", operation="write"),
        },
        policy=ToolAuthorizationPolicy(tool_rules=rules or {}, mcp_rules=mcp or {}, mode=mode),
        approval_ttl_seconds=ttl,
    )


def test_registered_and_exposed_validation_precedes_policy():
    svc = service(mode="bypass")
    unknown = svc.authorize("r", ToolCall("c1", "missing", {}), exposed_tools={"missing"})
    hidden = svc.authorize("r", ToolCall("c2", "read_file", {"path": "a"}), exposed_tools=set())
    assert unknown.error_code is PermissionErrorCode.UNREGISTERED_TOOL
    assert hidden.error_code is PermissionErrorCode.NOT_EXPOSED


def test_explicit_deny_wins_over_mode_and_existing_run_grant(tmp_path):
    svc = service(rules={"write_file": "ask"}, mode="bypass")
    call = ToolCall("c1", "write_file", {"path": str(tmp_path / "a"), "content": "x"})
    pending = svc.authorize("r", call, exposed_tools={"write_file"})
    svc.resolve(pending.request.id, PermissionDecision(
        request_id=pending.request.id, kind=PermissionDecisionKind.ALLOW_RUN_SCOPE,
        origin="user", argument_digest=pending.request.argument_digest))
    svc.update_policy(ToolAuthorizationPolicy(tool_rules={"write_file": "deny"}, mode="bypass"))
    denied = svc.authorize("r", ToolCall("c2", "write_file", call.arguments), exposed_tools={"write_file"})
    assert denied.kind is AuthorizationKind.DENIED
    assert denied.error_code is PermissionErrorCode.EXPLICIT_DENY


def test_request_matches_run_call_and_unchanged_digest_exactly(tmp_path):
    svc = service(rules={"write_file": "ask"})
    pending = svc.authorize("r1", ToolCall("c1", "write_file", {"path": str(tmp_path / "a")}),
                            exposed_tools={"write_file"})
    with pytest.raises(ValueError, match="argument digest"):
        svc.resolve(pending.request.id, PermissionDecision(
            request_id=pending.request.id, kind=PermissionDecisionKind.ALLOW_ONCE,
            origin="user", argument_digest="changed"))
    decision = PermissionDecision(request_id=pending.request.id,
        kind=PermissionDecisionKind.ALLOW_ONCE, origin="user",
        argument_digest=pending.request.argument_digest)
    svc.resolve(pending.request.id, decision)
    assert svc.consume("r1", ToolCall("c1", "write_file", {"path": str(tmp_path / "a") })).kind is AuthorizationKind.ALLOWED
    with pytest.raises(ValueError, match="already resolved"):
        svc.resolve(pending.request.id, decision)


def test_run_scope_is_exact_and_does_not_turn_paths_into_patterns(tmp_path):
    svc = service(rules={"read_file": "ask"})
    first = ToolCall("c1", "read_file", {"path": str(tmp_path / "a[1].txt")})
    pending = svc.authorize("r", first, exposed_tools={"read_file"})
    svc.resolve(pending.request.id, PermissionDecision(
        request_id=pending.request.id, kind=PermissionDecisionKind.ALLOW_RUN_SCOPE,
        origin="user", argument_digest=pending.request.argument_digest))
    assert svc.consume("r", first).kind is AuthorizationKind.ALLOWED
    same_scope = svc.authorize("r", ToolCall("c2", "read_file", first.arguments), exposed_tools={"read_file"})
    different = svc.authorize("r", ToolCall("c3", "read_file", {"path": str(tmp_path / "a1.txt")}),
                              exposed_tools={"read_file"})
    assert same_scope.kind is AuthorizationKind.ALLOWED
    assert different.kind is AuthorizationKind.PENDING


def test_expired_cancelled_and_headless_requests_are_honest(tmp_path):
    now = datetime.now(timezone.utc)
    svc = service(rules={"write_file": "ask"}, ttl=1)
    call = ToolCall("c1", "write_file", {"path": str(tmp_path / "a")})
    pending = svc.authorize("r", call, exposed_tools={"write_file"}, now=now)
    expired = svc.expire(now=now + timedelta(seconds=2))
    assert expired == (pending.request.id,)
    assert svc.consume("r", call).kind is AuthorizationKind.DENIED
    headless = svc.authorize("r", ToolCall("c2", "write_file", call.arguments),
                             exposed_tools={"write_file"}, headless=True)
    assert headless.kind is AuthorizationKind.BLOCKED
    assert headless.error_code is PermissionErrorCode.HEADLESS_APPROVAL_REQUIRED


def test_mcp_deny_and_policy_exception_fail_closed():
    denied = service(mcp={"github": {"write": False}}, mode="bypass").authorize(
        "r", ToolCall("c1", "github_create_issue", {}), exposed_tools={"github_create_issue"})
    assert denied.error_code is PermissionErrorCode.EXPLICIT_DENY

    class BrokenPolicy(ToolAuthorizationPolicy):
        def action_for(self, descriptor, canonical_arguments):
            raise RuntimeError("broken")

    svc = service()
    svc.update_policy(BrokenPolicy())
    result = svc.authorize("r", ToolCall("c2", "read_file", {"path": "a"}),
                           exposed_tools={"read_file"})
    assert result.kind is AuthorizationKind.DENIED
    assert result.error_code is PermissionErrorCode.POLICY_ERROR


def test_concurrent_duplicate_answers_resolve_exactly_once():
    svc = service(rules={"write_file": "ask"})
    pending = svc.authorize("r", ToolCall("c", "write_file", {"path": "a"}),
                            exposed_tools={"write_file"})
    decision = PermissionDecision(request_id=pending.request.id,
        kind=PermissionDecisionKind.ALLOW_ONCE, origin="user",
        argument_digest=pending.request.argument_digest)

    def answer():
        try:
            svc.resolve(pending.request.id, decision)
            return "resolved"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda _: answer(), range(2)))
    assert outcomes == ["rejected", "resolved"]


def test_project_or_global_deny_cannot_be_overridden_by_convenience_mode():
    descriptor = {"read_file": ToolDescriptor("read_file", effects=("read",))}
    for policy in (
        ToolAuthorizationPolicy(global_rules={"read_file": "deny"}, mode="bypass"),
        ToolAuthorizationPolicy(project_rules={"read_file": "deny"}, mode="bypass"),
    ):
        result = PermissionService(descriptors=descriptor, policy=policy).authorize(
            "r", ToolCall("c", "read_file", {"path": "a"}), exposed_tools={"read_file"})
        assert result.kind is AuthorizationKind.DENIED
        assert result.error_code is PermissionErrorCode.EXPLICIT_DENY


def test_cancel_is_distinct_from_deny():
    svc = service(rules={"write_file": "ask"})
    call = ToolCall("c", "write_file", {"path": "a"})
    pending = svc.authorize("r", call, exposed_tools={"write_file"})
    svc.resolve(pending.request.id, PermissionDecision(
        request_id=pending.request.id, kind=PermissionDecisionKind.CANCEL,
        origin="user", argument_digest=pending.request.argument_digest))
    result = svc.consume("r", call)
    assert result.kind is AuthorizationKind.DENIED
    assert result.error_code is PermissionErrorCode.CANCELLED
