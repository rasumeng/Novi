"""Phase 6A — genuine process-boundary recovery driver.

Runs as TWO genuinely separate Python interpreters modelling an interruption
and its recovery across disk (Process A -> disk -> Process B). Never drive two
"managers" in one process; each invocation is its own fresh process.

  --phase a --dir <dir>   Process A: build fresh stores, start a real Job,
                          persist a durable checkpoint (step 0 done), then
                          exit WITHOUT finishing the Job (crash model).
  --phase b --dir <dir>   Process B: reconstruct stores from the SAME dir,
                          load the persisted Task/Job/Checkpoint, resolve the
                          continuation, reopen the interrupted Job into a NEW
                          attempt, resume, and prove every remaining step runs
                          (a completed Step 0 must never skip Step 1).

Non-zero exit = any required invariant failed. Phase B also writes
<dir>/report.json with the recovery facts for the pytest-side assertions.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CONVERSATION_ID = "conv-recovery"


def _require(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAILED: {msg}")
        sys.exit(1)


def _ensure_store_path(root: Path):
    """Mount the shared store directory from the pytest-side temp dir."""
    from pathlib import Path

    import cozmo.jobs.persistence as persistence

    jobs_dir = Path(root) / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    persistence.JOBS_DIR = jobs_dir
    return root / "tasks", jobs_dir


def _make_plan(task):
    from cozmo.planner.models import Plan, PlanStep

    plan = Plan(id="plan-r", task_id=task.id)
    for i, desc in enumerate(["a", "b", "c"]):
        plan.add_step(PlanStep(id=f"plan-r-s{i+1}", plan_id="plan-r",
                               description=desc))
    return plan


# ── deterministic model double (no Ollama, no external services) ─────────────

class _FakeModel:
    """One non-tool chunk per remaining plan step; records call count."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._i = 0
        self.calls = 0

    def list_available(self):
        return {}

    def resolve(self, role, *a, **k):
        return (None, "m1")

    def bind_model(self, *a, **k):
        return self

    def client_for_model(self, *a, **k):
        return self

    def stream(self, messages):
        self.calls += 1
        if self._i >= len(self._chunks):
            raise RuntimeError("fake model exhausted")
        from langchain_core.messages import AIMessageChunk

        item = self._chunks[self._i]
        self._i += 1
        c = AIMessageChunk(content=item)
        c.additional_kwargs = {}
        yield c


# ── Process A: interrupted execution ─────────────────────────────────────────

def phase_a(root: Path) -> None:
    _ensure_store_path(root)

    from cozmo.jobs.job import JobStatus
    from cozmo.jobs.manager import JobManager
    from cozmo.jobs.persistence import JobStore
    from cozmo.orchestrator.task_store import TaskStore
    from cozmo.orchestrator.task_types import Goal, IntentType, Task

    task_store = TaskStore(persist_dir=root / "tasks")
    job_store = JobStore()

    task = Task(
        id="task-r",
        conversation_id=CONVERSATION_ID,
        raw_goal="build the widget",
        goal=Goal(text="build the widget", intent=IntentType.CODING),
    )
    task.plan = _make_plan(task)
    task_store.save(task)

    manager = JobManager(store=job_store)
    job = manager.submit(task_id=task.id, strategy="planned")
    _require(manager.start(job.id), "Process A: job should start")

    # Step 0 completed, then the process "crashes": the Job stays RUNNING on
    # disk with a durable checkpoint. Checkpoint.step == 1 == completed count
    # == the 0-based index of Step 1.
    from cozmo.jobs.job import Checkpoint

    manager.checkpoint(job.id, Checkpoint(
        job_id=job.id,
        task_id=task.id,
        plan_id=task.plan.id,
        step=1,
        completed_steps=[task.plan.steps[0].id],
    ))

    task.execution_history.add(job.id, reason="interrupted")
    _require(task_store.update(task), "Process A: task history persist")

    # Durability check BEFORE exiting: the checkpoint must be on disk.
    cp = job_store.load_checkpoint(job.id)
    _require(cp is not None, "Process A: checkpoint must be durable on disk")
    _require(cp.step == 1, "Process A: durable checkpoint step must be 1")
    stored = job_store.load(job.id)
    _require(stored is not None and stored.status is JobStatus.RUNNING,
             "Process A: job must remain RUNNING (crash, not finished)")

    (root / "report-a.json").write_text(
        json.dumps({"job_id": job.id, "checkpoint_step": cp.step}),
        encoding="utf-8",
    )
    print(f"Process A: job={job.id} checkpoint_step={cp.step} — durable, exiting")


# ── Process B: recovery in a fresh interpreter ───────────────────────────────

