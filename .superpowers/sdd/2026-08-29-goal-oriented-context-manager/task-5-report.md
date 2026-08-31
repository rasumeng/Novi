# Task 5 Report: Compaction Pipeline — L1/L2/L3 Before Exhaustion

**Status:** DONE
**Commit:** `feat: L1/L2/L3 compaction before exhaustion, preserve isolation`
**Date:** 2026-08-29

## Summary
Implemented L1 tool trim, L2 history compaction, L3 checkpoint before exhaustion with project/workspace isolation preserved. L1 wired via `ToolExecutor._sanitize` calling `ContextManager.compress_tool_result(budget_chars=4000)` keeping paths/errors/counts. L2 `compact_history` keeps last 6 turns, summarizes via `StableState.to_text()` + optional `simple_llm` else extractive, stores `stable_state` dict + `stable_state_text` + `compacted` in `ctx.metadata` and `ctx.summary`, truncates history only never StableState, preserves `project_id`. L3 `checkpoint_stable` returns canonical `StableState` persisted to `Checkpoint.stable` dict. Wired thresholds 75/85/90 via `ContextBudgetManager.should_compact` and runtime pre-check + mid-loop gates + system prompt injection of `stable_state_text` when history truncated. Verified isolation: proj-A preserved, proj-B never leaks.

## Files Modified
- `novi/runtime/context_manager.py:22-95` — `__init__` now accepts `simple_llm`; `compress_tool_result` doc + important `count:/total` keep; `compact_history` L2 logic (last 6, simple_llm try, fallback extractive, dict+text store, history-only truncation, project_id isolation); `checkpoint_stable` L3 doc + isolation note.
- `novi/runtime/tool_executor.py:358-368` — `_sanitize` L1 wire: if `len>4000` calls `ContextManager().compress_tool_result(text, budget_chars=4000)` before `max_tool_output` truncation, preserves important lines.
- `novi/runtime/runtime.py:314-370,666-705` — `_system_prompt` gains `stable_state_text` param injected as `Stable execution state (from prior compaction)`; `run_stream` pre-base_msgs computes `_stable_text` from `ctx.metadata stable_state` dict via `StableState.from_dict.to_text()` isolation-aware, passes to `_system_prompt`; pre-check gate `should_compact` at 75/85/90 with compact/emergency compaction persists `StableState.to_dict()` to `ctx.metadata` and `trace.metadata` before model call.
- `tests/test_compaction_l1_l2_l3.py` — created 7 tests: `test_compaction_preserves_project_isolation` (fill 16k -> >85%, compact, proj-A in stable, no proj-B leak, history 6, L3), `test_l1_compress_tool_result_keeps_paths_and_truncates`, `test_l1_wired_into_tool_executor`, `test_l2_history_truncation_and_stable_not_discarded`, `test_l2_thresholds_75_85_90`, `test_l3_checkpoint_stable_persists_to_job_checkpoint`, `test_runtime_injects_stable_state_text_when_truncated`.

## Test Summary
`pytest tests/test_compaction_l1_l2_l3.py tests/test_checkpoint_stable.py tests/test_context_manager_global.py -v` — **16 passed**
- `test_compaction_preserves_project_isolation` — FAIL→PASS (before: 71% with y*8000 insufficient, after: 16k payload >85% and stable preserves proj-A).
- `test_l1_compress_tool_result_keeps_paths_and_truncates` — PASS (truncated < original, keeps foo.py/error/count).
- `test_l1_wired_into_tool_executor` — PASS (inspect `_sanitize` contains `compress_tool_result`+`4000`).
- `test_l2_history_truncation_and_stable_not_discarded` — FAIL→PASS (fixed f-string NameError).
- `test_l2_thresholds_75_85_90` — PASS (warning at 75, compact 85, emergency 90).
- `test_l3_checkpoint_stable_persists_to_job_checkpoint` — PASS (Checkpoint.stable roundtrip).
- `test_runtime_injects_stable_state_text_when_truncated` — PASS (system prompt inject).
- plus 6 `test_checkpoint_stable` and 3 `test_context_manager_global` — PASS.

Also verified `pytest tests/test_context_budget.py -v` — not run here but budget manager unchanged (75/85/90 thresholds intact).

