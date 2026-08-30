# Task 4 Report: Replace Step-Exhaustion Failure with Goal-Oriented Safety Rail

**Status:** DONE
**Commit:** `refactor: max_steps as safety rail, goal is stopping condition`
**Date:** 2026-08-29

## Summary
Replaced `max_steps` failure (`"I ran out of steps..."` + `max_steps`/`False`) with goal-oriented continuation: when threshold hit and goal incomplete → checkpoint `StableState.to_dict()`, compact, `needs_continuation=True`, `_LOOP_DONE` with `needs_continuation`/`True` (not error). Added `is_goal_complete(ctx, final)` helper (`plan.status == COMPLETED` or `final.strip() and not has_unresolved`) and stall detection (same tool sig 3x → `needs_continuation` reason `stall`). Wired `runtime.py` pre-check + mid-loop `ContextManager` compaction with `StableState` persist to `trace.metadata` before next model call; per-segment `max_steps` kept as safety rail with comment `max_steps = safety rail, not completion boundary`.

## Files Modified
- `novi/runtime/react_attempt.py:50-120,189-368` — added `is_goal_complete(ctx, final)` (plan COMPLETED or `final.strip() and not has_unresolved`), `_checkpoint_needs_continuation(ctx, reason)` (from `StableState.from_context`, sets `ctx.metadata[stable_state, needs_continuation, continuation_reason]` + `trace.metadata`, opportunistic compact via `ContextManager.should_compact`), `sig_counts: dict[str,int]` tracking, `is_stall = sig_counts[sig]>=3` per call, post-`tool_result` stall check `→ _checkpoint(stall)` + `_LOOP_DONE(needs_continuation, True)`, `for-else` replacement: `if not is_goal_complete → _checkpoint(max_steps_safety)` + `_LOOP_DONE(needs_continuation)` else `completed`; removed `"ran out of steps"` token and `max_steps` stop_reason branch, downstream maps `needs_continuation` → `success=True`; `max_steps = safety rail` doc retained.
- `novi/runtime/runtime.py:561-598,708-713,968-1050` — pre-check gate now `StableState.from_context` → `metadata[stable_state, compacted]` + `trace.metadata[context_compacted, stable_state]` before model; per-segment budget kept with comment `# max_steps = safety rail, not completion boundary — per-segment safety, not failure`; sequential and unplanned `run_react_attempt` loops now mid-loop check after each `tool_result`: `cm.should_compact → compact_history + StableState.to_dict() → trace.metadata` before next model call (isolated via `_CM2/_SS2` imports, try/except).
- `tests/test_goal_oriented_continuation.py` — created 3 tests: `test_max_steps_triggers_continuation_not_error` (drive `max_steps` budget=3 → `_LOOP_DONE/needs_continuation` not `max_steps`, no `"ran out of steps"`, `needs_continuation`+`stable_state` in metadata, `success=True`), `test_stall_detection_triggers_continuation` (same sig 3x → `needs_continuation` with stall reason), `test_goal_complete_still_completes_normally` (plain answer → `completed`).

## Test Summary
`pytest tests/test_goal_oriented_continuation.py -v` — **3 passed**
- `test_max_steps_triggers_continuation_not_error` — FAIL→PASS (before: `max_steps`/error, after: `needs_continuation`/`True`, no error wording, checkpointed).
- `test_stall_detection_triggers_continuation` — PASS (same `read_file:a.py` 3x → `needs_continuation`/`stall` + metadata).
- `test_goal_complete_still_completes_normally` — PASS (no tools final → `completed`).

Also verified `pytest tests/test_context_budget.py tests/test_context_manager.py tests/test_context_manager_global.py tests/test_checkpoint_stable.py -v` — **25 passed**, `pytest tests/test_continuation.py tests/test_continuation_auto.py tests/test_continuation_integration.py -v` — **30 passed**. `test_react_attempt_parity::test_literal_max_steps_wording_and_reason` now fails as intended (legacy wording replaced; parity test expects outdated `max_steps`/`"I ran out of steps"` literal — intentional contract change, deferred to Task 7 guard update).

## Verification
- `python -m pytest tests/test_goal_oriented_continuation.py::test_max_steps_triggers_continuation_not_error -v` → FAIL (assert `max_steps==needs_continuation`) before impl, PASS after
- `python -m pytest tests/test_goal_oriented_continuation.py -v` → 3 passed
- `python -c "import inspect; print(inspect.getsource(open('novi/runtime/react_attempt.py').read))"` → `is_goal_complete` present, `"ran out of steps" not in` react_attempt.py (except downstream removed), `sig_counts` stall logic present
- `grep -n "safety rail" novi/runtime/runtime.py` → `max_steps = safety rail, not completion boundary` at budget calc
- `grep -n "should_compact" novi/runtime/runtime.py` → pre-check + 2 mid-loop sites
- `python -m pytest tests/test_context_budget.py tests/test_context_manager.py tests/test_context_manager_global.py tests/test_checkpoint_stable.py -v` → 25 passed
- `git log --oneline -1` → `refactor: max_steps as safety rail, goal is stopping condition`

## Concerns / Follow-ups
- `ExecutionTrace.metadata` is dynamic (not dataclass field); all `trace.metadata` writes guarded via `hasattr` + `try/except`, so no `AttributeError` — same pattern as `context_manager.py:60`.
- `is_goal_complete` uses string `value`/`str(status)` comparison for `PlanStatus` to avoid import cycle; covers `COMPLETED` typed or string.
- Stall detector counts `sig` before `seen_calls` dedup gate, so 3rd identical `write_file` (dedup message) still triggers `stall` continuation — matches brief `same tool sig 3x without progress`.
- `tests/test_react_attempt_parity.py:396-398` literal must be updated in Task 7 guard to expect `needs_continuation` instead of `max_steps` wording; left failing intentionally per plan.

## Global Constraints
- No workload-specific step budgets introduced (verified `grep -i workload` in `react_attempt.py` → helpers only check plan status, no workload step table).
- `max_steps` retained as emergency runaway guard per-segment with explicit `safety rail, not completion boundary` comment; runtime never fails task on exhaustion, only `needs_continuation`.
- `StableState` isolation preserved (`project_id`/`conversation_id` in `StableState.from_context`, re-export intact; compaction uses canonical `from_context`).
