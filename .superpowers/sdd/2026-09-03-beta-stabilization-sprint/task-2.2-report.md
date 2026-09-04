# Task 2.2 — Search Honest State (disabled vs no results vs failure) — Report

**Status:** DONE

**Commits:**
- feat: honest search state not_configured vs failed vs empty (Task 2.2)

**Test summary:**
- `python -m pytest tests/test_search_honest_state.py -v` — 22 passed
  - classify grounding_status: not_configured (via NOT_CONFIGURED_MSG variants), failed, no_results, grounded
  - finalize_grounding sets ctx/trace grounding_status, grounding_error, search_error, grounding_searched correctly for each state
  - execute_search not_configured without network (returns NOT_CONFIGURED_MSG, quality FAILED)
  - execute_search failed via collector (error propagation)
  - execute_search no_results (EMPTY quality)
  - _execute_grounding_search emits honest status for not_configured (yields status NOT_CONFIGURED_MSG, grounding_text empty no fake), failed, no_results, grounded (no error status)
  - runtime system prompt: not_configured contains [Search disabled] + Brave/SearXNG guidance and not fake grounding; failed surfaced with detail; no_results distinct; grounded contains results
  - WebSearchService is_configured false when backend empty, test_connection not_configured
- `python -m pytest tests/test_capability_verification.py tests/test_search_pipeline.py tests/test_discovery_honest_errors.py tests/test_grounding.py -v` — 68 passed (regression)
- `npm --prefix novi/webui run build` — success (vite build 1.12s, 1592kB)

**What changed:**
- `novi/runtime/execution_context.py:101-104,237-245` — Added `grounding_status` (grounded|not_configured|no_results|failed) and `search_error` fields to `ExecutionContext`, serialized in `to_dict()`.
- `novi/runtime/trace.py:115-116,165-166` — Added `grounding_status` and `grounding_error` to `ExecutionTrace` and `to_dict()`.
- `novi/runtime/retrieval.py:35,396-431,840-888,944-956,958-989,1011-1037` — Wired honest state throughout retrieval: `_classify_grounding_status` + `_finalize_grounding` already present; refactored 4 web paths (`WEB_ONLY`, `KNOWLEDGE_THEN_WEB` escalated, `_execute_grounding_search`, `_execute_direct_web`) to call `_finalize_grounding`, set latency, and emit distinct `("status", msg)` + trace events per status (not_configured → NOT_CONFIGURED_MSG, failed → bundle.error, no_results → "No search results..."). Prevents fake grounding when disabled; failures surfaced as status trace. `execute_search` already returned NOT_CONFIGURED bundle without network.
- `novi/runtime/runtime.py:333-394,786-794` — Extended `_system_prompt` with `grounding_status`/`search_error`; distinct branches: grounding → primary source; not_configured → `[Search disabled] Search not configured — set Brave API key or SearXNG URL in Settings → Connectors.`; no_results → note no sources; failed → `Search failed: {detail}... Do NOT pretend`; legacy fallback. Call site passes `ctx.grounding_status`/`search_error`.
- `novi/webui/src/hooks/useNoviChat.ts:367-380` — Honest status UI: `thinking`/`status` case now distinguishes icons/status per message text (not_configured → Settings/error, failed → AlertTriangle/error, no_results → SearchX) so inline steps surf distinct empty states.
- `tests/test_search_honest_state.py` — New 22-test suite covering classify/finalize/execute_search/emits/prompt/service scopes.

**Concerns / deviations:**
- `search.backend=""` → `not_configured` handled without touching provider/network via `_is_search_configured` guard in `execute_search`; degenerate empty query still returns empty without error (unchanged).
- `no_results` now also emits a status ("No search results found...") for UI distinctness; spec said "UI shows distinct empty state per case" — this satisfies via status + trace.
- Frontend hook change is minimal (icon/status per text) — full empty-state components for search UI (SearchModal) remain post-2.2 scope; current inline steps already surface distinct messages.
- `novi/search/service.py` and `novi/webui_server.py` search test endpoint already honest (NOT_CONFIGURED state) — no change needed, verified via WebSearchService tests.

**Remaining / not done:**
- None for 2.2 scope. Phase 2 other tasks (2.3-2.5) untouched.

**Verification:**
- Honest disabled vs failure vs empty distinguished via grounding_status + searchError, status message guidance, system prompt [Search disabled] not fake grounding, failures surfaced as status trace.
