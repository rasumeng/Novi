"""ExecutionCoordinator — the single ownership seam for one execution attempt.

Milestone 5 Phase 5E-1: extracts the imperative run orchestration that lived
in WebUI ``Session.start_run`` into a reusable service. One coordinated run
materializes exactly:

  fresh:        input → Orchestrator.plan → Task (get_or_create) → Job (submit)
                → Runtime.run_stream → Job COMPLETED + one ExecutionHistory entry
  continuation: input → ContinuationService.recommended → JobManager.reopen
                (new attempt) → Runtime.run_stream(resume_from) → Job COMPLETED
                + original ("interrupted") + resume ("resumed") history entries

Ownership contract:
  - The coordinator CREATES the Job (submit / reopen). It registers that Job
    with JobLifecycle so the event-driven path observes it instead of creating
    a second Job on ``plan.started`` (no double creation, no duplicate history).
  - The coordinator records ExecutionHistory exactly once per attempt.
  - The Runtime stays a pure plan executor — it never imports Jobs, the
    TaskStore, or this module (architecture guard).
  - ContinuationService stays read-only; the coordinator performs the hand-off
    (reopen a NEW attempt, never resurrect the interrupted Job).

This service is transport-agnostic: it yields structured items (the same
tuples Runtime.run_stream yields) plus ``("control", payload)`` messages
(continuation candidates / errors). Callers forward them to their channel.
"""

from __future__ import annotations

import logging
from typing import Callable, Iterator, Optional

from ..orchestrator.task_types import ExecutionPlan, Goal, IntentType
from ..planner.models import Plan

log = logging.getLogger("novi.services.execution")


