"""
JobStore — save/load Jobs and Checkpoints to/from JSON.

Stores files under ~/.novi/jobs/ as individual JSON files.
Supports list, get, save, delete operations.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .job import Checkpoint, Job, JobStatus, JobEvent
from ..paths import home as app_home

log = logging.getLogger("novi.jobs.persistence")

JOBS_DIR = app_home() / "jobs"


def _ensure_dir():
    JOBS_DIR.mkdir(parents=True, exist_ok=True)


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _checkpoint_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.checkpoint.json"


class JobStore:
    """Persists jobs and checkpoints as JSON files."""

    def save(self, job: Job) -> bool:
        """Save a job to disk."""
        _ensure_dir()
        try:
            data = {
                "id": job.id,
                "task_id": job.task_id,
                "status": job.status.value,
                "strategy": job.strategy,
                "retry_count": job.retry_count,
                "max_retries": job.max_retries,
                "events": [
                    {"type": e.type, "data": e.data, "timestamp": e.timestamp}
                    for e in job.events
                ],
                "error": job.error,
                "result": job.result,
                "started_at": job.started_at,
                "completed_at": job.completed_at,
                "created_at": job.created_at,
                "metadata": job.metadata,
                "checkpoint": job.checkpoint.to_dict() if job.checkpoint else None,
            }
            _job_path(job.id).write_text(
                json.dumps(data, indent=2, default=str), encoding="utf-8"
            )
            return True
        except Exception as e:
            log.warning("failed to save job %s: %s", job.id, e)
            return False

    def load(self, job_id: str) -> Optional[Job]:
        """Load a job from disk."""
        path = _job_path(job_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text("utf-8"))
            job = Job(
                id=data["id"],
                task_id=data.get("task_id", ""),
                status=JobStatus(data.get("status", "pending")),
                strategy=data.get("strategy", "execute"),
                retry_count=data.get("retry_count", 0),
                max_retries=data.get("max_retries", 2),
                events=[
                    JobEvent(type=e["type"], data=e.get("data", {}), timestamp=e.get("timestamp", ""))
                    for e in data.get("events", [])
                ],
                error=data.get("error", ""),
                result=data.get("result", ""),
                started_at=data.get("started_at"),
                completed_at=data.get("completed_at"),
                created_at=data.get("created_at", ""),
                metadata=data.get("metadata", {}),
            )
            cp_data = data.get("checkpoint")
            if cp_data:
                job.checkpoint = Checkpoint(
                    job_id=cp_data.get("job_id", job_id),
                    step=cp_data.get("step", 0),
                    messages=cp_data.get("messages", []),
                    tool_states=cp_data.get("tool_states", {}),
                    task_id=cp_data.get("task_id", ""),
                    plan_id=cp_data.get("plan_id", ""),
                    completed_steps=cp_data.get("completed_steps", []),
                    created_at=cp_data.get("created_at", ""),
                )
            return job
        except Exception as e:
            log.warning("failed to load job %s: %s", job_id, e)
            return None

    def delete(self, job_id: str) -> bool:
        """Delete a job file."""
        path = _job_path(job_id)
        cp_path = _checkpoint_path(job_id)
        try:
            if path.exists():
                path.unlink()
            if cp_path.exists():
                cp_path.unlink()
            return True
        except Exception as e:
            log.warning("failed to delete job %s: %s", job_id, e)
            return False

    def list_ids(self) -> list[str]:
        """List all stored job IDs."""
        _ensure_dir()
        return sorted(
            p.stem for p in JOBS_DIR.iterdir()
            if p.suffix == ".json" and not p.stem.endswith(".checkpoint")
        )

    def list(self) -> list[Job]:
        """Load all stored jobs."""
        jobs = []
        for jid in self.list_ids():
            job = self.load(jid)
            if job:
                jobs.append(job)
        return jobs

    def save_checkpoint(self, checkpoint: Checkpoint) -> bool:
        """Save a checkpoint separately for fast resume."""
        _ensure_dir()
        try:
            data = {
                "job_id": checkpoint.job_id,
                "step": checkpoint.step,
                "messages": checkpoint.messages,
                "tool_states": checkpoint.tool_states,
                "task_id": checkpoint.task_id,
                "plan_id": checkpoint.plan_id,
                "completed_steps": checkpoint.completed_steps,
                "created_at": checkpoint.created_at,
            }
            _checkpoint_path(checkpoint.job_id).write_text(
                json.dumps(data, indent=2, default=str), encoding="utf-8"
            )
            return True
        except Exception as e:
            log.warning("failed to save checkpoint %s: %s", checkpoint.job_id, e)
            return False

    def load_checkpoint(self, job_id: str) -> Optional[Checkpoint]:
        """Load a checkpoint for a given job."""
        cp_path = _checkpoint_path(job_id)
        if not cp_path.exists():
            return None
        try:
            data = json.loads(cp_path.read_text("utf-8"))
            return Checkpoint(
                job_id=data.get("job_id", job_id),
                step=data.get("step", 0),
                messages=data.get("messages", []),
                tool_states=data.get("tool_states", {}),
                task_id=data.get("task_id", ""),
                plan_id=data.get("plan_id", ""),
                completed_steps=data.get("completed_steps", []),
                created_at=data.get("created_at", ""),
            )
        except Exception as e:
            log.warning("failed to load checkpoint %s: %s", job_id, e)
            return None


# Statuses the startup sweep transitions to INTERRUPTED (Phase 6B).
#
# A persisted execution whose previous process disappeared while it was still
# "in flight" is abandoned work: RUNNING mid-plan, COMPLETING, or queued but
# never started (CREATED/PENDING/QUEUED). PAUSED is deliberately preserved — a
# pause is a user-initiated, intentional state whose resume path is
# JobManager.resume, not startup recovery. Terminal statuses (DONE/COMPLETED/
# ERROR/FAILED/CANCELLED) are never touched. INTERRUPTED itself is not in the
# set, which is what makes the sweep idempotent.
INTERRUPTIBLE_FROM_STARTUP = frozenset({
    JobStatus.RUNNING,
    JobStatus.COMPLETING,
    JobStatus.CREATED,
    JobStatus.PENDING,
    JobStatus.QUEUED,
})


def find_interrupted_jobs(store: "JobStore") -> list[dict]:
    """Return running/paused jobs left behind by a crash — candidates to resume.

    Startup detection (Phase 4D): Jobs persisted as RUNNING/PAUSED are
    interrupted work. This enumerates them so a future continuation flow can
    ask "Continue previous task?" with the exact playing fields a resume
    needs (task_id, plan_id, next step index, completed steps).

    Note (Phase 6B): this DISCOVERY helper reports both RUNNING and PAUSED as
    candidates, because PAUSED is genuinely resumable work. The startup SWEEP
    (``mark_interrupted`` / ``recover_interrupted_jobs``) deliberately does
    NOT transition PAUSED — a pause is an intentional state whose resume path
    is JobManager.resume. Discovery and transition are intentionally scoped
    differently. It performs no automatic resume.
    """
    candidates = []
    for job in store.list():
        if job.status not in (JobStatus.RUNNING, JobStatus.PAUSED):
            continue
        cp = job.checkpoint
        if cp:
            candidates.append({
                "job_id": job.id,
                "task_id": job.task_id,
                "status": job.status.value,
                "plan_id": cp.plan_id,
                # Checkpoint.step is the exact resume pointer (Phase 6A
                # contract): the completed-step count == 0-based index of the
                # next step. Expose it unchanged — never +1.
                "next_step": cp.step,
                "completed_steps": list(cp.completed_steps),
                "has_checkpoint": True,
                "started_at": job.started_at,
            })
        else:
            # No checkpoint yet → resume from the very beginning (step 0).
            candidates.append({
                "job_id": job.id,
                "task_id": job.task_id,
                "status": job.status.value,
                "plan_id": "",
                "next_step": 0,
                "completed_steps": [],
                "has_checkpoint": False,
                "started_at": job.started_at,
            })
    return candidates


def mark_interrupted(store: JobStore) -> list[dict]:
    """Startup recovery: flip abandoned nonterminal Jobs to INTERRUPTED.

    Transitions every persisted Job whose status is in
    ``INTERRUPTIBLE_FROM_STARTUP`` (RUNNING / COMPLETING / CREATED / PENDING /
    QUEUED) to INTERRUPTED and persists the result. PAUSED is preserved (a
    deliberate user pause — JobManager.resume remains its path) and terminal
    statuses are untouched. INTERRUPTED is not in the transition set, so
    running the sweep a second time is a no-op that returns no rows — the
    operation is idempotent and never re-emits transitions.

    The original Job object, its checkpoint, and its Task/Plan references are
    preserved verbatim; nothing is executed, resurrected, or deleted.

    Returns the enrichment rows so the continuation layer can still surface the
    interrupted work. ``next_step`` is exactly checkpoint.step (Phase 6A
    contract — the completed-step count / 0-based next index, never +1).
    """
    marked = []
    for job in store.list():
        if job.status not in INTERRUPTIBLE_FROM_STARTUP:
            continue
        job.status = JobStatus.INTERRUPTED
        store.save(job)
        cp = job.checkpoint
        marked.append({
            "job_id": job.id,
            "task_id": job.task_id,
            "status": "interrupted",
            "plan_id": cp.plan_id if cp else "",
            # Checkpoint.step is the exact resume pointer (Phase 6A
            # contract): the completed-step count == 0-based index of the
            # next step. Expose it unchanged — never +1.
            "next_step": cp.step if (cp and cp.step is not None) else 0,
            "completed_steps": list(cp.completed_steps) if cp else [],
            "has_checkpoint": cp is not None,
            "started_at": job.started_at,
        })
    return marked
