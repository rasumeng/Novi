"""Background run service — one coordinated execution off the request path.

Milestone 5 Phase 5E-2D: replaces the old WebUI background path that submitted
Jobs against fake ``schedule-<run_id>`` task ids and then called
``runtime.run_stream(goal)`` directly. A background run is now an
``ExecutionCoordinator`` run with a fresh runtime:

    Background request
        ↓
    Task
        ↓
    Plan
        ↓
    Job
        ↓
    Runtime

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
                   on_event: Optional[Callable[[tuple], None]] = None,
                   stop_check: Optional[Callable[[], bool]] = None,
                   attachments: Optional[list] = None,
                   metadata: Optional[dict] = None) -> BackgroundRunResult:
    """Execute one goal through the coordinator using a fresh runtime.

    ``on_event`` receives every streamed item (duck-typed tuples) so the
    caller can surface tool/log progress without owning any lifecycle logic.
    ``stop_check`` mirrors the WebUI stop flag: when it flips mid-stream the
    coordinator stops generation and finalises the Job.

    ``metadata`` is merged into the fresh Job's metadata so background /
    schedule / queue runs can be traced to their source (e.g.
    ``{"source": "background", "run_id": ...}``). The coordinator stays the
    only owner of Job creation — the caller only tags.

    Returns a :class:`BackgroundRunResult` with the coordinator-populated
    Task/Job ids so the caller can link queue-schedules to real Tasks.
    """
    from .execution import build_application_execution

    runtime, coordinator, _ = build_application_execution(ctx)
    parts = []

    for item in coordinator.run_stream(
        runtime,
        user_input=goal,
        conversation_id=conversation_id or "",
        attachments=attachments,
        stop_check=stop_check,
        metadata=metadata,
    ):
        if on_event is not None:
            try:
                on_event(item)
            except Exception as e:
                log.warning("background on_event failed: %s", e)
        if item and item[0] == "token":
            parts.append(str(item[1]))

    return BackgroundRunResult(
        answer="".join(parts).strip(),
        task_id=coordinator.task_id or "",
        job_id=coordinator.job_id or "",
        mode=coordinator.mode or "",
    )