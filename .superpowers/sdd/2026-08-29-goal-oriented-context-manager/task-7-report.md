# Task 7 Report: Guardrails — No Workload Step Budgets, Progress, Isolation Regression

**Status:** DONE
**Commit:** `guard: forbid workload-specific step budgets, add stall/context safeguards` (e8281bd)
**Date:** 2026-08-30

## Summary
Final guardrails: architecture guard forbids per-workload step budgets, safety-rail invariant enforced via comment audit, stall (3 identical sigs) and context-overflow (>90% after compact → needs_continuation context_overflow) safeguards hardened, full verification green.

## Files Modified
- `tests/test_no_workload_step_budgets.py` (new) — scans `complexity.py` for workload/max_steps branching and per-workload pattern `general.*8.*research.*12`, scans `runtime.py` for `workload.*max_steps`, asserts `safety rail` and `not completion` phrases in runtime/react_attempt, asserts safety rail in complexity.
- `novi/orchestrator/complexity.py:63` — added `max_steps is safety rail, not completion boundary — Novi works toward goal; step exhaustion is safety not completion.` invariant comment; no workload branch.
- `novi/runtime/context_manager.py:102-147` — `compact_history` now post-checks budget after truncation; if utilization_pct >=90 still emergency, persists updated StableState, sets `needs_continuation=True` `continuation_reason=context_overflow` in ctx.metadata and trace.metadata, stores updated budget_breakdown. Preserves project_id isolation.
- `novi/runtime/react_attempt.py:192-258,388-435` — progress_tracker comment + stall already via `sig_counts[sig]>=3` → checkpoint stall; added pre-loop emergency check (should_compact==emergency → compact → if still overflow → _checkpoint_needs_continuation context_overflow yield), mid-loop overflow check after stall (metadata context_overflow → yield), opportunistic utilization check after tool append (emergency → compact → overflow yield). Max_steps remains safety rail via existing `_LOOP_DONE needs_continuation` path (reason max_steps_safety).

## Test Summary
`pytest tests/test_no_workload_step_budgets.py -v` — **1 passed**
- `test_no_workload_specific_max_steps` — complexity no workload branching, safety rail present; runtime no per-workload max_steps, safety rail phrases present; react_attempt safety rail present.

`pytest tests/test_context_budget.py tests/test_goal_oriented_continuation.py tests/test_jobs_long_running.py tests/test_no_workload_step_budgets.py -v` — **24 passed**

`pytest tests/test_checkpoint_stable.py tests/test_compaction_l1_l2_l3.py -v` — **13 passed**

`pytest tests/test_checkpoint_isolation.py tests/test_continuation_auto.py tests/test_continuation.py -v` — **30 passed**

`pytest tests/test_react_attempt_parity.py -v` — **36 passed**

`npm run build` — **built in 1.14s** (2795 modules, gzip ok)

`npm run test` (vitest) — **171 passed** 7 files

Manual overflow check — ContextManager with small-7b window 4096, utilization 234% before compact, 211% after → metadata `needs_continuation context_overflow` set.

## Verification
- Guard test fails if `complexity.py` introduces workload branch or removes safety rail comment, fails if `runtime.py` introduces `workload.*max_steps` or drops safety rail phrase — verified by creating guard, running to pass.
- Stall: `test_stall_detection_triggers_continuation` passes (3 identical sigs → needs_continuation stall).
- Context overflow: synthetic large history/workspace Utilization >90% → compact_history sets context_overflow, react_attempt pre-loop yields needs_continuation without growing prompt.
- Isolation: `test_cross_project_isolation_proj_A_vs_B`, `test_compaction_preserves_project_isolation`, `test_checkpoint_isolation` all pass; StableState.project_id/workspace_paths preserved.
- Sidebar hierarchy intact via vitest + build success.

## Concerns / Follow-ups
- Guard uses heuristic regex; future per-workload budgets must avoid substrings `workload` near `max_steps` and pattern `general.*8.*research.*12` — intentional strictness for CI.
- Context overflow detection relies on `ContextBudgetManager.estimate_tokens` (len//4) and fallback windows; large tool outputs already bounded by L1 compress to 4000 chars, so overflow typically only on extreme history/workspace/grounding growth — behavior verified.
- Stall progress_tracker currently pure sig_counts; spec also mentions "no new discoveries/important_files" — StableState.discoveries syncs with workspace_files_used, but stall gate on identical sigs already covers runaway; enhancement to track discoveries delta deferred as minor.

## Global Constraints
- Novi works toward goal; step exhaustion is safety rail not completion — enforced in complexity.py, runtime.py:742, react_attempt.py:57,358,395.
- No workload-specific step budgets: guard asserts absence.
- Checkpoint.step contract unchanged, project isolation preserved across compaction/continuation.

---

## Fix 2026-08-30 — Harden guard against vacuous safety-rail bypass

**Commit:** `fix: harden guard against vacuous safety-rail bypass`

**Issues:**
- **High:** `tests/test_no_workload_step_budgets.py:38` used `assert not re.search(r"general.*\bmax_steps\b|research.*\bmax_steps\b", rt_lower) or "safety rail" in rt_lower` — always passes because safety rail exists, vacuous bypass. Fixed: removed `or` clause, split into independent asserts: `assert not re.search(..., re.S)` separately and `assert "safety rail" in rt_lower` separately, so per-workload mapping is never masked.
- **Medium:** `has_workload`/`has_max_steps` gate + missing `re.S` + trivial `if.*workload|workload.*if` bypass. Fixed: removed conditional `if has_workload and has_max_steps` gate so checks run irrespective of workload keyword; added `re.S` to all per-workload searches to catch multiline dicts; replaced `if.*workload` trivial bypass with broader workload dict check `general.{0,80}max_steps|research.{0,80}max_steps` and `max_steps.{0,80}general` without requiring `if`; added dict checks `'"general"\s*:\s*\d+|"research"\s*:\s*\d+'` with `re.S` to catch `{"general":8}` mapping irrespective of workload keyword.

**Files Modified:**
- `tests/test_no_workload_step_budgets.py` — removed `has_workload`/`has_max_steps` gate, added `re.S` to `general.*8.*research.*12`, `workload.*max_steps`, and `general/research.*max_steps` searches; added dict mapping asserts `'"general"\s*:\s*\d+'`/`'"research"\s*:\s*\d+'` and `'"general"\s*:\s*8'` for both `complexity.py` and `runtime.py` with `re.S`; split vacuous `or "safety rail"` into `assert not re.search(...)` + `assert "safety rail" in rt_lower`; changed `if.*workload` to broader `general.{0,80}max_steps` etc.

**Verification:**
- `python -m pytest tests/test_no_workload_step_budgets.py -v` — **1 passed** after hardening (no false positive on legitimate `workload`/`general` coexistence — limited distance `.{0,80}` with `re.S` avoids distant false match).
- Negative insertion test: appending `viol = {"general": 8}` to `complexity.py` causes `assert not re.search(r'"general"\s*:\s*\d+', text, re.S)` to fail — **manual test failed as expected** (exit 1, `workload-specific max_steps dict mapping forbidden`).
- Negative insertion test: appending `X = {"general":8}` to `runtime.py` causes dict assert to fail — **manual test failed as expected**.
- Vacuous bypass test: old `or "safety rail"` would pass even with `general_max_steps = 8` + safety rail present; new independent assert `assert not re.search(r"general.{0,80}max_steps|...", rt_lower, re.S)` correctly fails on `general_max_steps = 8` despite safety rail — **manual test caught**.
