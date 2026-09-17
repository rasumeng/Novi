"""Shared request preparation and plain-text rendering for run surfaces."""

from __future__ import annotations

from uuid import uuid4

from novi.runtime.run_contracts import RunEventType, RunRequest
from novi.services.run_composition import primary_model_snapshot


def execute_text(service, ctx, text: str, conversation_id: str, *,
                 attachments=(), project_id: str = "", workspace: str = "",
                 research_selected: bool = False):
    request = RunRequest(conversation_id=conversation_id,
        user_message_id=f"msg-{uuid4().hex}", user_text=text,
        project_id=project_id, workspace=workspace,
        attachments=tuple(attachments or ()), research_selected=research_selected,
        model=primary_model_snapshot(ctx))
    run_id = service.start(request)
    return run_id, service.snapshot(run_id), service.subscribe(run_id)


def render_public_text(state, events) -> str:
    parts = [event.payload.get("content", "") for event in events
             if event.type is RunEventType.MESSAGE_COMPLETED
             and event.payload.get("content")]
    if parts:
        return "\n".join(parts).strip()
    return state.terminal_reason or ""