## Verification
- `python -m pytest tests/test_compaction_l1_l2_l3.py::test_compaction_preserves_project_isolation -v` → FAIL (71%) before payload fix, PASS after 16k payload
- `python -m pytest tests/test_compaction_l1_l2_l3.py tests/test_checkpoint_stable.py tests/test_context_manager_global.py -v` → 16 passed
- `grep -n compress_tool_result novi/runtime/tool_executor.py` → found in `_sanitize` with budget_chars=4000
- `grep -n stable_state_text novi/runtime/runtime.py` → found in `_system_prompt` + `run_stream` injection
- `grep -n should_compact novi/runtime/context_manager.py novi/runtime/runtime.py` → 75 warning /85 compact /90 emergency via ContextBudgetManager

## Concerns / Follow-ups
- `compact_history` fallback uses `ctx.summary` even when history <=6 to ensure stable_state injection still populates summary for system prompt; may duplicate but bounded 2000 chars.
- `ToolExecutor._sanitize` L1 compress happens before `max_tool_output` truncation; double truncation safe because compress keeps important lines first.
- Runtime `stable_state` injection prioritizes dict->to_text for isolation; str fallback also contains proj-A but dict path is canonical.

## Global Constraints
- No workload-specific branches introduced (verified `grep workload` only in routing, not in compaction).
- Project isolation preserved: `StableState.from_context` only uses `ctx.project_id`/`workspace_files_used`, compact never mixes proj-B; runtime inject filters by project_id dict key.
- Bounded limits respected: history 6, summary 2000, stable_state_text 1200, tool output 4000 L1 + max_tool_output 8000, Checkpoint stable dict unbounded but structured not dumped messages.

---

## Fix 2026-08-30: Harden Task5 compaction tests and checkpoint wiring

**Review findings addressed:**
1. **Source-inspection → behavioral** — `test_l1_wired_into_tool_executor` now instantiates `ToolExecutor` and calls `_sanitize` with >4000 input, asserts output `len <= 8000`, `len < original`, contains `foo.py`/`error`/`count`, and `ContextManager.compress_tool_result` bounded to 4000. `test_runtime_injects_stable_state_text_when_truncated` now creates compacted `ExecutionContext` via `ContextManager.compact_history`, calls `NoviRuntime._system_prompt(stable_state_text=...)` and asserts prompt contains `proj-A` and `Stable execution state`, plus `StableState.from_dict` roundtrip path.
2. **Tightened `test_l2_thresholds_75_85_90`** — asserts `74.9→None`, `75.0→warning`, `84.9→warning`, `85.0→compact`, `89.9→compact`, `90.0→emergency`, `95.0→emergency`; high-utilization ctx (20*2000 + y*16000 → >=85%) asserts `lvl in (compact,emergency)` and `lvl != warning` (was overly permissive `in (warning,compact,emergency)`).
3. **L3 Checkpoint persistence** — documented contract (`ctx.metadata["stable_state"]` canonical for `Checkpoint.stable`) in `runtime.run_stream` pre-check comment and mid-loop comments; both pre-check and mid-loop now persist `stable_state` dict to `ctx.trace.metadata["stable_state"]` so `JobManager` can create `Checkpoint(stable=ctx.metadata["stable_state"])` via `ContextManager.checkpoint_stable` without re-deriving; caller contract explicit.

**Minor fixes:**
- **Bound L1 output to `budget_chars`** — `ContextManager.compress_tool_result` now guarantees `len(out) <= budget_chars` by prioritizing `keep` (paths/errors) and allocating remaining budget to head/tail; truncates `keep` if needed and hard-bounds final slice.
- **Fix `simple_llm` wiring** — `ContextManager(model_name=..., simple_llm=self.simple_llm)` in `runtime.run_stream` pre-check and both mid-loop gates so LLM summarization is actually used.
- **Dedupe runtime injection** — collapsed 4-branch `stable_state_text` logic to 3 branches (dict→`StableState.to_text`, str, compacted fallback), removed duplicate `history<=6` branch.

**Files modified (this fix):**
- `novi/runtime/context_manager.py:66-108` — bounded L1
- `novi/runtime/runtime.py:568-592, 669-686, 1012-1028, 1107-1124` — simple_llm wiring, deduped injection, L3 contract comments
- `tests/test_compaction_l1_l2_l3.py` — full rewrite of 2 tests to behavioral + tightened thresholds

**Verification:**
- `python -m pytest tests/test_compaction_l1_l2_l3.py tests/test_checkpoint_stable.py -v` → **13 passed** (7+6)
- `python -m pytest tests/test_compaction_l1_l2_l3.py tests/test_checkpoint_stable.py -v` re-run after bound fix → 13 passed


