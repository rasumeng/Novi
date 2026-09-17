"""Background run service — one coordinated execution off the request path.

Replaces the old WebUI background path that submitted Jobs against fake
``schedule-<run_id>`` task ids and then called the generic runtime directly.
A background run now uses the same durable RunService protocol as foreground:

    Background request
        ↓
    RunRequest → RunService → ordered RunEvents

No Job may exist whose ``task_id`` does not resolve to a TaskStore Task — the
coordinator creates the Job against the Task its orchestrator just planned, so
fake/orphan task ids are structurally impossible on this path.

The scheduler (``NoviContext._scheduled_trigger``) and the TaskQueue worker
both dispatch through :func:`run_background`; the WebUI wraps it with a
broadcast ``on_event`` so surfaced progress still reaches connected sockets.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from novi.runtime.run_contracts import RunEvent

log = logging.getLogger("novi.services.background")


class BackgroundRunResult:
    """Outcome of one background execution (ids + final answer text)."""

    def __init__(self, *, answer: str = "", task_id: str = "",
                 job_id: str = "", mode: str = ""):
        self.answer = answer
        self.task_id = task_id
        self.job_id = job_id
        self.mode = mode


def run_background(ctx, goal: str, *, conversation_id: str = "",
                   on_event: Optional[Callable[[RunEvent], None]] = None,
                   stop_check: Optional[Callable[[], bool]] = None,
                   attachments: Optional[list] = None,
                   metadata: Optional[dict] = None) -> BackgroundRunResult:
    """Execute one goal through the canonical headless RunService.

    ``on_event`` receives each persisted :class:`RunEvent` so callers surface
    progress without owning lifecycle logic or interpreting positional tuples.
    ``stop_check`` mirrors the WebUI stop flag: when it flips mid-stream the
    coordinator stops generation and finalises the Job.

    ``metadata`` is merged into the fresh Job's metadata so background /
    schedule / queue runs can be traced to their source (e.g.
    ``{"source": "background", "run_id": ...}``). The coordinator stays the
    only owner of Job creation — the caller only tags.

    Returns a :class:`BackgroundRunResult` with run-linked Task/Job ids when
    the request preparation layer assigned them.
    """
    from .run_composition import build_run_service
    from .run_render import execute_text, render_public_text

    service = build_run_service(ctx, headless=True)
    try:
        run_id, state, events = execute_text(service, ctx, goal,
            conversation_id or f"background:{id(service)}", attachments=attachments or ())
        for item in events:
            if on_event is not None:
                try:
                    on_event(item)
                except Exception as e:
                    log.warning("background on_event failed: %s", e)
        if stop_check is not None and stop_check() and not state.finished:
            state = service.cancel(run_id)

        return BackgroundRunResult(
            answer=render_public_text(state, events),
            task_id=state.task_id or "",
            job_id=state.job_id or "",
            mode="run_service",
        )
    finally:
        service.close()
