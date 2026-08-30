# Task 2 Report: Global ContextManager — Decouple from Workload/Workspace

**Status:** DONE
**Commit:** `9c2fcb2` — `refactor: make ContextManager global agent infra, no workload coupling`
**Date:** 2026-08-29

## Summary
Made `ContextManager` globally reusable agent-wide infrastructure. Removed any workload-specific coupling (imports/branches), `budget_for` now budgets solely on generic contexts (`ctx.model_name` + memory/project/workspace/grounding/tool) via `ContextBudgetManager.compute`, `StableState` carries `project_id` without budgeting branch, added required docstring phrase.

## Files Modified
- `novi/runtime/context_manager.py:1-152` — added module + class docstring phrase "Agent-wide, usable across conversation/coding/research/workspace/tools/memory/jobs", added `StableState.project_id: str = ""`, wired `checkpoint_stable()` to populate `project_id`, verified no `_CAPABILITY_TO_WORKLOAD`/`WORKLOADS` imports, no `workload` branching in `budget_for` (uses only `ctx.model_name` + generic contexts).
- `tests/test_context_manager_global.py` — created 1 test per brief Step 1.

## Test Summary
`pytest tests/test_context_manager_global.py::test_context_manager_global_not_workload_coupled -v` — **1 passed**
- loops intents conversation/research/coding/planning, builds `ExecutionContext` with mocked `analysis.intent.value`, asserts `cm.budget_for(ctx)` returns `context_window > 0` without raising, asserts `inspect.getsource(ContextManager.budget_for)` contains no workload-specific branch (`"workload" not in src.lower() or "general" not in src`).

Also verified `pytest tests/ -k context -v` — **174 selected** passed including `tests/test_context_manager.py` (7 tests), `tests/test_context_budget.py` (9 tests), `tests/test_context_manager_global.py` (1 test).

## Verification
- `python -m pytest tests/test_context_manager_global.py::test_context_manager_global_not_workload_coupled -v` → 1 passed
- `grep workload novi/runtime/context_manager.py` → no output (no coupling)
- `python -c "open(...).read() contains Agent-wide, usable across"` → True
- `python -c "inspect.getsource(budget_for) workload check"` → False, no WORKLOADS/CAPABILITY leak
- `git show --stat HEAD` → 2 files, 21 insertions

## Concerns / Follow-ups
- `ExecutionContext.workload` field retained (used by `Noviruntime._workload_for` + `ModelSelector`) — ContextManager intentionally does not read it. No migration needed.
- `StableState` kept in `context_manager.py` (38 lines < 50); per brief, move to dedicated file deferred to Task 3 if threshold exceeded. Re-export compat not needed yet.
- Budget still fixed `SYSTEM_PROMPT_EST`/`STABLE_STATE_EST` constants in `context_budget.py` — generic, not workload-specific; consistent with global infra goal.
- Frontend polish / Project/Workspace intact (untracked webui changes remain uncommitted, not part of this task).

## Global Constraints
- No workload-specific step budgets/mode branches introduced (file contains zero `workload` string).
- ContextManager globally usable across all agent workloads (tested with 4 intents).
- Never fabricates budget; delegates to model-aware `ContextBudgetManager`.

---

## Fix Report — 2026-08-29 review findings (High/Medium)

**Commit:** `fix: harden ContextManager global invariant tests`

**Findings addressed:**
1. `tests/test_context_manager_global.py:14` weak assertion `assert "workload" not in src.lower() or "general" not in src` — fixed to strict `assert "workload" not in src.lower()` plus `assert "WORKLOADS" not in src` and `assert "_CAPABILITY_TO_WORKLOAD" not in src`. No triviumtautology escape.
2. Budget invariance across intents — added `windows` collection loop + `assert len(set(windows)) == 1` ensuring identical `context_window` for conversation/research/coding/planning. Verifies no intent-branch budgeting.
3. `novi/runtime/context_manager.py:85-86` used `self.model_name` ignoring per-run `ctx.model_name` — fixed to `ctx.model_name or self.model_name` so per-run model wins, fallback to manager default. New test `test_context_manager_budget_per_run_model_divergence` covers both directions (mini→default and default→small) and empty-ctx fallback.
4. Coverage: retrieved aggregates + project_id wiring — new test `test_context_manager_retrieved_aggregates_and_project_id` asserts `BudgetBreakdown.retrieved_context == estimate_tokens(memory+project+workspace+grounding+extra)` and that two contexts differing only in `project_id` yield identical `context_window`/`available`/`retrieved_context`. Also verifies `checkpoint_stable()` correctly populates `StableState.project_id` without affecting budget.

**Files modified:**
- `novi/runtime/context_manager.py:85-86` — `self.model_name` → `ctx.model_name or self.model_name`
- `tests/test_context_manager_global.py:1-72` — rewrote with strict source checks, invariance, divergence, aggregate/project_id tests
- `novi/runtime/context_budget.py` — unchanged (verified no workload coupling)

**Verification:**
- `pytest tests/test_context_manager_global.py tests/test_context_budget.py -v` → 12 passed (3 global + 9 budget)
- `grep -i workload novi/runtime/context_manager.py` → no output
- Manual: `ContextManager(model_name="my-mini-model").budget_for(ctx with ctx.model_name="any-model")` → 8192 (ctx wins); empty ctx → 4096 (manager fallback)
- `inspect.getsource(ContextManager.budget_for)` contains no `workload`/`WORKLOADS`/`_CAPABILITY_TO_WORKLOAD`
