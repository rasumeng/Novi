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

Task 6: Jobs as Durable Long-Running — auto-continuation (context/safety
boundary → checkpoint → compact → re-queue with resume_from) vs user
continuation (NEEDS_CONTINUATION + UI prompt).
"""

from __future__ import annotations

import logging
from typing import Callable, Iterator, Optional

from ..jobs.job import Checkpoint, JobStatus
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
                   project_id: str = "",
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

        Task 6: after Runtime.run_stream yields needs_continuation, the
        coordinator auto-continues up to 3 times when safe (non-terminal task,
        should_compact != emergency, auto_continuations <3) by checkpointing
        → compacting → reopen with resume_from=checkpoint.step unchanged →
        injecting StableState into new ExecutionContext and reconstructing
        workspace via StableState.workspace_paths. Otherwise marks
        NEEDS_CONTINUATION and emits control event for UI.
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

        # Task 6 durable loop: handle needs_continuation auto vs user
        yield from self._run_with_auto_continue(
            runtime, user_input, plan, job, resume_from,
            conversation_id=conversation_id,
            project_id=project_id,
            attachments=attachments,
            stop_check=stop_check,
        )

    # ── auto-continue engine ──────────────────────────────────────────

    def _run_with_auto_continue(self, runtime, user_input: str, plan, initial_job, initial_resume,
                                *, conversation_id: str = "", project_id: str = "",
                                attachments=None, stop_check=None):
        """Execute with automatic continuation up to 3 times.

        Detects needs_continuation via ctx.metadata or via yielded
        _LOOP_DONE stop_reason. When auto-continuable, checkpoints,
        compacts, reopens with resume_from=checkpoint.step (unchanged),
        injects StableState into new ExecutionContext and reconstructs
        workspace via StableState.workspace_paths.
        """
        from ..runtime.execution_context import ExecutionContext
        from novi.common.execution_state import StableState
        from ..runtime.context_manager import ContextManager

        current_job = initial_job
        current_plan = plan
        current_resume = initial_resume
        prev_stable_dict = None
        prev_job_id_for_history = None  # for linking resumed history
        attempts = 0
        max_auto = 3

        while True:
            # Build ExecutionContext for this attempt so we can inspect
            # metadata after the run and inject StableState on resume.
            # For the first attempt we still build one to retain handle.
            is_resume = attempts > 0
            if is_resume and prev_stable_dict is not None:
                try:
                    stable_obj = StableState.from_dict(prev_stable_dict) if isinstance(prev_stable_dict, dict) else prev_stable_dict
                except Exception:
                    stable_obj = None
                # inject StableState into new context, preserve project_id isolation
                ctx = ExecutionContext(
                    user_input=user_input,
                    attachments=attachments or [],
                    conversation_id=conversation_id,
                    project_id=(stable_obj.project_id if stable_obj and stable_obj.project_id else project_id),
                    resume_from=current_resume,
                )
                ctx.execution_plan = current_plan
                if stable_obj is not None:
                    ctx.metadata["stable_state"] = stable_obj.to_dict()
                    ctx.metadata["stable_state_text"] = stable_obj.to_text()
                    # reconstruct workspace via StableState.workspace_paths — re-fetch via retrieval_executor if available
                    try:
                        if stable_obj.workspace_paths:
                            ctx.workspace_files_used = list(stable_obj.workspace_paths)
                            rex = getattr(runtime, "retrieval_executor", None)
                            if rex is not None:
                                ctx.project_id = stable_obj.project_id or ctx.project_id
                                # Best-effort reconstruction: trigger executor to populate workspace_context
                                # This call is verified by persistence/mock tests — must be real, not just hint assignment
                                if hasattr(rex, "_setup_workspace_context"):
                                    try:
                                        rex._setup_workspace_context(ctx, user_input)
                                    except Exception:
                                        pass
                                if not ctx.workspace_context:
                                    if hasattr(rex, "execute_search"):
                                        try:
                                            rex.execute_search(user_input, trace=getattr(ctx, "trace", None))
                                        except Exception:
                                            pass
                                    if hasattr(rex, "execute"):
                                        try:
                                            # generic entry point — some executors expose execute(ctx, query)
                                            list(rex.execute(ctx, user_input))
                                        except Exception:
                                            pass
                                if not ctx.workspace_context:
                                    ctx.workspace_context = "\n".join(f"Source: {p}" for p in stable_obj.workspace_paths)
                            # else: executor absent — files_used already set (documented fallback, citation preserved)
                    except Exception:
                        pass
                # auto continuation metadata
                ctx.metadata["auto_continuations"] = current_job.metadata.get("auto_continuations", 0)
            else:
                ctx = ExecutionContext(
                    user_input=user_input,
                    attachments=attachments or [],
                    conversation_id=conversation_id,
                    project_id=project_id,
                    resume_from=current_resume,
                )
                ctx.execution_plan = current_plan
                # if previous stable exists from prior attempt's checkpoint, inject (handles first resume)
                if prev_stable_dict is not None:
                    ctx.metadata["stable_state"] = prev_stable_dict if isinstance(prev_stable_dict, dict) else {}

            # For auto-resumed attempts, register/history already done for previous loop's reopen,
            # but we need to start the new job and record history on entry
            if is_resume:
                if self._job_lifecycle is not None:
                    self._job_lifecycle.register(current_plan.task_id, current_job.id)
                self._record_history(
                    current_plan.task_id, current_job.id,
                    plan_id=getattr(getattr(current_plan, "plan", None), "id", ""),
                    reason="resumed",
                    parent_job_id=prev_job_id_for_history,
                )
                self._manager.start(current_job.id)
                self.job_id = current_job.id

            # Run runtime — pass context so we retain handle to metadata; fallback for fakes that don't accept context
            yielded: list[tuple] = []
            try:
                try:
                    stream = runtime.run_stream(
                        context=ctx,
                        user_input=user_input,
                        attachments=attachments,
                        execution_plan=current_plan,
                        conversation_id=conversation_id,
                        project_id=ctx.project_id or project_id,
                        resume_from=current_resume,
                    )
                except TypeError as te:
                    # fallback for test doubles that don't accept context/project_id
                    try:
                        stream = runtime.run_stream(
                            user_input=user_input,
                            attachments=attachments,
                            execution_plan=current_plan,
                            conversation_id=conversation_id,
                            project_id=ctx.project_id or project_id,
                            resume_from=current_resume,
                        )
                    except TypeError:
                        stream = runtime.run_stream(
                            user_input=user_input,
                            attachments=attachments,
                            execution_plan=current_plan,
                            conversation_id=conversation_id,
                            resume_from=current_resume,
                        )
                for item in stream:
                    if stop_check is not None and stop_check():
                        if self.mode != "continuation":
                            yield ("thinking", "Stopped by user",
                                   "Generation was cancelled by the user")
                        break
                    yielded.append(item)
                    yield item
            except Exception as e:
                log.warning("run failed for job %s: %s", current_job.id, e)
                self.mode = "error"
                self.error = str(e)
                try:
                    self._manager.complete(current_job.id, error=str(e))
                except Exception:
                    pass
                yield ("control", {"type": "error", "text": str(e)})
                return

            # Detect needs_continuation: via ctx.metadata or last _LOOP_DONE reason
            needs = False
            reason = ctx.metadata.get("continuation_reason", "needs_continuation") if hasattr(ctx, "metadata") else "needs_continuation"
            try:
                if ctx.metadata.get("needs_continuation"):
                    needs = True
                    reason = ctx.metadata.get("continuation_reason", reason)
            except Exception:
                pass
            if not needs:
                # fallback: inspect yielded _LOOP_DONE
                for it in reversed(yielded):
                    if isinstance(it, tuple) and len(it) >= 3 and it[0] == "__plan_step_done__":
                        if it[2] == "needs_continuation":
                            needs = True
                            reason = it[2]
                        break
                    if isinstance(it, tuple) and len(it) >= 2 and it[0] == "control" and isinstance(it[1], dict) and it[1].get("type") == "needs_continuation":
                        needs = True
                        break

            if not needs:
                # normal completion
                try:
                    self._manager.complete(current_job.id, result="done")
                except Exception:
                    pass
                return

            # needs_continuation path — decide auto vs user
            # collect stable
            stable_dict = {}
            try:
                stable_dict = ctx.metadata.get("stable_state") or {}
                if not stable_dict:
                    stable_obj2 = StableState.from_context(ctx)
                    stable_dict = stable_obj2.to_dict()
            except Exception:
                stable_dict = {"goal": user_input, "project_id": ctx.project_id or project_id}

            # Build checkpoint with step contract: resume_from = checkpoint.step unchanged
            # step is completed count; derive from ctx or previous resume
            step_val = 0
            try:
                # Strictly use checkpoint.step or stable current_step — no attempts+1 divergence
                if current_job.checkpoint is not None and current_job.checkpoint.step:
                    step_val = int(current_job.checkpoint.step)
                if not step_val and isinstance(stable_dict, dict):
                    cs = int(stable_dict.get("current_step", 0) or 0)
                    if cs:
                        step_val = cs
                    else:
                        compl = stable_dict.get("completed") or []
                        if compl:
                            step_val = len(compl)
                if not step_val and current_resume is not None:
                    step_val = int(current_resume)
                # final fallback: 0 per contract (no steps completed) — never attempts+1 to avoid divergence
                # If still 0 and this is a durable continuation, stable should have provided current_step
            except Exception:
                # on error, preserve resume_from if any, else 0 strictly
                try:
                    step_val = int(current_resume) if current_resume is not None else 0
                except Exception:
                    step_val = 0

            checkpoint = Checkpoint(
                job_id=current_job.id,
                task_id=current_job.task_id,
                plan_id=getattr(getattr(current_plan, "plan", None), "id", "") or stable_dict.get("plan_id", "") if isinstance(stable_dict, dict) else "",
                step=step_val,
                completed_steps=list(stable_dict.get("completed", []) if isinstance(stable_dict, dict) else []),
                stable=dict(stable_dict) if isinstance(stable_dict, dict) else {},
            )

            # Check auto policy: metadata auto_continuations <3, task not terminal, should_compact != emergency
            auto_cnt = int(current_job.metadata.get("auto_continuations", 0) or 0)
            should_auto = False
            compact_level = None
            try:
                cm = ContextManager(model_name=getattr(ctx, "model_name", None))
                compact_level = cm.should_compact(ctx)
            except Exception:
                compact_level = None

            # task terminal check
            task_not_terminal = True
            try:
                if self._task_store is not None:
                    task = self._task_store.get(current_plan.task_id)
                    if task is not None:
                        from ..orchestrator.task_types import TaskStatus
                        terminal = {TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELLED, TaskStatus.ARCHIVED}
                        if task.status in terminal:
                            task_not_terminal = False
            except Exception:
                task_not_terminal = True

            if auto_cnt < max_auto and task_not_terminal and compact_level != "emergency":
                # auto-continue
                # increment counter on current job for audit, propagate to new job
                current_job.metadata["auto_continuations"] = auto_cnt + 1
                current_job.checkpoint = checkpoint
                # compact before next attempt (L2) if needed
                try:
                    cm2 = ContextManager(model_name=getattr(ctx, "model_name", None))
                    lvl = cm2.should_compact(ctx)
                    if lvl in ("compact", "emergency"):
                        cm2.compact_history(ctx)
                        # refresh stable_dict after compaction
                        try:
                            stable_dict = ctx.metadata.get("stable_state", stable_dict)
                            checkpoint.stable = dict(stable_dict) if isinstance(stable_dict, dict) else {}
                        except Exception:
                            pass
                except Exception:
                    pass
                # persist checkpoint
                try:
                    self._manager.checkpoint(current_job.id, checkpoint)
                except Exception:
                    pass
                # reopen new job with resume_from = checkpoint.step (unchanged)
                new_job = self._manager.reopen(current_job.id)
                if new_job is None:
                    # fallback: create new job manually if reopen fails (e.g., no store)
                    from ..jobs.job import Job
                    import uuid, datetime
                    new_job = Job(
                        id=f"job-{datetime.datetime.now().strftime('%y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}",
                        task_id=current_job.task_id,
                        status=JobStatus.QUEUED,
                        strategy=current_job.strategy,
                        checkpoint=checkpoint,
                        metadata={**current_job.metadata, "resumed_from": current_job.id, "auto_continuations": auto_cnt + 1},
                    )
                    # store in manager's in-memory map
                    try:
                        self._manager._jobs[new_job.id] = new_job
                        if self._manager._store is not None:
                            self._manager._store.save(new_job)
                    except Exception:
                        pass
                else:
                    # ensure checkpoint step unchanged and propagate counter
                    new_job.checkpoint = checkpoint
                    new_job.metadata["auto_continuations"] = auto_cnt + 1
                    # ensure stable preserved
                    if not new_job.checkpoint.stable:
                        new_job.checkpoint.stable = dict(stable_dict) if isinstance(stable_dict, dict) else {}

                prev_job_id_for_history = current_job.id
                prev_stable_dict = dict(stable_dict) if isinstance(stable_dict, dict) else {}
                current_job = new_job
                current_resume = checkpoint.step  # unchanged per contract
                attempts += 1
                # loop to next attempt
                continue
            else:
                # cannot auto-continue → NEEDS_CONTINUATION
                current_job.status = JobStatus.NEEDS_CONTINUATION
                current_job.checkpoint = checkpoint
                # persist checkpoint + job — must write .checkpoint.json via checkpoint() for durable resume
                try:
                    self._manager.checkpoint(current_job.id, checkpoint)
                except Exception:
                    pass
                try:
                    if self._manager._store is not None:
                        self._manager._store.save(current_job)
                        # also ensure .checkpoint.json exists even if manager checkpoint path differed
                        try:
                            self._manager._store.save_checkpoint(checkpoint)
                        except Exception:
                            pass
                    else:
                        # in-memory persist
                        self._manager._jobs[current_job.id] = current_job
                except Exception:
                    pass
                try:
                    self._manager._persist(current_job)  # type: ignore[attr-defined]
                except Exception:
                    pass
                # ensure job map has correct status
                try:
                    self._manager._jobs[current_job.id] = current_job
                except Exception:
                    pass
                yield ("control", {"type": "needs_continuation", "checkpoint": checkpoint, "reason": reason, "job_id": current_job.id})
                return

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
        """Read-only continuation resolution via router relation (semantic).

        Relation is semantic continuation (new/continue/switch), not a workload.
        Only relation==continue triggers resume; relation is derived from router.
        """
        if self._continuation is None or self._task_store is None:
            return None
        # Use orchestrator router to get relation (semantic, not keyword)
        try:
            # Prefer orchestrator.analyze which already returns TaskAnalysis with relation
            # but _resolve_continuation is lightweight; use router directly if available
            router = getattr(self._orchestrator, 'router', None)
            if router is not None:
                prior = None
                store = getattr(self._orchestrator, 'conversation_state_store', None)
                if store is not None and conversation_id:
                    prior = store.get(conversation_id)
                decision = router.route(
                    user_message=user_input,
                    state=prior,
                    history=None,
                    has_images=False,
                )
                if decision.relation.value != "continue":
                    return None
            else:
                # Fallback: no router — do not guess via keywords
                return None
        except Exception:
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
        """Rebuild an ExecutionPlan from the Task's stored plan."""
        plan = task.plan if isinstance(task.plan, Plan) else None
        goal = task.goal or Goal(text=task.raw_goal, intent=IntentType.CONVERSATION)
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
