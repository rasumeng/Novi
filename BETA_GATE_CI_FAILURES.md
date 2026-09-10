# Beta Gate CI Failures — 2026-09-07

**Source:** `python scripts/verify_beta.py` on GitHub Actions (`windows-latest`, Python 3.11.9)
**Repo root:** `D:\a\Novi\Novi`
**Result:** **BETA GATE FAILED — 1 step failed: `pytest -q`**
**Counts:** 8 failed, 2165 passed, 23 warnings (77.18s)

> Other gate steps PASSED:
> - `npx tsc --noEmit` — PASS
> - `npm --prefix novi/webui run build` — PASS (vite 8.1.3, 2798 modules, chunks split)
> - Targeted 4-file gate — PASS (34/34: persistence, project linking, capability verification, discovery honest errors)

---

## 1. Failed Tests

All failures are in the retrieval layer — the adapter/wiring for web search → `EvidenceBundle` → `ctx.grounding_text`.

### 1.1 `tests/test_execution_context.py` — `TestExecutorEntryPoint` (3)

```
FAILED TestExecutorEntryPoint.test_execute_plan_web_only
  assert ctx.grounding_text == "test result about shindo life"
  assert '' == 'test result ...'

FAILED TestExecutorEntryPoint.test_execute_needs_grounding
  same

FAILED TestExecutorEntryPoint.test_execute_research_fallback
  same
```

**Test intent:** `RetrievalExecutor(debug_trace=True)` with a mocked `EvidenceCollector.collect` should populate `ctx.grounding_text` via web search. Tests patch `EvidenceCollector.collect` to return a sufficient bundle and expect `kinds` to contain `trace` + `thinking`.

**Actual:** `kinds` contains `trace`+`thinking` but `grounding_text` stays `''` — executor ran but did not wire collector result into context.

### 1.2 `tests/test_retrieval_web_adapter.py` — `TestExecuteSearchWebRouting` (5)

```
FAILED test_default_source_uses_adapter_owned_collector
  patch.object(EvidenceCollector, "collect", fake_collect)
  bundle = exe.execute_search("test query")
  assert bundle.merged_text == "fake merged summary test query"
  -> '' == 'fake ...'

FAILED test_injected_web_source_is_used
  collector = _FakeCollector([_sufficient_bundle(text="test query result")])
  exe = RetrievalExecutor(web_source=WebRetrievalSource(collector))
  bundle = exe.execute_search("test query")
  assert collector.calls == [("test query", 2)]
  -> [] == [('test query', 2)]

FAILED test_failed_transition_and_trace_event
  bundle = exe.execute_search("q", trace=trace)  # with FAILED quality
  assert bundle.error == "search api 400"
  -> 'Search not configured — set Brave API key or SearXNG URL in Settings → Connectors.' == 'search api 400'

FAILED test_empty_transition_and_trace_event
  assert bundle.quality == EMPTY
  -> FAILED == EMPTY
  EvidenceBundle(error='Search not configured — set Brave API key ...', quality=FAILED)

FAILED test_low_relevance_reformulation_retry_via_adapter
  collector.calls == [("what is the foo bar", 2), ("foo bar", 1)]
  -> [] == [...]
```

**Pattern:** Adapter-owned collector and injected `WebRetrievalSource` are never called — `collector.calls == []` — and every call falls through to a hard `FAILED` with `Search not configured` error, regardless of injected/faked collector. `execute_search` is ignoring its `web_source` seam.

---

## 2. Root Cause Hypothesis (not yet verified in code)

- `RetrievalExecutor.execute_search` / `execute` no longer routes through `self.web_source` / `EvidenceCollector`. Likely a refactor introduced a direct `WebSearchService` or `Search not configured` early-return that bypasses the injectable collector seam used by tests.
- The `Search not configured` string (`Settings → Connectors` Brave/SearXNG) indicates the production “no credentials” branch is taken unconditionally, even when a fake collector is injected — so the `if not configured: return FAILED` happens before `web_source.collect` is consulted.
- `execute_needs_grounding` / `web_only` tests patching `EvidenceCollector.collect` on the class no longer reaches the executor because the executor now owns a separate collector instance or has moved collection into `WebRetrievalSource` without patching that path.

