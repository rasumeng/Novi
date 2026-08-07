"""
JobStore — save/load Jobs and Checkpoints to/from JSON.

Stores files under ~/.cozmo/jobs/ as individual JSON files.
Supports list, get, save, delete operations.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .job import Checkpoint, Job, JobStatus, JobEvent

log = logging.getLogger("cozmo.jobs.persistence")

JOBS_DIR = Path.home() / ".cozmo" / "jobs"


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


def find_interrupted_jobs(store: "JobStore") -> list[dict]:
    """Return running/paused jobs left behind by a crash — candidates to resume.

    Startup detection (Phase 4D): Jobs persisted as RUNNING/PAUSED are
    interrupted work. This enumerates them so a future continuation flow can
    ask "Continue previous task?" with the exact playing fields a resume
    needs (task_id, plan_id, next step index, completed steps). It performs no
    automatic resume.
    """
    candidates = []
    for job in store.list():
        if job.status not in (JobStatus.RUNNING, JobStatus.PAUSED):
            continue
        cp = job.checkpoint
        candidates.append({
            "job_id": job.id,
            "task_id": job.task_id,
            "status": job.status.value,
            "plan_id": cp.plan_id if cp else "",
            "next_step": (cp.step + 1) if (cp and cp.step is not None) else 0,
            "completed_steps": list(cp.completed_steps) if cp else [],
            "has_checkpoint": cp is not None,
            "started_at": job.started_at,
        })
    return candidates


def mark_interrupted(store: JobStore) -> list[dict]:
    """Startup recovery: flip RUNNING jobs to INTERRUPTED and persist.

    INTERRUPTED is preferred over auto-resume. Returns the same enrichment
    rows ``recover_interrupted_jobs`` produces so the continuation layer can
    still ask the user. Persists every transition.
    """
    marked = []
    for job in store.list():
        if job.status != JobStatus.RUNNING:
            continue
        job.status = JobStatus.INTERRUPTED
        store.save(job)
        cp = job.checkpoint
        marked.append({
            "job_id": job.id,
            "task_id": job.task_id,
            "status": "interrupted",
            "plan_id": cp.plan_id if cp else "",
            "next_step": (cp.step + 1) if (cp and cp.step is not None) else 0,
            "completed_steps": list(cp.completed_steps) if cp else [],
            "has_checkpoint": cp is not None,
            "started_at": job.started_at,
        })
    return marked
