"""Shared request preparation and plain-text rendering for run surfaces."""

from __future__ import annotations

from uuid import uuid4
import threading

from novi.runtime.run_contracts import RunEventType, RunImage, RunRequest
from novi.services.run_composition import primary_model_snapshot


def execute_text(service, ctx, text: str, conversation_id: str, *,
                 images: tuple[RunImage, ...] = (), project_id: str = "", workspace: str = "",
                 research_selected: bool = False, on_event=None, stop_check=None):
    request = RunRequest(conversation_id=conversation_id,
        user_message_id=f"msg-{uuid4().hex}", user_text=text,
        project_id=project_id, workspace=workspace,
        images=images, research_selected=research_selected,
        model=primary_model_snapshot(ctx))
    run_id = service.prepare(request)
    unsubscribe = service.listen(
        lambda event: on_event(event) if event.run_id == run_id else None
    ) if on_event else None
    finished = threading.Event()
    watcher = None

    def watch_cancellation():
        while not finished.wait(0.05):
            if stop_check():
                service.cancel(run_id)
                return

    try:
        if stop_check and stop_check():
            service.cancel(run_id)
        else:
            if stop_check:
                watcher = threading.Thread(target=watch_cancellation, daemon=True)
                watcher.start()
            service.execute(run_id)
        return run_id, service.snapshot(run_id), service.subscribe(run_id)
    finally:
        finished.set()
        if watcher:
            watcher.join()
        if unsubscribe:
            unsubscribe()


def render_public_text(state, events) -> str:
    parts = [event.payload.get("content", "") for event in events
             if event.type is RunEventType.MESSAGE_COMPLETED
             and event.payload.get("content")]
    if parts:
        return "\n".join(parts).strip()
    return state.terminal_reason or ""
