"""Tool for creating and managing scheduled agent runs."""

from . import register_tool

_global_scheduler = None


def init_scheduler_tool(scheduler):
    """Set the global scheduler instance for tool access."""
    global _global_scheduler
    _global_scheduler = scheduler


def get_scheduler():
    return _global_scheduler


@register_tool()
def schedule_task(goal: str, description: str = "", interval_minutes: int = 0) -> str:
    """Schedule an autonomous agent run at a regular interval or as a one-shot.

    The agent will execute the goal automatically when the schedule triggers.
    Results are logged and visible in the WebUI.

    Args:
        goal: The task or question for the agent to execute.
        description: Human-readable label (optional).
        interval_minutes: Minutes between runs (0 = one-shot, runs once at next check).
    """
    sched = get_scheduler()
    if sched is None:
        return "[error] Scheduler not available. Start the WebUI first."
    s = sched.add(goal, description, interval_minutes)
    freq = f"every {interval_minutes}min" if interval_minutes > 0 else "one-shot"
    return f"[ok] Scheduled '{s.description}' (id={s.id}, {freq})"


@register_tool()
def list_schedules() -> str:
    """List all active schedules."""
    sched = get_scheduler()
    if sched is None:
        return "[error] Scheduler not available."
    items = sched.list()
    if not items:
        return "[info] No schedules defined."
    lines = ["Active schedules:"]
    for s in items:
        status = "🟢" if s.enabled else "🔴"
        freq = f"every {s.interval_minutes}min" if s.interval_minutes > 0 else "one-shot"
        lines.append(f"  {status} {s.id}: {s.description} ({freq}) — goal: {s.goal[:80]}")
    return "\n".join(lines)


@register_tool()
def remove_schedule(schedule_id: str) -> str:
    """Remove a schedule by ID."""
    sched = get_scheduler()
    if sched is None:
        return "[error] Scheduler not available."
    ok = sched.remove(schedule_id)
    return f"[ok] Schedule {schedule_id} removed." if ok else f"[error] Schedule {schedule_id} not found."
