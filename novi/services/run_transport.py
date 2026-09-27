"""Transport-neutral projection and controller for socket-based run clients."""

from __future__ import annotations

import threading
from typing import Callable

from novi.runtime.run_contracts import RunEvent, RunEventType, RunRequest
from novi.services.permission_service import PermissionDecision, PermissionDecisionKind


def event_to_socket_message(event: RunEvent) -> dict | None:
    """Convert RunEvent to WebSocket message (new format only — includes runId, conversationId, sequence)."""
    payload = event.payload
    common = {"runId": event.run_id, "conversationId": event.conversation_id,
              "sequence": event.sequence}
    kind = event.type
    if kind in {RunEventType.RUN_STARTED, RunEventType.RUN_STATE_CHANGED}:
        return {"type": "run_state", "status": payload.get("status", "running"), **common}
    if kind is RunEventType.MESSAGE_STARTED:
        return {"type": "message_start", "messageId": payload.get("message_id"), **common}
    if kind is RunEventType.MESSAGE_DELTA:
        return {"type": "token", "text": payload.get("content", ""),
                "messageId": payload.get("message_id"), **common}
    if kind is RunEventType.MESSAGE_REASONING:
        return {"type": "reasoning", "text": payload.get("content", ""),
                "messageId": payload.get("message_id"), **common}
    if kind is RunEventType.MESSAGE_COMPLETED:
        return {"type": "message_end", "messageId": payload.get("message_id"), **common}
    if kind is RunEventType.TOOL_REQUESTED:
        return {"type": "tool_call", "id": payload.get("call_id"),
                "tool": payload.get("name"), "args": payload.get("arguments", {}), **common}
    if kind is RunEventType.TOOL_STARTED:
        return {"type": "tool.started", "id": payload.get("call_id"),
                "tool": payload.get("name"), "args": payload.get("arguments", {}), **common}
    if kind is RunEventType.TOOL_COMPLETED:
        result = payload.get("output") or payload.get("error") or ""
        return {"type": "tool_result", "id": payload.get("call_id"),
                "tool": payload.get("name"), "result": result,
                "status": payload.get("status"), "diff": payload.get("diff"), **common}
    if kind is RunEventType.PERMISSION_REQUESTED:
        return {"type": "permission_request", "id": payload.get("request_id"),
                "tool": payload.get("name"), "args": payload.get("arguments", {}),
                "digest": payload.get("argument_digest"),
                "effects": payload.get("effects", []),
                "expiresAt": payload.get("expires_at"),
                "proposedDiff": payload.get("proposed_diff"), **common}
    if kind is RunEventType.PERMISSION_RESOLVED:
        return {"type": "permission_resolved", "id": payload.get("request_id"),
                "decision": payload.get("decision"), **common}
    if kind is RunEventType.THINKING:
        return {"type": "thinking", "text": payload.get("text", ""), **common}
    if kind is RunEventType.STATUS:
        return {"type": "status", "text": payload.get("text", ""), **common}
    # The client requires consecutive run sequences, including context events.
    if kind is RunEventType.CONTEXT_COMPACTING:
        return {"type": "status", "text": "Compacting conversation context…", **common}
    if kind is RunEventType.CONTEXT_COMPACTED:
        return {"type": "status", "text": "Conversation context compacted.", **common}
    if kind is RunEventType.RUN_COMPLETED:
        return {"type": "done", **common}
    if kind is RunEventType.RUN_CANCELLED:
        return {"type": "cancelled", **common}
    if kind in {RunEventType.RUN_FAILED, RunEventType.RUN_BLOCKED,
                RunEventType.RUN_INTERRUPTED}:
        return {"type": "error", "text": payload.get("reason", kind.value),
                "status": kind.value.removeprefix("run."), **common}
    return None


class RunSocketBridge:
    """Attach/detach socket clients without transferring run ownership."""

    def __init__(self, service, emit: Callable[[dict], None]) -> None:
        self._service = service
        self._emit = emit
        self._run_id: str | None = None
        self._delivery_lock = threading.RLock()
        self._unsubscribe = service.listen(self._on_event)
        self._worker: threading.Thread | None = None

    @property
    def run_id(self) -> str | None:
        return self._run_id

    @property
    def busy(self) -> bool:
        if self._worker and self._worker.is_alive():
            return True
        if not self._run_id:
            return False
        try:
            return not self._service.snapshot(self._run_id).finished
        except Exception:
            return False

    def start(self, request: RunRequest) -> str:
        run_id = self._service.prepare(request)
        self._run_id = run_id
        self._worker = threading.Thread(
            target=self._execute, args=(run_id,), daemon=True)
        self._worker.start()
        return run_id

    def respond_permission(self, request_id: str, allowed: bool) -> None:
        request = self._service.permission_request(request_id)
        decision = PermissionDecision(request_id=request.id,
            kind=(PermissionDecisionKind.ALLOW_ONCE if allowed
                  else PermissionDecisionKind.DENY),
            origin="socket", argument_digest=request.argument_digest)
        threading.Thread(target=self._service.respond_permission,
                         args=(request_id, decision), daemon=True).start()

    def replay(self, run_id: str, after_sequence: int = 0) -> None:
        # Serialize replay with live delivery; duplicate sequences are safe on clients.
        with self._delivery_lock:
            self._run_id = run_id
            for event in self._service.subscribe(run_id, after_sequence):
                self._on_event(event)

    def cancel(self) -> None:
        if self._run_id:
            self._service.cancel(self._run_id)

    def detach(self) -> None:
        self._unsubscribe()

    def _execute(self, run_id: str) -> None:
        try:
            self._service.execute(run_id)
        except Exception as exc:
            self._emit({"type": "error", "text": f"run transport error: {exc}",
                        "runId": run_id})

    def _on_event(self, event: RunEvent) -> None:
        with self._delivery_lock:
            if self._run_id and event.run_id != self._run_id:
                return
            message = event_to_socket_message(event)
            if message is not None:
                self._emit(message)
