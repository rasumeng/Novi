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

from novi.runtime.run_contracts import RunEvent, RunImage

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
                   images: tuple[RunImage, ...] = (),
                   metadata: Optional[dict] = None) -> BackgroundRunResult:
    """Execute one goal through the canonical headless RunService.

    ``on_event`` receives each persisted :class:`RunEvent` so callers surface
    progress without owning lifecycle logic or interpreting positional tuples.
    ``stop_check`` cancels the durable run when the caller requests a stop.

    ``metadata`` is accepted for callers migrating from the Job API. This
    service does not create Jobs or interpret Job metadata.

    Returns a :class:`BackgroundRunResult` with run-linked Task/Job ids when
    the request preparation layer assigned them.
    """
    from .run_composition import build_run_service
    from .run_render import execute_text, render_public_text

    service = build_run_service(ctx, headless=True)
    try:
        def deliver(item):
            if on_event is not None:
                try:
                    on_event(item)
                except Exception as e:
                    log.warning("background on_event failed: %s", e)
        run_id, state, events = execute_text(service, ctx, goal,
            conversation_id or f"background:{id(service)}", images=images,
            on_event=deliver, stop_check=stop_check)

        return BackgroundRunResult(
            answer=render_public_text(state, events),
            task_id=state.task_id or "",
            job_id=state.job_id or "",
            mode="run_service",
        )
    finally:
        service.close()