class ExecutionCoordinator:
    """Composition root for a single Task/Plan/Job/Runtime execution."""

    def __init__(self, orchestrator=None, job_manager=None, task_store=None,
                 continuation=None, job_lifecycle=None):
        self._orchestrator = orchestrator
        self._manager = job_manager
        self._task_store = task_store
        self._continuation = continuation
        self._job_lifecycle = job_lifecycle

        # Per-run state (reset by _prepare / _on_stream_end).
        self.mode: str = "unset"        # fresh | continuation | ambiguous | error
        self.task_id: str = ""
        self.job_id: str = ""
        self.original_job_id: str = ""
        self.candidates: list = []
        self.error: str = ""

    @property
    def orchestrator(self):
        return self._orchestrator

    def run_stream(self, runtime, user_input: str, *, conversation_id: str = "",
                   attachments: Optional[list] = None,
                   stop_check: Optional[Callable[[], bool]] = None,
                   metadata: Optional[dict] = None,
                   force_intent: Optional[str] = None,
                   force_capability: Optional[str] = None,
                   force_model: Optional[str] = None,
                   ) -> Iterator[tuple]:
        """Drive one run though the full lifecycle. Yields runtime items.

        ``stop_check`` (optional callable) mirrors the WebUI stop_flag: when
        it returns True mid-stream, generation stops and the message is
        surfaced exactly like the previous Session behavior.

        ``metadata`` (optional dict) is merged into the fresh Job's metadata
        so a surface can tag its attempts (e.g. ``{"source": "background",
        "run_id": ...}``) without owning any lifecycle logic. It is ignored
        for continuations — a resumed attempt keeps the original attempt's
        metadata plus ``resumed_from``.

        ``force_intent`` / ``force_capability`` / ``force_model`` thread
        EXPLICIT user-mode overrides (e.g. Deep Research) into planning. They
        only apply to fresh runs — continuations resume the stored plan.
        """
        self.mode = "unset"
        self.task_id = ""
        self.job_id = ""
        self.original_job_id = ""
        self.candidates = []
        self.error = ""

        prepared = self._prepare(user_input, conversation_id, attachments,
                                 metadata=metadata,
                                 force_intent=force_intent,
                                 force_capability=force_capability,
                                 force_model=force_model)
        if prepared.get("ambiguous"):
            self.mode = "ambiguous"
            self.candidates = prepared["candidates"]
            yield ("control", {"type": "continuation_candidates",
                               "candidates": self.candidates})
            return
        if prepared.get("error"):
            self.mode = "error"
            self.error = prepared["error"]
            yield ("control", {"type": "error", "text": self.error})
            return

        plan = prepared["plan"]
        job = prepared["job"]
        resume_from = prepared.get("resume_from")
        self.task_id = plan.task_id
        self.job_id = job.id

        # Register the coordinator-created Job so a wired JobLifecycle observes
        # it (checkpoints, completion) instead of creating a duplicate.
        if self._job_lifecycle is not None:
            self._job_lifecycle.register(self.task_id, job.id)

        # History is owned and recorded here — exactly one entry per attempt.
        self._record_history(
            plan.task_id, job.id, plan_id=prepared.get("plan_ref_id", ""),
            reason=prepared.get("reason", "started"),
            parent_job_id=prepared.get("parent_job_id"),
        )

        self._manager.start(job.id)

        stream = runtime.run_stream(
            user_input=user_input,
            attachments=attachments,
            execution_plan=plan,
            conversation_id=conversation_id,
            resume_from=resume_from,
        )
        try:
            for item in stream:
                if stop_check is not None and stop_check():
                    # Parity with the pre-5E-1 Session: fresh runs surface a
                    # "Stopped by user" message; continuation stops silently.
                    if self.mode != "continuation":
                        yield ("thinking", "Stopped by user",
                               "Generation was cancelled by the user")
                    break
                yield item
        except Exception as e:
            log.warning("run failed for job %s: %s", job.id, e)
            self.mode = "error"
            self.error = str(e)
            try:
                self._manager.complete(job.id, error=str(e))
            except Exception:
                pass
            yield ("control", {"type": "error", "text": str(e)})
            return

        self._manager.complete(job.id, result="done")

    # ── preparation ─────────────────────────────────────────────────────

    def _prepare(self, user_input: str, conversation_id: str,
                 attachments: Optional[list] = None,
                 metadata: Optional[dict] = None,
                 force_intent: Optional[str] = None,
                 force_capability: Optional[str] = None,
                 force_model: Optional[str] = None) -> dict:
        """Resolve continuation or plan fresh; open the Job attempt."""
        continuation = self._resolve_continuation(user_input, conversation_id)
        if continuation is not None:
            if continuation.get("ambiguous"):
                return {"ambiguous": True,
                        "candidates": continuation["candidates"]}
            target = continuation["target"]
            task = continuation["task"]
            plan = self._continuation_exec_plan(task, target)
            new_job = self._manager.reopen(target.job_id)
            if new_job is None:
                return {"error": "That task can no longer be resumed."}
            self.mode = "continuation"
            self.original_job_id = target.job_id
            return {
                "plan": plan,
                "job": new_job,
                "resume_from": target.next_step,
                "reason": "resumed",
                "parent_job_id": target.job_id,
                "plan_ref_id": getattr(getattr(plan, "plan", None), "id", ""),
            }

        plan = self._orchestrator.plan(
            user_input=user_input,
            has_images=bool(
                attachments and any(a.get("type") == "image" for a in attachments)
            ),
            conversation_id=conversation_id or None,
            force_intent=force_intent,
            force_capability=force_capability,
            force_model=force_model,
        )
        job = self._manager.submit(
            task_id=plan.task_id,
            strategy=plan.strategy.value,
            metadata={
                "intent": plan.goal.intent.value,
                "tools": plan.tools,
                **(metadata or {}),
            },
        )
        self.mode = "fresh"
        return {
            "plan": plan,
            "job": job,
            "resume_from": None,
            "reason": "started",
            "plan_ref_id": getattr(getattr(plan, "plan", None), "id", ""),
        }

    def _resolve_continuation(self, user_input: str,
                              conversation_id: str) -> Optional[dict]:
        """Read-only continuation resolution (mirrors Session behavior).

        Returns None when nothing is resumable (fresh planning happens) or
        when the request is not a continuation. Returns ``{"ambiguous": True,
        "candidates": [...]}`` when there is resumable work but no clear
        single target — the caller surfaces candidates to the user.
        """
        if self._continuation is None or self._task_store is None:
            return None
        intent, _ = self._orchestrator.intent_detector.detect(user_input)
        if intent is not IntentType.CONTINUATION:
            return None
        target = self._continuation.recommended(
            conversation_id=conversation_id or None)
        if target is None:
            return {
                "ambiguous": True,
                "candidates": [t.to_dict() for t in self._continuation.candidates()],
            }
        task = self._task_store.get(target.task_id)
        if task is None or task.plan is None:
            return None
        return {"target": target, "task": task, "ambiguous": False}

    def _continuation_exec_plan(self, task, target) -> ExecutionPlan:
        """Rebuild an ExecutionPlan from the Task's stored plan.

        The Task already owns a Plan (PlannerEngine attached it at creation).
        The plan object is reused — no replanning. ``resume_from`` is threaded
        separately into run_stream, not baked into the plan.
        """
        plan = task.plan if isinstance(task.plan, Plan) else None
        goal = task.goal or Goal(text=task.raw_goal, intent=IntentType.CONTINUATION)
        return ExecutionPlan(
            task_id=task.id,
            goal=goal,
            strategy="execute",
            capabilities=getattr(task, "capabilities", None) or [],
            tools=[],
            model_spec={"model": "", "supports_tools": True},
            max_steps=10,
            temperature=0.2,
            plan=plan,
            context={"task_id": task.id, "resumed": True},
        )

    # ── history ─────────────────────────────────────────────────────────

    def _record_history(self, task_id: str, job_id: str, *,
                        plan_id: str = "", reason: str = "started",
                        parent_job_id: Optional[str] = None) -> None:
        """Append ONE ExecutionHistory entry for an attempt (idempotent).

        For a resumed attempt this also records the original job it continues,
        mirroring Phase 5D semantics: original ("interrupted") + resume
        ("resumed", linked via parent_job_id). Duplicate job_ids never produce
        a second entry.
        """
        if self._task_store is None or not task_id:
            return
        task = self._task_store.get(task_id)
        if task is None:
            return
        if parent_job_id is not None:
            if task.execution_history.find(parent_job_id) is None:
                task.execution_history.add(
                    parent_job_id, reason="interrupted", parent_job_id=None)
        if task.execution_history.find(job_id) is None:
            task.execution_history.add(
                job_id, reason=reason or "started", parent_job_id=parent_job_id)
        try:
            self._task_store.update(task)
        except Exception as e:
            log.warning("failed to persist execution history for %s: %s",
                        task_id, e)


