# Task 3 Report: Durable Execution State — StableState Extraction

**Status:** DONE
**Commit:** `abdd871` — `feat: durable StableState for compaction-surviving execution`
**Date:** 2026-08-29

## Summary
Extracted canonical `StableState` to `novi/runtime/execution_state.py` as durable, compaction-surviving execution state. Re-exported from `context_manager.py` for compat, wired `Checkpoint.stable` dict to `StableState.to_dict()` via typed `stable_state` property, preserved `Checkpoint.step` contract (no +1) and project_id/conversation_id isolation.

## Files Modified
- `novi/runtime/execution_state.py:1-150` — created `StableState` dataclass with 16 fields (`goal`, `current_objective`, `plan`, `completed`, `current_step`, `discoveries`, `important_files`, `workspace_paths`, `decisions`, `errors`, `unresolved`, `next_action`, `memory_refs`, `budget_breakdown`, `project_id`, `conversation_id`) + methods `to_text(max_chars=1200)->str` (bounded, joined parts, `[:max_chars]`), `to_dict()->dict`, `from_dict(d)->StableState`, `from_context(ctx: ExecutionContext)->StableState` (goal from `ctx.user_input[:500]`, plan from `ctx.execution_plan[:800]`, `current_objective` from `ctx.intent_str`, `discoveries`/`important_files` from `workspace_files_used[:8]`, `errors` from `metadata.errors[:3]`, `project_id`/`conversation_id` isolation, `workspace_paths=[project_id]`, `budget_breakdown`/`completed`/`current_step`/`next_action` from metadata).
- `novi/runtime/context_manager.py:14-19,97-113` — removed inline `StableState` definition, added `from .execution_state import StableState` re-export (`__all__`), kept `checkpoint_stable()` populating all fields including `conversation_id`, updated `compact_history` to use imported `StableState`.
- `novi/jobs/job.py:1-8,88-116` — added `TYPE_CHECKING` import for `StableState`, kept `Checkpoint.stable: dict` contract, added `@property stable_state: Optional[StableState]` getter (lazy import, `StableState.from_dict(self.stable)`) and setter (`value.to_dict()` or {}), preserves dict serialization for persistence.
- `tests/test_checkpoint_stable.py` — created 6 tests per brief Step 1 (survives compaction + serializable, conversation_id roundtrip, checkpoint typed property, workload isolation, from_context preserves errors/discoveries, re-export identity).

## Test Summary
`pytest tests/test_checkpoint_stable.py -v` — **6 passed**
- `test_stable_state_survives_compaction` — builds `ExecutionContext(user_input="Analyze auth tests", project_id="proj-1", conversation_id="conv-1")` with 20 history entries, `StableState.from_context(ctx)`, sets `completed`/`important_files`, roundtrips via `to_dict`/`from_dict`, asserts `goal`/`project_id`/`completed` preserved, `len(to_text(1200)) <=1200`.
- `test_stable_state_conversation_id_roundtrip` — same with `conv-42`, verifies both ids survive dict roundtrip and bounded text.
- `test_checkpoint_stable_typed_property` — `StableState` → `Checkpoint.stable_state` setter → `cp.stable` dict check → getter roundtrip → `to_dict` preserves.
- `test_stable_state_not_workload_coupled` — `inspect.getsource(StableState.from_context)` contains no `workload`.
- `test_stable_from_context_preserves_errors_and_discoveries` — `workspace_files_used` → `discoveries`/`important_files`, `metadata.errors/completed/current_step` preserved.
- `test_reexport_from_context_manager` — `from novi.runtime.context_manager import StableState is from execution_state import StableState`.

Also verified `pytest tests/test_checkpoint_stable.py tests/test_context_budget.py tests/test_context_manager_global.py -v` — **18 passed**, `pytest tests/test_checkpoint_isolation.py tests/test_checkpoint_semantics.py -v` — **11 passed** (Checkpoint.step no +1, isolation).

