"""
Startup interruption recovery — the composition-root sweep (Milestone 5 Phase 6B).

A crashed process can leave a persisted Job in a nonterminal state forever. This
module is the single startup hook that recognizes that abandonment on the next
application boot:

    start up
        → enumerate persisted Jobs left nonterminal by the previous process
        → transition them to INTERRUPTED (idempotent, terminal-safe, PAUSED-safe)
        → preserve their checkpoint + Task/Plan references
        → emit the established ``job.interrupted`` lifecycle event (once per
          actual transition) into the surface's event bus
        → leave them discoverable by ContinuationService

It NEVER executes a Job, never creates or resurrects Jobs, never alters the
checkpoint's resume pointer, and never touches ExecutionHistory. Continuation
remains an explicit, user-initiated act — there is no automatic resume.

Ownership: this lives at the composition root (co/services) so every execution
surface that constructs a Cozmo application shares the same recovery behavior.
The Runtime, TaskStore, and the planner stay untouched, and the durable
transition itself remains in jobs/persistence.py (``mark_interrupted``).
"""

from __future__ import annotations

import logging
from typing import Optional

from ..jobs.persistence import JobStore, mark_interrupted

log = logging.getLogger("cozmo.services.recovery")

INTERRUPT_EVENT = "job.interrupted"


def recover_interrupted_jobs(store: JobStore, *, bus=None) -> list[dict]:
    """Idempotent startup sweep over persisted Jobs.

    Delegates the durable RUNNING→INTERRUPTED transition to
    ``mark_interrupted`` (jobs/persistence.py), which only ever transitions
    statuses in ``INTERRUPTIBLE_FROM_STARTUP`` and leaves PAUSED + terminal
    jobs untouched. ``job.interrupted`` is then emitted — once per row that
    ACTUALLY transitioned — over the duck-typed ``bus`` (EventBus), the same
    path ``JobLifecycle.subscribe`` uses for manager lifecycle events, so
    passive projections such as TimelineService observe the interruption.

    Because ``mark_interrupted`` returns nothing for a second run (INTERRUPTED
    is not re-transitioned and no rows are produced), running the sweep twice
    is a no-op: identical durable state, zero duplicate events.

    Returns the marked rows (job/task/plan references + ``next_step`` ==
    checkpoint.step, never +1).
    """
    marked = mark_interrupted(store)
    if bus is not None:
        for row in marked:
            try:
                bus.emit(
                    INTERRUPT_EVENT,
                    job_id=row["job_id"],
                    task_id=row["task_id"],
                    step=row["next_step"],
                    has_checkpoint=row["has_checkpoint"],
                )
            except Exception as e:
                log.warning("failed to emit %s for %s: %s",
                            INTERRUPT_EVENT, row["job_id"], e)
    return marked