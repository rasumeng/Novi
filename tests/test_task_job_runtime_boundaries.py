"""Architecture regression guard — Task / Job / Runtime ownership boundaries.

Prevents future refactors from collapsing the three concepts together.

Milestone contract:
    Task    owns → durable intent: goal, conversation_id, plan reference,
                   task lifecycle state.
    Job     owns → execution ATTEMPT lifecycle: checkpointing, retries,
                   execution status.
    Runtime owns → executing tools/models, current execution mechanics.

    Task must NOT grow checkpoint/retry/execution-attempt/runtime state.
    Job must NOT grow plan/goal/conversation-identity state.
    Cross-subsystem imports are the collapse vector (jobs→orchestrator/runtime,
    runtime→jobs/task lifecycle, orchestrator→execution mechanics).

The guards are deliberate friction: changing a boundary forces editing this
file AND the contract in cozmo/orchestrator/task_types.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COZMO_SRC = PROJECT_ROOT / "cozmo"


# ── Documented field snapshots ──────────────────────────────────────────────

# ONLY fields Task may hold. Anything that stores execution mechanics here is
# a Task→Job/Runtime collapse.
TASK_OWNED_FIELDS = frozenset(
    {
        "id",
        "conversation_id",
        "raw_goal",
        "status",
        "goal",
        "profile",
        "plan",
        "execution_history",
        "result",
        "error",
        "parent_id",
        "priority",
        "created_at",
        "updated_at",
        "metadata",
    }
)

# Execution-attempt / runtime state that belongs to Job or Runtime, never Task.
TASK_FORBIDDEN_FIELD_TOKENS = frozenset(
    {
        "checkpoint",
        "retry",
        "attempt",
        "job_id",  # Task tracks attempts via execution_history only
        "current_step",
        "tool_state",
        "messages",
        "runtime",
        "engine",
        "progress",
    }
)

# ONLY fields Job may hold — the attempt lifecycle.
JOB_OWNED_FIELDS = frozenset(
    {
        "id",
        "task_id",
        "status",
        "strategy",
        "checkpoint",
        "retry_count",
        "max_retries",
        "events",
        "error",
        "result",
        "started_at",
        "completed_at",
        "created_at",
        "metadata",
    }
)

# Plan/goal/conversation identity that belongs to Task, never Job.
JOB_FORBIDDEN_FIELD_TOKENS = frozenset(
    {"goal", "plan", "intent", "conversation_id", "raw_goal", "user", "assistant"}
)


# ── Forbidden cross-subsystem import tokens ────────────────────────────────

ORCHESTRATOR_FORBIDDEN = frozenset(
    {"execution_context", "tool_executor", "tool_registry", "engine", "jobs"}
)
RUNTIME_FORBIDDEN = frozenset({"jobs", "task_store", "taskstore"})
JOBS_FORBIDDEN = frozenset({"orchestrator", "runtime"})

# Phase 4 (durable execution lifecycle) — the Runtime must not pull in the
# Job store/manager directly; it only emits plan/step events that a
# composition-root coordinator (cozmo/services) translates into Jobs.
RUNTIME_STORE_TOKENS = frozenset(
    {"JobStore", "JobManager", "job_store", "job_manager"}
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _read(*parts: str) -> str:
    return (COZMO_SRC.joinpath(*parts)).read_text("utf-8", errors="replace")


def _parse(text: str) -> ast.Module:
    return ast.parse(text)


def _dataclass_fields(text: str, class_name: str) -> set[str]:
    for node in ast.walk(_parse(text)):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                t.target.id
                for t in node.body
                if isinstance(t, ast.AnnAssign)
                and isinstance(t.target, ast.Name)
            }
    raise AssertionError(f"class {class_name} not found")


def _import_statements(text: str) -> list[tuple[int, str]]:
    out = []
    for node in ast.walk(_parse(text)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            out.append((node.lineno, ast.get_source_segment(text, node) or ""))
    return out


def _forbidden_imports(text: str, tokens: frozenset) -> list[str]:
    return [
        f"line {ln}: {stmt.strip()}"
        for ln, stmt in _import_statements(text)
        if any(tok in stmt for tok in tokens)
    ]


# ── Task owns Task-level state only ─────────────────────────────────────────


def test_task_fields_stay_within_ownership_snapshot():
    fields = _dataclass_fields(_read("orchestrator", "task_types.py"), "Task")
    extra = fields - TASK_OWNED_FIELDS
    assert extra == set(), (
        "Task gained fields; a legit Task/plan/lifecycle field must be "
        "documented in TASK_OWNED_FIELDS (task_types.py contract), otherwise "
        f"it belongs on Job/Runtime. Gained: {sorted(extra)}"
    )


def test_task_does_not_grow_execution_state():
    fields = _dataclass_fields(_read("orchestrator", "task_types.py"), "Task")
    violations = {
        f for f in fields
        if any(tok in f.lower() for tok in TASK_FORBIDDEN_FIELD_TOKENS)
    }
    assert violations == set(), (
        "Task gained execution-attempt/runtime state (belongs on Job or "
        f"Runtime): {sorted(violations)}"
    )


# ── Job owns the execution attempt only ─────────────────────────────────────


def test_job_fields_stay_within_ownership_snapshot():
    fields = _dataclass_fields(_read("jobs", "job.py"), "Job")
    extra = fields - JOB_OWNED_FIELDS
    assert extra == set(), (
        "Job gained fields outside the attempt-lifecycle snapshot; a legitimate "
        "attempt field must be documented in JOB_OWNED_FIELDS. "
        f"Gained: {sorted(extra)}"
    )


def test_job_does_not_own_plan_or_goal():
    fields = _dataclass_fields(_read("jobs", "job.py"), "Job")
    violations = {
        f for f in fields
        if any(tok in f.lower() for tok in JOB_FORBIDDEN_FIELD_TOKENS)
    }
    assert violations == set(), (
        "Job drifted into owning Task/plan/goal/conversation identity "
        f"(belongs on Task): {sorted(violations)}"
    )


# ── Import boundaries ───────────────────────────────────────────────────────


def test_jobs_does_not_import_orchestrator_or_runtime():
    """Job links to a Task by id string only — never by Task object/state."""
    violations = []
    for p in (COZMO_SRC / "jobs").glob("*.py"):
        rel = p.relative_to(PROJECT_ROOT)
        violations += [
            f"{rel}/{v}" for v in _forbidden_imports(p.read_text(encoding="utf-8"), JOBS_FORBIDDEN)
        ]
    assert violations == [], "jobs/ imported orchestrator or runtime:\n" + "\n".join(violations)


def test_runtime_does_not_import_job_or_task_lifecycle():
    """CozmoRuntime executes; it must not own Job or Task lifecycle."""
    # Legacy exception: engine.py is the (currently unused) checkpointed ReAct
    # loop. As the Job-checkpoint executor it legitimately consumes
    # Job.Checkpoint — this is Engine↔Job coordination, not Task/Runtime
    # collapse. Everything else in runtime/ must stay import-clean.
    allowed = {"engine.py"}
    violations = []
    for p in (COZMO_SRC / "runtime").glob("*.py"):
        if p.name in allowed:
            continue
        rel = p.relative_to(PROJECT_ROOT)
        violations += [
            f"{rel}/{v}" for v in _forbidden_imports(p.read_text("utf-8"), RUNTIME_FORBIDDEN)
        ]
    assert violations == [], "runtime/ imported jobs or TaskStore:\n" + "\n".join(violations)


def test_runtime_never_names_job_store_or_manager():
    """Phase 4: Runtime stays execution-only — no direct JobStore/JobManager use.

    The durable execution coordinator lives in cozmo/services, which may
    import everything; runtime must not grow a backdoor into persistence.
    """
    violations = []
    for f in (COZMO_SRC / "runtime").glob("*.py"):
        if f.name == "engine.py":  # legacy checkpoint loop, allowed
            continue
        rel = f.relative_to(PROJECT_ROOT)
        text = f.read_text("utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if any(tok in line for tok in RUNTIME_STORE_TOKENS):
                violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    assert violations == [], (
        "runtime/ referenced JobStore/JobManager (persistence must be driven "
        "from the composition root):\n" + "\n".join(violations)
    )


def test_job_does_not_gain_plan_or_goal_field():
    """Phase 4: Job references a Task by id only — never plan/goal ownership."""
    fields = _dataclass_fields(_read("jobs", "job.py"), "Job")
    assert not fields - JOB_OWNED_FIELDS, (
        f"Job gained non-attempt field(s): {sorted(fields - JOB_OWNED_FIELDS)}"
    )
    forbidden_extra = {
        f for f in fields
        if any(tok in f.lower() for tok in ("goal", "plan", "intent"))
    }
    assert forbidden_extra == set(), (
        "Job owns plan/goal/intent state (belongs on Task): "
        f"{sorted(forbidden_extra)}"
    )


def test_task_does_not_gain_checkpoint_field():
    """Phase 4: checkpoint ownership stays on Job — a Task never holds it."""
    fields = _dataclass_fields(_read("orchestrator", "task_types.py"), "Task")
    assert "checkpoint" not in fields, (
        "Task gained checkpoint state; a Task records attempts only via "
        "execution_history (job_id strings)."
    )


def test_orchestrator_does_not_import_execution_mechanics_or_jobs():
    """Orchestrator creates/lanes a Task — it never executes or runs jobs."""
    violations = []
    for p in (COZMO_SRC / "orchestrator").glob("*.py"):
        rel = p.relative_to(PROJECT_ROOT)
        violations += [
            f"{rel}/{v}" for v in _forbidden_imports(p.read_text("utf-8"), ORCHESTRATOR_FORBIDDEN)
        ]
    assert violations == [], (
        "orchestrator imported execution mechanics or jobs:\n" + "\n".join(violations)
    )