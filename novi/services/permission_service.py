"""Typed, fail-closed authorization and permission-request lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from novi.runtime.run_contracts import ToolCall, ToolResult, ToolResultStatus


class AuthorizationKind(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    PENDING = "pending"
    BLOCKED = "blocked"


class PermissionDecisionKind(str, Enum):
    ALLOW_ONCE = "allow_once"
    ALLOW_RUN_SCOPE = "allow_run_scope"
    DENY = "deny"
    CANCEL = "cancel"
    EXPIRED = "expired"


class PermissionErrorCode(str, Enum):
    UNREGISTERED_TOOL = "unregistered_tool"
    NOT_EXPOSED = "not_exposed"
    INVALID_ARGUMENTS = "invalid_arguments"
    EXPLICIT_DENY = "explicit_deny"
    POLICY_ERROR = "policy_error"
    USER_DENIED = "user_denied"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    HEADLESS_APPROVAL_REQUIRED = "headless_approval_required"


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    effects: tuple[str, ...] = ()
    path_arguments: tuple[str, ...] = ()
    command_arguments: tuple[str, ...] = ()
    mcp_server: str | None = None
    operation: str = "execute"


@dataclass(frozen=True)
class PermissionRequest:
    id: str
    run_id: str
    call_id: str
    tool_name: str
    canonical_arguments: dict[str, Any]
    argument_digest: str
    scope_key: str
    effects: tuple[str, ...]
    policy_version: int
    created_at: datetime
    expires_at: datetime
    proposed_diff: dict[str, Any] | None = None
    decision: "PermissionDecision | None" = None
    consumed: bool = False


@dataclass(frozen=True)
class PermissionDecision:
    request_id: str
    kind: PermissionDecisionKind
    origin: str
    argument_digest: str


@dataclass(frozen=True)
class AuthorizationResult:
    kind: AuthorizationKind
    request: PermissionRequest | None = None
    error_code: PermissionErrorCode | None = None
    reason: str | None = None


@dataclass
class ToolAuthorizationPolicy:
    tool_rules: dict[str, str] = field(default_factory=dict)
    global_rules: dict[str, str] = field(default_factory=dict)
    project_rules: dict[str, str] = field(default_factory=dict)
    mcp_rules: dict[str, dict[str, bool]] = field(default_factory=dict)
    mode: str = "manual"
    version: int = 1

    def explicit_deny(self, descriptor: ToolDescriptor) -> bool:
        if any(rules.get(descriptor.name) == "deny" for rules in
               (self.tool_rules, self.global_rules, self.project_rules)):
            return True
        if descriptor.mcp_server:
            server = self.mcp_rules.get(descriptor.mcp_server, {})
            if server.get(descriptor.name) is False or server.get(descriptor.operation) is False:
                return True
        return False

    def action_for(self, descriptor: ToolDescriptor, canonical_arguments: dict[str, Any]) -> str:
        for rules in (self.project_rules, self.tool_rules, self.global_rules):
            rule = rules.get(descriptor.name)
            if rule in {"allow", "ask", "deny"}:
                return rule
        return "allow" if self.mode == "bypass" else "ask"


class PermissionService:
    def __init__(self, *, descriptors: dict[str, ToolDescriptor],
                 policy: ToolAuthorizationPolicy,
                 approval_ttl_seconds: int = 120) -> None:
        if approval_ttl_seconds < 1:
            raise ValueError("approval_ttl_seconds must be positive")
        self._descriptors = dict(descriptors)
        self._policy = policy
        self._ttl = approval_ttl_seconds
        self._requests: dict[str, PermissionRequest] = {}
        self._request_by_call: dict[tuple[str, str], str] = {}
        self._run_grants: set[tuple[str, str]] = set()
        self._lock = threading.RLock()

    def update_policy(self, policy: ToolAuthorizationPolicy) -> None:
        with self._lock:
            self._policy = policy

    def authorize(self, run_id: str, call: ToolCall, *, exposed_tools: set[str],
                  headless: bool = False, now: datetime | None = None,
                  proposed_diff: dict[str, Any] | None = None) -> AuthorizationResult:
        with self._lock:
            return self._authorize(run_id, call, exposed_tools=exposed_tools,
                headless=headless, now=now, proposed_diff=proposed_diff)

    def _authorize(self, run_id: str, call: ToolCall, *, exposed_tools: set[str],
                   headless: bool, now: datetime | None,
                   proposed_diff: dict[str, Any] | None) -> AuthorizationResult:
        descriptor = self._descriptors.get(call.name)
        if descriptor is None:
            return AuthorizationResult(AuthorizationKind.DENIED,
                error_code=PermissionErrorCode.UNREGISTERED_TOOL, reason="tool is not registered")
        if call.name not in exposed_tools:
            return AuthorizationResult(AuthorizationKind.DENIED,
                error_code=PermissionErrorCode.NOT_EXPOSED, reason="tool was not exposed to this run")
        try:
            canonical = _canonicalize(call.arguments, descriptor)
            digest = _digest(canonical)
            scope_key = _scope_key(descriptor, canonical)
        except Exception as exc:
            return AuthorizationResult(AuthorizationKind.DENIED,
                error_code=PermissionErrorCode.INVALID_ARGUMENTS, reason=str(exc))
        try:
            if self._policy.explicit_deny(descriptor):
                return AuthorizationResult(AuthorizationKind.DENIED,
                    error_code=PermissionErrorCode.EXPLICIT_DENY, reason="explicit policy denial")
            if (run_id, scope_key) in self._run_grants:
                return AuthorizationResult(AuthorizationKind.ALLOWED)
            action = self._policy.action_for(descriptor, canonical)
        except Exception as exc:
            return AuthorizationResult(AuthorizationKind.DENIED,
                error_code=PermissionErrorCode.POLICY_ERROR, reason=f"permission evaluation failed: {exc}")
        if action == "deny":
            return AuthorizationResult(AuthorizationKind.DENIED,
                error_code=PermissionErrorCode.EXPLICIT_DENY, reason="explicit policy denial")
        if action == "allow":
            return AuthorizationResult(AuthorizationKind.ALLOWED)
        existing_id = self._request_by_call.get((run_id, call.id))
        if existing_id:
            request = self._requests[existing_id]
            if request.argument_digest != digest:
                return AuthorizationResult(AuthorizationKind.DENIED,
                    error_code=PermissionErrorCode.INVALID_ARGUMENTS,
                    reason="tool arguments changed after approval was requested")
            if request.decision is not None:
                return self.consume(run_id, call)
            return AuthorizationResult(AuthorizationKind.PENDING, request=request)
        if headless:
            return AuthorizationResult(AuthorizationKind.BLOCKED,
                error_code=PermissionErrorCode.HEADLESS_APPROVAL_REQUIRED,
                reason="interactive approval is required")
        created = now or datetime.now(timezone.utc)
        request = PermissionRequest(id=f"perm-{uuid4().hex}", run_id=run_id,
            call_id=call.id, tool_name=call.name, canonical_arguments=canonical,
            argument_digest=digest, scope_key=scope_key, effects=descriptor.effects,
            policy_version=self._policy.version, created_at=created,
            expires_at=created + timedelta(seconds=self._ttl), proposed_diff=proposed_diff)
        self._requests[request.id] = request
        self._request_by_call[(run_id, call.id)] = request.id
        return AuthorizationResult(AuthorizationKind.PENDING, request=request)

    def resolve(self, request_id: str, decision: PermissionDecision,
                *, now: datetime | None = None) -> PermissionRequest:
        with self._lock:
            return self._resolve(request_id, decision, now=now)

    def _resolve(self, request_id: str, decision: PermissionDecision,
                 *, now: datetime | None) -> PermissionRequest:
        request = self._requests.get(request_id)
        if request is None:
            raise ValueError("permission request not found")
        if request.decision is not None:
            raise ValueError("permission request already resolved")
        if decision.request_id != request.id:
            raise ValueError("decision request id does not match")
        if decision.argument_digest != request.argument_digest:
            raise ValueError("decision argument digest does not match")
        current = now or datetime.now(timezone.utc)
        if current >= request.expires_at:
            decision = replace(decision, kind=PermissionDecisionKind.EXPIRED)
        resolved = replace(request, decision=decision)
        self._requests[request_id] = resolved
        if decision.kind is PermissionDecisionKind.ALLOW_RUN_SCOPE:
            self._run_grants.add((request.run_id, request.scope_key))
        return resolved

    def consume(self, run_id: str, call: ToolCall) -> AuthorizationResult:
        with self._lock:
            return self._consume(run_id, call)

    def _consume(self, run_id: str, call: ToolCall) -> AuthorizationResult:
        request = self.pending_for_call(run_id, call.id)
        descriptor = self._descriptors.get(call.name)
        if descriptor is None:
            return AuthorizationResult(AuthorizationKind.DENIED,
                error_code=PermissionErrorCode.UNREGISTERED_TOOL)
        canonical = _canonicalize(call.arguments, descriptor)
        if request.tool_name != call.name or request.argument_digest != _digest(canonical):
            return AuthorizationResult(AuthorizationKind.DENIED,
                error_code=PermissionErrorCode.INVALID_ARGUMENTS,
                reason="call identity or arguments changed")
        if request.consumed:
            return AuthorizationResult(AuthorizationKind.DENIED,
                error_code=PermissionErrorCode.INVALID_ARGUMENTS, reason="permission already consumed")
        if request.decision is None:
            return AuthorizationResult(AuthorizationKind.PENDING, request=request)
        consumed = replace(request, consumed=True)
        self._requests[request.id] = consumed
        if request.decision.kind in {PermissionDecisionKind.ALLOW_ONCE,
                                     PermissionDecisionKind.ALLOW_RUN_SCOPE}:
            return AuthorizationResult(AuthorizationKind.ALLOWED, request=consumed)
        codes = {
            PermissionDecisionKind.DENY: PermissionErrorCode.USER_DENIED,
            PermissionDecisionKind.CANCEL: PermissionErrorCode.CANCELLED,
            PermissionDecisionKind.EXPIRED: PermissionErrorCode.EXPIRED,
        }
        return AuthorizationResult(AuthorizationKind.DENIED, request=consumed,
            error_code=codes[request.decision.kind], reason=request.decision.kind.value)

    def expire(self, *, now: datetime | None = None) -> tuple[str, ...]:
        with self._lock:
            return self._expire(now=now)

    def _expire(self, *, now: datetime | None) -> tuple[str, ...]:
        current = now or datetime.now(timezone.utc)
        expired = []
        for request in tuple(self._requests.values()):
            if request.decision is None and current >= request.expires_at:
                decision = PermissionDecision(request_id=request.id,
                    kind=PermissionDecisionKind.EXPIRED, origin="system",
                    argument_digest=request.argument_digest)
                self._requests[request.id] = replace(request, decision=decision)
                expired.append(request.id)
        return tuple(expired)

    def pending_for_call(self, run_id: str, call_id: str) -> PermissionRequest:
        with self._lock:
            request_id = self._request_by_call.get((run_id, call_id))
            if request_id is None:
                raise ValueError("permission request not found for call")
            return self._requests[request_id]

    def request(self, request_id: str) -> PermissionRequest:
        with self._lock:
            request = self._requests.get(request_id)
            if request is None:
                raise ValueError("permission request not found")
            return request


class AuthorizedToolDispatcher:
    """Executes registered functions only after PermissionService authorization."""

    def __init__(self, *, permission_service: PermissionService,
                 exposed_tools: set[str], run_id: str,
                 executors: dict[str, Callable[[dict[str, Any]], Any]],
                 headless: bool = False) -> None:
        self._permissions = permission_service
        self._exposed = set(exposed_tools)
        self._run_id = run_id
        self._executors = dict(executors)
        self._headless = headless

    def dispatch(self, call: ToolCall) -> ToolResult:
        authorization = self.authorize(call)
        if authorization.kind is AuthorizationKind.ALLOWED:
            return self.execute_authorized(call)
        return self._authorization_result(call, authorization)

    def authorize(self, call: ToolCall) -> AuthorizationResult:
        return self._permissions.authorize(
            self._run_id, call, exposed_tools=self._exposed, headless=self._headless)

    def _authorization_result(self, call: ToolCall,
                              authorization: AuthorizationResult) -> ToolResult:
        if authorization.kind is AuthorizationKind.PENDING:
            return ToolResult(call_id=call.id, status=ToolResultStatus.PENDING_PERMISSION,
                error="permission required", permission_request_id=authorization.request.id)
        if authorization.kind is AuthorizationKind.BLOCKED:
            return ToolResult(call_id=call.id, status=ToolResultStatus.DENIED,
                error=authorization.reason or authorization.error_code.value)
        if authorization.kind is AuthorizationKind.DENIED:
            return ToolResult(call_id=call.id, status=ToolResultStatus.DENIED,
                error=authorization.reason or (authorization.error_code.value if authorization.error_code else "denied"))

    def execute_authorized(self, call: ToolCall) -> ToolResult:
        executor = self._executors.get(call.name)
        if executor is None:
            return ToolResult(call_id=call.id, status=ToolResultStatus.FAILED,
                              error="registered executor is unavailable")
        try:
            output = executor(dict(call.arguments))
            return ToolResult(call_id=call.id, status=ToolResultStatus.SUCCEEDED,
                              output=str(output) if output is not None else "")
        except Exception as exc:
            return ToolResult(call_id=call.id, status=ToolResultStatus.FAILED,
                              error=f"tool execution failed: {exc}")


def _canonicalize(arguments: dict[str, Any], descriptor: ToolDescriptor) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    canonical = json.loads(json.dumps(arguments, sort_keys=True, separators=(",", ":")))
    for key in descriptor.path_arguments:
        if key in canonical:
            value = canonical[key]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} must be a non-empty path")
            canonical[key] = os.path.normcase(str(Path(value).expanduser().resolve(strict=False)))
    for key in descriptor.command_arguments:
        if key in canonical:
            value = canonical[key]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} must be a non-empty command")
            canonical[key] = value.strip().replace("\r\n", "\n")
    return canonical


def _digest(canonical_arguments: dict[str, Any]) -> str:
    encoded = json.dumps(canonical_arguments, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _scope_key(descriptor: ToolDescriptor, canonical_arguments: dict[str, Any]) -> str:
    value = {"tool": descriptor.name, "effects": descriptor.effects,
             "arguments": canonical_arguments}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