**Evidence for hypothesis:**
- Both class-patch and instance-injection variants fail identically (`calls == []`).
- The `FAILED` with `Search not configured` appears even for the `EMPTY` case where the fake collector explicitly returns `EMPTY` — so fake collector never ran.
- CI environment has no Brave/SearXNG keys (expected) — but tests should not hit that branch when a fake source is injected.

**Not environment fluke:** Local run via `python -m pytest -q` on `6e6c939` was **2173 passed** (Windows Python 3.14.7, same `verify_beta.py` steps), while CI Python 3.11.9 reports 8 failures. Suggests local venv had stale cached config or different `search` settings that made the early-return pass locally, or tests were not isolated between runs (e.g., `stdout` capture hides `Search not configured` locally where it still passed due to fallback).

---

## 3. Impact

- **Gate blocks beta** — `verify_beta.py` exits 1 due to `pytest -q`. No tag/release should be cut.
- **No product defect proven yet** — failures are in test-only injected-source seams; product retrieval may still work with real Brave/SearXNG, but the test seam being broken means we have no coverage for grounding → prompt injection.
- **Regression corpus / capability / persistence gates are green** — only retrieval web adapter is red.

---

## 4. Reproduction

```bash
# CI exact
python -m pytest -q
# Expected: 8 failures as above
# Local (current main 6e6c939) may show 0 failures — run with clean env to reproduce:
python -m pytest tests/test_execution_context.py::TestExecutorEntryPoint -v
python -m pytest tests/test_retrieval_web_adapter.py -v
```

Check with clean config:

```bash
python -m pytest tests/test_retrieval_web_adapter.py::TestExecuteSearchWebRouting::test_injected_web_source_is_used -vv
```

---

## 5. Files Likely Involved (for next fix session)

- `novi/runtime/retrieval.py` — `RetrievalExecutor.execute` / `execute_search` routing
- `novi/runtime/retrieval_policy.py` — `RetrievalStrategy.WEB_ONLY` handling
- `novi/runtime/sources/web.py` or `novi/runtime/evidence.py` — `EvidenceCollector` / `WebRetrievalSource`
- `novi/search/service.py` — `WebSearchService` `Search not configured` early return
- Tests that define seam: `tests/test_execution_context.py:590+`, `tests/test_retrieval_web_adapter.py:1-140`

---

## 6. Recommended Next Steps (DO NOT IMPLEMENT NOW)

1. **Isolate seam:** Read `RetrievalExecutor.__init__` — verify `web_source` is stored and `execute_search` actually calls `self.web_source.collect` before checking `search.backend` config.
2. **Fix early return order:** Move `Search not configured` check after `if self.web_source is not None` injection check, or make injected source bypass config.
3. **Restore test parity:** Ensure both `patch.object(EvidenceCollector, "collect")` and `WebRetrievalSource(_FakeCollector)` paths reach the collector.
4. **Verify with CI-like config:** Run tests with `search.backend=""` to force the `not configured` branch and confirm injected fakes still win.
5. **Re-run gate:** `python scripts/verify_beta.py` should go `BETA GATE PASSED` with 2173 passed again.

---

## 7. Raw CI Output (truncated, full log attached above)

```
8 failed, 2165 passed, 23 warnings in 77.18s
FAILED tests/test_execution_context.py::TestExecutorEntryPoint::test_execute_plan_web_only
FAILED tests/test_execution_context.py::TestExecutorEntryPoint::test_execute_needs_grounding
FAILED tests/test_execution_context.py::TestExecutorEntryPoint::test_execute_research_fallback
FAILED tests/test_retrieval_web_adapter.py::TestExecuteSearchWebRouting::test_default_source_uses_adapter_owned_collector
FAILED tests/test_retrieval_web_adapter.py::TestExecuteSearchWebRouting::test_injected_web_source_is_used
FAILED tests/test_retrieval_web_adapter.py::TestExecuteSearchWebRouting::test_failed_transition_and_trace_event
FAILED tests/test_retrieval_web_adapter.py::TestExecuteSearchWebRouting::test_empty_transition_and_trace_event
FAILED tests/test_retrieval_web_adapter.py::TestExecuteSearchWebRouting::test_low_relevance_reformulation_retry_via_adapter
```

---

*Generated for Actions — no code changes made. Owner is working on loading screen; retrieval fix to resume after.*
