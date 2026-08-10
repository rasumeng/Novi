"""
JobManager — lifecycle for Jobs: submit, pause, resume, cancel, retry.

Owns all active/paused/completed jobs. Thread-safe.
Persistence delegated to JobStore (jobs/persistence.py).

Architecture:
  Orchestrator/Continuation → JobManager.submit() → Job
                              JobManager.pause()  → Checkpoint
                              JobManager.resume() → new Job from checkpoint
                              JobManager.cancel()
                              JobManager.retry()
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from typing import Callable, Optional

from .job import Checkpoint, Job, JobStatus

log = logging.getLogger("cozmo.jobs.manager")


EventSink = Callable[[str, dict], None]


class JobManager:
    """Owns job lifecycle. Thread-safe.

    Accepts an optional ``JobStore`` so every lifecycle transition is
    persisted, and an optional ``event_sink`` callable (``(type, data)``) so a
    passive projection (e.g. timeline) can observe lifecycle events without
    the manager importing any bus implementation.
    """

    def __init__(self, store: Optional["JobStore"] = None,
                 event_sink: Optional[EventSink] = None):
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._store = store
        self._event_sink = event_sink

    @property
    def store(self):
        return self._store

    def set_event_sink(self, sink: Optional[EventSink]):
        self._event_sink = sink

    def _next_id(self) -> str:
        """Collision-resistant Job id: ``job-<timestamp>-<uuid>``.

        The UUID suffix (not a per-manager counter) guarantees uniqueness
        across same-second creations, process restarts, and separate
        processes. Its prefix keeps ``job-`` sortability and leaves every
        already-persisted ``job-<ts>-<counter>`` id loadable unchanged.
        """
        ts = datetime.now().strftime("%y%m%d%H%M%S")
        return f"job-{ts}-{uuid.uuid4().hex[:12]}"

    def _persist(self, job: Job) -> Job:
        if self._store is not None:
            try:
                self._store.save(job)
            except Exception as e:
                log.warning("failed to persist job %s: %s", job.id, e)
        return job

    def _emit(self, event_type: str, job: Job, **data) -> None:
        if self._event_sink is None:
            return
        payload = {
            "job_id": job.id,
            "task_id": job.task_id,
            **data,
        }
        try:
            self._event_sink(event_type, payload)
        except Exception as e:
            log.warning("event sink failed for %s: %s", event_type, e)

    # ── lifecycle ────────────────────────────────────────────────────────

    def submit(self, task_id: str, strategy: str = "execute",
               max_retries: int = 2, metadata: dict | None = None,
               status: JobStatus = JobStatus.PENDING) -> Job:
        """Create and register a new Job."""
        job = Job(
            id=self._next_id(),
            task_id=task_id,
            status=status,
            strategy=strategy,
            max_retries=max_retries,
            metadata=metadata or {},
        )
        with self._lock:
            self._jobs[job.id] = job
        self._persist(job)
        self._emit("job.created", job, status=job.status.value)
        log.info("job submitted: %s (task=%s, strategy=%s)", job.id, task_id, strategy)
        return job

    def pause(self, job_id: str, checkpoint: Checkpoint | None = None) -> bool:
        """Pause a running job. Captures checkpoint if provided."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != JobStatus.RUNNING:
                return False
            job.status = JobStatus.PAUSED
            if checkpoint:
                job.checkpoint = checkpoint
        self._persist(job)
        self._emit("job.paused", job, step=checkpoint.step if checkpoint else None)
        log.info("job paused: %s (step=%s)", job_id,
                 checkpoint.step if checkpoint else "?")
        return True

    def resume(self, job_id: str) -> Job | None:
        """Resume a paused job. Creates a new Job linked via parent_job_id.

        Returns the new Job, or None if the original job can't be resumed.
        """
        with self._lock:
            original = self._jobs.get(job_id)
            if original is None or original.status != JobStatus.PAUSED:
                log.warning("cannot resume %s: status=%s", job_id,
                            original.status if original else "not found")
                return None
            if not original.can_resume:
                log.warning("cannot resume %s: no checkpoint", job_id)
                return None

            new_job = Job(
                id=self._next_id(),
                task_id=original.task_id,
                status=JobStatus.QUEUED,
                strategy=original.strategy,
                checkpoint=original.checkpoint,
                metadata={**original.metadata, "resumed_from": job_id},
            )
            self._jobs[new_job.id] = new_job

        self._persist(new_job)
        self._emit("job.resumed", new_job, resumed_from=job_id)
        log.info("job resumed: %s → %s (step=%s)", job_id,
                 new_job.id, new_job.checkpoint.step if new_job.checkpoint else "?")
        return new_job

    def reopen(self, job_id: str) -> Job | None:
        """Open a NEW attempt for a store-backed interrupted/paused job.

        Phase 5D continuation: a disk-loaded job (detected at startup via
        ``find_interrupted_jobs`` / ``mark_interrupted``) is *historical*. We
        never resurrect it. Instead we create a fresh Job carrying its
        checkpoint, and record ``resumed_from`` so the old attempt stays the
        durable record of what happened.

        Returns the new attempt, or None when the referenced job can't be
        reopened (unknown id, terminal status, or no checkpoint).
        """
        # Prefer the in-memory view, fall back to the persisted store.
        with self._lock:
            original = self._jobs.get(job_id)
        if original is None and self._store is not None:
            original = self._store.load(job_id)
        if original is None:
            log.warning("cannot reopen %s: not found", job_id)
            return None
        # Only genuinely finished attempts are dead-ends. INTERRUPTED jobs
        # are precisely the historical record a continuation reopens — they
        # are never resurrected (a NEW attempt is created), but they ARE a
        # valid resume source.
        _dead = frozenset({
            JobStatus.DONE, JobStatus.COMPLETED, JobStatus.ERROR,
            JobStatus.FAILED, JobStatus.CANCELLED,
        })
        if original.status in _dead:
            log.warning("cannot reopen %s: terminal status %s",
                        job_id, original.status.value)
            return None
        if original.checkpoint is None:
            log.warning("cannot reopen %s: no checkpoint", job_id)
            return None

        new_job = Job(
            id=self._next_id(),
            task_id=original.task_id,
            status=JobStatus.QUEUED,
            strategy=original.strategy,
            checkpoint=original.checkpoint,
            max_retries=original.max_retries,
            metadata={
                **original.metadata,
                "resumed_from": job_id,
                "reopen": True,
            },
        )
        with self._lock:
            self._jobs[new_job.id] = new_job
        self._persist(new_job)
        self._emit("job.resumed", new_job, resumed_from=job_id)
        log.info("job reopened: %s → %s (task=%s, step=%s)", job_id,
                 new_job.id, new_job.task_id,
                 new_job.checkpoint.step if new_job.checkpoint else "?")
        return new_job

    def cancel(self, job_id: str) -> bool:
        """Cancel a job. Works from any non-terminal status."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.is_done:
                return False
            job.status = JobStatus.CANCELLED
            job.completed_at = datetime.now().isoformat()
        self._persist(job)
        self._emit("job.cancelled", job)
        log.info("job cancelled: %s", job_id)
        return True

    def retry(self, job_id: str) -> Job | None:
        """Retry a failed or errored job. Creates a new Job."""
        with self._lock:
            original = self._jobs.get(job_id)
            if original is None:
                return None
            if original.retry_count >= original.max_retries:
                log.warning("max retries reached for %s (%d/%d)", job_id,
                            original.retry_count, original.max_retries)
                return None

            new_job = Job(
                id=self._next_id(),
                task_id=original.task_id,
                status=JobStatus.QUEUED,
                strategy=original.strategy,
                retry_count=original.retry_count + 1,
                max_retries=original.max_retries,
                metadata={**original.metadata, "retry_of": job_id},
            )
            self._jobs[new_job.id] = new_job

        self._persist(new_job)
        self._emit("job.created", new_job, retry_of=job_id,
                   retry_count=new_job.retry_count)
        log.info("job retry: %s → %s (attempt %d/%d)", job_id,
                 new_job.id, new_job.retry_count, new_job.max_retries)
        return new_job

    def start(self, job_id: str) -> bool:
        """Mark a job as running."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in (
                    JobStatus.PENDING, JobStatus.QUEUED, JobStatus.CREATED):
                return False
            job.status = JobStatus.RUNNING
            job.started_at = datetime.now().isoformat()
        self._persist(job)
        self._emit("job.started", job)
        return True

    def complete(self, job_id: str, result: str = "", error: str = "") -> bool:
        """Mark a job as completed (or errored, backward-compat).

        ``error`` truthy keeps the legacy ``ERROR`` status so existing retry
        callers keep working; a clean success is recorded as ``COMPLETED``.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if error:
                job.status = JobStatus.ERROR
                job.error = error
            else:
                job.status = JobStatus.COMPLETED
            job.result = result
            job.completed_at = datetime.now().isoformat()
        self._persist(job)
        self._emit("job.completed" if not error else "job.failed",
                   job, result=result[:500], error=error[:200])
        return True

    def fail(self, job_id: str, error: str = "") -> bool:
        """Mark a running job as failed (durable FAILED lifecycle, path )."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            job.status = JobStatus.FAILED
            job.error = error or job.error
            job.completed_at = datetime.now().isoformat()
        self._persist(job)
        self._emit("job.failed", job, error=error[:200])
        return True

    def checkpoint(self, job_id: str, checkpoint: Checkpoint | None = None) -> bool:
        """Persist a progress snapshot for a running job (additive, non-blocking).

        Job stays RUNNING. If a JobStore is wired the checkpoint is persisted
        separately; the running job also carries it for in-memory resume.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if checkpoint:
                job.checkpoint = checkpoint
        if self._store is not None and checkpoint is not None:
            try:
                self._store.save_checkpoint(checkpoint)
            except Exception as e:
                log.warning("failed to save checkpoint %s: %s", job_id, e)
        self._persist(job)
        self._emit("job.checkpointed", job,
                   step=checkpoint.step if checkpoint else None)
        return True

    # ── queries ──────────────────────────────────────────────────────────

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())

    def list_by_task(self, task_id: str) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.task_id == task_id]

    def active(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.is_running]

    def count_by_status(self) -> dict[str, int]:
        with self._lock:
            counts: dict[str, int] = {}
            for j in self._jobs.values():
                counts[j.status.value] = counts.get(j.status.value, 0) + 1
            return counts