def build_application_execution(ctx, *, project_index=None, auto: bool = False):
    """Compose a synchronous surface's runtime + ExecutionCoordinator (5E-2).

    One composition root for every non-WebUI execution surface (CLI, Telegram,
    TaskQueue, background runs). Builds a fresh event bus, a runtime wired with
    the task/job lifecycle projections, and the authoritative coordinator so
    the surface only ever talks to the coordinator seam — never straight to the
    runtime.

    Ownership:
      - The ctx supplies resolved configuration (adapters consume it, they do
        not own it). The coordinator is never given settings.
      - ``job_lifecycle`` is threaded from the ctx so coordinator-created Jobs
        are registered (no duplicate Job from ``plan.started``).
      - ``auto`` mirrors the legacy CLI ``--auto``: it flags the runtime's
        permission policy to bypass asks (headless surfaces fail safe by
        denying, exactly as the previous CLI did).

    Returns ``(runtime, coordinator, event_bus)``.

    Startup recovery: any Job left nonterminal by a previous process is
    marked INTERRUPTED once per NoviContext (``ctx.recover_once``) before the
    surface can start new work — parity with the WebUI ``warmup`` sweep, so
    CLI/Telegram never leave persisted executions stranded.
    """
    from ..runtime.event_bus import EventBus

    bus = EventBus()
    recover = getattr(ctx, "recover_once", None)
    if recover is not None:
        recover(bus=bus)
    runtime = ctx.create_runtime(
        project_index=project_index,
        event_bus=bus,
        job_lifecycle=getattr(ctx, "job_lifecycle", None),
    )
    if auto:
        perms = getattr(runtime, "_perms", None)
        if perms is not None:
            perms.auto = True
    coordinator = ExecutionCoordinator(
        orchestrator=ctx.orchestrator,
        job_manager=ctx.job_manager,
        task_store=getattr(ctx.orchestrator, "task_store", None),
        continuation=getattr(ctx, "continuation", None),
        job_lifecycle=getattr(ctx, "job_lifecycle", None),
    )
    return runtime, coordinator, bus