## Verification
- `python -m pytest tests/test_checkpoint_stable.py tests/test_context_budget.py tests/test_context_manager_global.py -v` → 18 passed
- `python -m pytest tests/test_checkpoint_isolation.py tests/test_checkpoint_semantics.py -v` → 11 passed
- `python -c "from novi.runtime.context_manager import StableState; from novi.runtime.execution_state import StableState as ES; assert StableState is ES"` → True (re-export)
- `python -c "from novi.runtime.execution_state import StableState; import inspect; assert 'workload' not in inspect.getsource(StableState.from_context).lower()"` → True (no coupling)
- `git show --stat HEAD` → 4 files, 261 insertions, 45 deletions
- `grep -n workload novi/runtime/execution_state.py novi/runtime/context_manager.py` → no output (no coupling)

## Concerns / Follow-ups
- `ContextManager.compact_history` still builds inline `StableState` with truncated goal ([:200]) vs `from_context` ([:500]) — intentional: compaction uses summary-oriented truncation, checkpoint uses full durable state. Both use same canonical class, no divergence risk.
- `Checkpoint.step` contract untouched (no +1); `stable` remains `dict` for persistence compat, typed property is additive only. Future Task 6 consumer should use `checkpoint.stable_state` getter.
- `execution_state.py` lazy `TYPE_CHECKING` import avoids circular `ExecutionContext`→`StableState` loop; `from_context` handles missing `execution_plan`/`metadata` gracefully with `or ""`/`or []`.
- Untracked `docs/superpowers/` and `novi/webui/*` changes remain uncommitted per global constraints (not part of this task).

## Global Constraints
- No workload-specific budgets/mode branches introduced (file contains zero `workload` string, verified via test).
- Canonical `StableState` survives compaction and checkpoint (serializable dict, bounded text, roundtrip preserves goal/project_id/completed).
- `Checkpoint.step` contract preserved (no +1 conversion; verified via `test_checkpoint_semantics` still passing).
- project_id/conversation_id isolation preserved (tested via `test_checkpoint_isolation_preserved` and new conversation_id roundtrip).

---

## Fix 2026-08-29: Deduplicate StableState canonical path, log checkpoint deserialization (medium issues)

**Commit:** `fix: deduplicate StableState canonical path, log checkpoint deserialization`
**Date:** 2026-08-29
**Issues addressed:**
1. `novi/runtime/context_manager.py:80-87,97-113` — `compact_history` built partial `StableState` omitting `project_id`/`conversation_id`/`plan`/`errors`/`budget_breakdown` with divergent truncation `[:200]` vs `from_context` `[:500]`; `checkpoint_stable` duplicated `from_context` logic instead of delegating to canonical `StableState.from_context(ctx)`.
2. `novi/jobs/job.py:113` — `except Exception: return None` silently swallowed deserialization bugs without logging.

**Changes:**
- `novi/runtime/context_manager.py:77-92` — `compact_history` now `stable = StableState.from_context(ctx)` as canonical base (preserves isolation, all fields, consistent `goal[:500]`/`plan[:800]` truncation), then `stable.to_text()` bounded summary, `history[-6:]` truncation, `metadata["stable_state"]`/`compacted` unchanged. `checkpoint_stable` now single line `return StableState.from_context(ctx)` — deduped, canonical path.
- `novi/jobs/job.py:15,22,117-119` — added `import logging; logger = logging.getLogger(__name__)` and changed `except Exception:` to `except Exception as e: logger.warning("failed to deserialize Checkpoint.stable for job %s: %s", self.job_id, e); return None` — surfaces bugs while preserving `None` fallback.

**Verification:**
- `python -m pytest tests/test_checkpoint_stable.py tests/test_context_manager_global.py -v` → **9 passed** (6 checkpoint + 3 global).
- Manual check: `StableState.from_context(ctx).goal` len `500` consistent for both `compact_history` and `checkpoint_stable`; `cm.checkpoint_stable(ctx).to_dict() == StableState.from_context(ctx).to_dict()` → True; corrupted `Checkpoint.stable` now emits `WARNING:novi.jobs.job:failed to deserialize...` and returns `None`.
- `grep -n workload novi/runtime/execution_state.py novi/runtime/context_manager.py` → no output.