class _ContIntent:
    """Forces CONTINUATION so the coordinator takes the resume path."""

    def detect(self, user_input, history=None, has_images=False):
        from cozmo.orchestrator.task_types import IntentType

        return (IntentType.CONTINUATION, 1.0)


def _recovery_assertions(root: Path) -> dict:
    from cozmo.jobs.job import Checkpoint, JobStatus
    from cozmo.jobs.manager import JobManager
    from cozmo.jobs.persistence import JobStore
    from cozmo.orchestrator import Orchestrator
    from cozmo.orchestrator.task_store import TaskStore
    from cozmo.runtime.event_bus import EventBus
    from cozmo.runtime.event_bus import EventType
    from cozmo.services.continuation import ContinuationService
    from cozmo.services.execution import ExecutionCoordinator
    from cozmo.services.job_lifecycle import JobLifecycle
    from cozmo.services.recovery import INTERRUPT_EVENT, recover_interrupted_jobs

    task_store = TaskStore(persist_dir=root / "tasks")
    job_store = JobStore()

    # 1. Load persisted state from disk (fresh objects in this process).
    task = task_store.get("task-r")
    _require(task is not None, "Process B: Task must survive on disk")
    _require(task.plan is not None and task.plan.step_count == 3,
             "Process B: 3-step Plan must survive on disk")

    # 2. Startup recovery: the previous process crashed while the Job was still
    #    RUNNING. The composition hook must recognize that abandonment,
    #    transition the Job to INTERRUPTED, preserve everything, and emit the
    #    job.interrupted lifecycle event — WITHOUT executing anything.
    bus = EventBus()
    events = []
    bus.on_any(lambda ev: events.append(ev))

    marked = recover_interrupted_jobs(job_store, bus=bus)
    _require(len(marked) == 1,
             f"Process B: exactly one Job must be marked interrupted, got "
             f"{len(marked)}")
    _require(marked[0]["status"] == "interrupted",
             f"Process B: marked status must be interrupted, got "
             f"{marked[0]['status']}")
    interrupted_id = marked[0]["job_id"]

    original = job_store.load(interrupted_id)
    _require(original is not None, "Process B: original Job must be loadable")
    _require(original.status is JobStatus.INTERRUPTED,
             f"Process B: original Job must be INTERRUPTED after startup "
             f"recovery, got {original.status.value}")
    _require(original.checkpoint is not None
             and original.checkpoint.step == 1,
             f"Process B: checkpoint must survive intact with step 1, got "
             f"{original.checkpoint.step if original.checkpoint else None}")
    _require(original.checkpoint.completed_steps == ["plan-r-s1"],
             f"Process B: completed steps must survive intact, got "
             f"{original.checkpoint.completed_steps}")
    _require(original.task_id == "task-r",
             "Process B: Task reference must survive intact")
    _require(original.checkpoint.plan_id == "plan-r",
             "Process B: Plan reference must survive intact")
    _require(len(job_store.list_ids()) == 1,
             "Process B: startup recovery must not create a new Job")
    jobs_after_recovery = len(job_store.list_ids())

    _require(any(e.type == INTERRUPT_EVENT for e in events),
             "Process B: startup recovery must emit job.interrupted")
    _require(not any(e.type == EventType.STEP_STARTED for e in events)
             and not any(e.type == EventType.PLAN_STARTED for e in events),
             "Process B: recovery must never start execution (no auto-resume)")
    original_id = original.id

    # 3. Resolve the continuation (read-only resolver over the interrupted
    #    Job). next_step must EQUAL checkpoint.step == 1 — no +1.
    service = ContinuationService(task_store=task_store, job_store=job_store)
    target = service.recommended(conversation_id=CONVERSATION_ID)
    _require(target is not None, "Process B: resumable work must be found")
    cp = target.checkpoint
    _require(cp is not None, "Process B: target must carry a checkpoint")
    _require(cp.step == 1, "Process B: checkpoint survived with step == 1")
    _require(target.next_step == cp.step == 1,
             f"Process B: next_step must equal checkpoint.step, got "
             f"next_step={target.next_step} cp.step={cp.step}")

    manager = JobManager(store=job_store)

    # 4. Resume execution through the production coordinator seam with a real
    #    CozmoRuntime (deterministic model double — no Ollama needed). The
    #    coordinator EXPLICITLY reopens the interrupted Job into a NEW attempt.
    lifecycle = JobLifecycle(manager, task_store=task_store).subscribe(bus)
    fake = _FakeModel(["b-result", "c-result"])       # Steps 1 and 2 only

    from cozmo.runtime.runtime import CozmoRuntime

    runtime = CozmoRuntime(model_service=fake, event_bus=bus)
    coordinator = ExecutionCoordinator(
        orchestrator=Orchestrator(intent_detector=_ContIntent(),
                                  task_store=task_store),
        job_manager=manager,
        task_store=task_store,
        continuation=service,
        job_lifecycle=lifecycle,
    )
    items = list(coordinator.run_stream(
        runtime, "continue the task", conversation_id=CONVERSATION_ID))
    # A real planned run ends with plan.completed (there is no "assistant"
    # tuple in the planned path).
    _require(any(i[0] == "plan.completed" for i in items),
             "Process B: resume run must complete the plan")

    # 5. The resumed attempt is distinct, linked to the original, and carries
    #    the original checkpoint.
    resumed_id = coordinator.job_id
    _require(resumed_id != original_id, "Process B: resumed Job must be distinct")
    resumed = manager.list_by_task(original.task_id)
    resumed = next((j for j in resumed if j.id == resumed_id), None)
    _require(resumed is not None, "Process B: resumed Job must be registered")
    _require(resumed.metadata.get("resumed_from") == original_id,
             "Process B: resumed attempt must be linked to the original")
    _require(resumed.status is JobStatus.COMPLETED,
             f"Process B: resumed Job must complete durably, got "
             f"{resumed.status.value}")

    # 6. Behavioral proof: only Steps 1 and 2 execute, exactly once; Step 0 is
    #    never re-executed and Step 1 is never skipped.
    _require(fake.calls == 2,
             f"Process B: steps 1+2 must execute exactly once, model called "
             f"{fake.calls} times instead")
    started = [e.data["index"] for e in events if e.type == EventType.STEP_STARTED]
    completed = [e.data["index"] for e in events
                 if e.type == EventType.STEP_COMPLETED]
    _require(started == [1, 2],
             f"Process B: step starts must be [1, 2], got {started}")
    _require(0 not in started and 0 not in completed,
             "Process B: Step 0 must never re-execute during resume")

    # The resumed attempt's durable checkpoint proves Steps 1 and 2 ran and
    # the plan reached its end (step == 3 == all three completed).
    final_cp = job_store.load_checkpoint(resumed_id)
    _require(final_cp is not None and final_cp.step == 3,
             f"Process B: resumed checkpoint must reach step 3, got "
             f"{final_cp.step if final_cp else None}")
    _require(set(final_cp.completed_steps) == {"plan-r-s2", "plan-r-s3"},
             f"Process B: resumed checkpoint must record steps 1+2, got "
             f"{final_cp.completed_steps}")

    # 7. Original Job preserved (INTERRUPTED, historical), resumed Job distinct.
    original_now = job_store.load(original_id)
    _require(original_now.id == original_id,
             "Process B: original Job must remain preserved")
    _require(original_now.status is JobStatus.INTERRUPTED,
             f"Process B: original Job must stay INTERRUPTED, got "
             f"{original_now.status.value}")
    _require(original_now.checkpoint.step == 1,
             "Process B: original Job checkpoint must stay at step 1")
    resumed_now = job_store.load(resumed_id)
    _require(resumed_now is not None and resumed_now.status is JobStatus.COMPLETED,
             "Process B: resumed Job must complete durably")

    # 8. ExecutionHistory carries original + resumed attempts, no duplicates.
    tasks_now = task_store.get("task-r")
    _require(tasks_now is not None, "Process B: Task must reload after resume")
    _require(tasks_now.execution_history.count() == 2,
             f"Process B: history must hold 2 attempts, has "
             f"{tasks_now.execution_history.count()}")
    _require(tasks_now.execution_history.all_job_ids == [original_id, resumed_id],
             f"Process B: history order original→resume, got "
             f"{tasks_now.execution_history.all_job_ids}")

    return {
        "original_job_id": original_id,
        "resumed_job_id": resumed_id,
        "checkpoint_step": cp.step,
        "next_step": target.next_step,
        "model_calls": fake.calls,
        "started_indexes": started,
        "history": tasks_now.execution_history.all_job_ids,
        "original_status": original_now.status.value,
        "recovery_marked": len(marked),
        "interrupted_event": any(e.type == INTERRUPT_EVENT for e in events),
        "no_auto_resume": True,
        "jobs_after_recovery": jobs_after_recovery,
        "jobs_after_resume": len(job_store.list_ids()),
    }


def phase_b(root: Path) -> None:
    _ensure_store_path(root)
    report = _recovery_assertions(root)
    (root / "report.json").write_text(json.dumps(report, indent=2),
                                      encoding="utf-8")
    print(f"Process B: recovery OK {report}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=("a", "b"), required=True)
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()

    root = Path(args.dir)
    root.mkdir(parents=True, exist_ok=True)
    os.chdir(str(PROJECT_ROOT))
    if args.phase == "a":
        phase_a(root)
    else:
        phase_b(root)


if __name__ == "__main__":
    main()