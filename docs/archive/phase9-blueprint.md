# Phase 9 — Unified Retrieval Policy: Implementation Blueprint

**ARCHIVED:** Implemented (2026-08-01), then absorbed by the layered Brain (Phase E/F). Historical record — see `docs/architecture/brain-evolution.md`.

**Status:** Implemented (steps 1-7 landed; step 8 deferred). Adapter ownership boundaries closed by the hardening pass — executor no longer touches `EvidenceCollector` or the knowledge-index global; WebUI memory endpoints route through `MemoryRetrievalSource`.
**Source spec:** `PLAN.md` §5.5 (lines 680-815).
**Baseline:** Commit `dc70353` — Phase 7-8 + Pre-Phase 9 complete. 403 tests, ~5.4s, zero network/backend deps.
**Companion docs:** PLAN.md §5.4 (Pre-Phase 9 memory/knowledge correctness), §5.1 (Phase 8 evaluation), §5.2 (Phase 7 evidence).

---

## 1. Current Retrieval Paths

Three separate phases execute retrieval; only one goes through `RetrievalExecutor`.

### Phase A — Pre-loop grounding (`runtime.py:557` → `retrieval.py:172` `execute()`)

Dispatches on `ctx.analysis.retrieval_plan.strategy`:

| Strategy | Path | Location |
|---|---|---|
| `WEB_ONLY` | `execute_search` → `EvidenceCollector.collect` → `_search_multi` (SearXNG) | retrieval.py:227-251 |
| `KNOWLEDGE_ONLY` | `retrieve_knowledge` → global `get_knowledge_index()` | retrieval.py:253-276 |
| `KNOWLEDGE_THEN_WEB` | knowledge then web | retrieval.py:278-317 |
| Fallbacks (plan-independent) | `_execute_grounding_search` (grounding.needs_grounding), `_execute_direct_web` (research intent) | retrieval.py:319-360 |

Writes: `ctx.grounding_text`, `grounding_error`, `grounding_quality`, `retrieval_plan`, trace fields.

### Phase B — Prompt-build inline retrieval (`runtime.py:367-425` `_system_prompt`)

- **Memory:** `_query_memory()` (runtime.py:311-349) gated by `analysis.evidence.needs_memory`, intent type-filter (317-323), `_rank_memories` (350-366) — injected as "Relevant memory".
- **Project:** `self.project_index.query()` for coding/work (runtime.py:409-417) — injected as "Relevant project context".
- **Gap:** `ctx.memory_context` (execution_context.py:101) exists but is **never populated** — memory bypasses ctx entirely.

### Phase C — Mid-loop tool calls (`tool_executor.py:170`, coordinator gate at 200-208)

- Web: `web_search`, `web_search_pipeline`, `web_fetch`, `fetch_url`, `webfetch`
- Knowledge: `search_knowledge` (file_ops.py:152)
- Memory: `search_memory` (memory_ops.py:8)
- Coordinator: created runtime.py:560, budget set from plan strategy (561-571), `seed_cache` from grounding_text (572-573), recorded tool_executor.py:288-289.

**Write path** (out of scope): `_remember` → `memory.add_interaction` (runtime.py:977-985).

## 2. Retrieval Logic Leaking Outside RetrievalExecutor

| Leak | Location | Phase 9 action |
|---|---|---|
| Memory query + rank | `runtime.py:311-366` `_query_memory`/`_rank_memories` | Remove (PLAN.md:712, 775) |
| Project query | `runtime.py:409-417` | Move behind `ProjectRetrievalSource` |
| Coordinator lifecycle | `runtime.py:560-576` | Move into `RetrievalExecutor` |
| Recovery decisions | runtime.py:687-723 (pre-loop), 811-850 (mid-loop), 908-934 (post-tool) | Read executor state object; no behavior change |
| Knowledge access | retrieval.py:147-168 via module global | **Done** — executor injects `KnowledgeRetrievalSource`; runtime resolves the global at composition |
| Web access | retrieval.py:94 instantiates `EvidenceCollector` | **Done** — executor consumes `WebRetrievalSource` (which owns the collector); `collect()` bundle delegate preserves `execute_search` semantics |
| Store access from tools | file_ops.py:160, memory_ops.py:15 | Optional thin delegation to adapters |
| Stale tool ref | `search_web` in coordinator (retrieval_coordinator.py:20), category maps (tool_executor.py:44, runtime.py:68), WebUI | Cleanup; no such tool exists |

## 3. Naming Collision — Resolve First

Two Phase 9 spec names already exist with different meanings:

| Spec name | Current meaning | Codebase | Resolution |
|---|---|---|---|
| `RetrievalSource` | Protocol: `retrieve(query, budget) -> RetrievalResult` (PLAN.md:729) | **enum** of source kinds (retrieval_policy.py:23-27) | Rename enum → `SourceType` |
| `RetrievalBudget` | context budget: `max_sources/max_results/max_context_chars` (PLAN.md:753-757) | web-tool budget (retrieval_coordinator.py:36-54) | Keep web-tool budget on coordinator; introduce new `ContextAllocation` for context budget |

Rename `SourceType` touches: retrieval_policy.py, orchestrator.py, runtime.py:209, execution_context.py:203.

## 4. Component Responsibilities

### `RetrievalSource` (Protocol, new)
- Owns: store access, query → item shaping, per-source relevance, source-typed errors.
- Does **not** own: budget, cross-source ranking, merging, context allocation, decision to query.

```python
class RetrievalSource(Protocol):
    id: str  # "memory" | "knowledge" | "project" | "file" | "web"
    def retrieve(self, query: str, budget: ContextAllocation) -> RetrievalResult
```

### `RetrievalResult` (new)
```python
@dataclass
class RetrievedItem:
    id: str
    text: str
    source: str
    score: float            # normalized 0-1
    metadata: dict          # path/url/title/type/timestamp

@dataclass
class RetrievalResult:
    source: str
    items: list[RetrievedItem]
    score: float            # merged source-level score
    quality: RetrievalQuality   # reuse Phase 6.5 enum (SUFFICIENT/WEAK/EMPTY/FAILED)
    error: str | None
```
Design rule: `RetrievalResult` must be constructible by the cheapest/fastest source and consumable by `_system_prompt`, `EvidenceCollector`, and `EvidenceContext` alike (PLAN.md:781 normalized scores).

### `RetrievalPolicy` (extend existing, retrieval_policy.py)
Currently pure `resolve(needs_grounding, signal_types, signal_strengths, has_external, intent) -> RetrievalPlan` with 2 sources. Extend to own (PLAN.md:705, 758-761):
- Source selection (add Memory, Project, File)
- Strategy ordering (add `MEMORY_FIRST`, project-aware variants)
- Ranking + merging across sources — deterministic, evaluable (PLAN.md:777)
- Context allocation per source
- Stays pure: no I/O (PLAN.md:771). Couple via `SourceSelector` strategy objects, not source impls (PLAN.md:776).

### `RetrievalExecutor` (extend, retrieval.py)
- Execute `RetrievalPlan` by calling each selected `RetrievalSource.retrieve()`
- Own coordinator lifecycle (build, budget, seed_cache) — pulled from runtime.py:560-576
- Populate `ctx`: grounding_text, quality, per-source sections, `ctx.memory_context`, trace
- Own fallback/retry (already has reformulate at retrieval.py:130-137)
- Expose recovery signals (quality/attempt state) so runtime stops reading bare ctx internals

### `SourceSelector` (new) + `ResultMerger` (new)
Per spec components table (PLAN.md:736-737): pluggable selection strategies; deterministic multi-source merge/rank.

## 5. Source Adapters

| Source | Adapter | Maps onto | Notes |
|---|---|---|---|
| Memory | `MemoryRetrievalSource` | `MemoryManager.query` | Wrap existing `query(k, threshold, types)` as-is. **No memory redesign.** Removes `_query_memory`/`_rank_memories` |
| Knowledge | `KnowledgeRetrievalSource` | `KnowledgeIndex.search` (already used at retrieval.py:147) | Inject index; drop module-global call site |
| Project | `ProjectRetrievalSource` | `ProjectIndex.query` (runtime.py:409-417) | Thin wrapper; stay behind executor |
| File | `FileRetrievalSource` | **stub/NoOp** | Full-text indexing deferred (PLAN.md:812). Workspace tools (read/grep/glob) remain explicit non-semantic access |
| Web | `WebRetrievalSource` | `EvidenceCollector.collect` | Wraps existing web pipeline; preserves HTTP-400/quality semantics |

Constraint: no memory/OKF redesign, no advanced features (PLAN.md:795-813). Sources expose the contract; storage formats stay internal (PLAN.md:772).

## 6. Migration Order (lowest regression risk first)

Each step keeps the 403-test suite green.

1. **Rename** `RetrievalSource` enum → `SourceType`; introduce `RetrievalSource` Protocol + `RetrievalResult` types. No behavior change. ✅
2. **Extract coordinator lifecycle** from run_stream (runtime.py:560-576) into `RetrievalExecutor`. Same behavior, moved ownership. ✅
3. **Introduce adapters** (Memory/Knowledge/Project/Web) as pure wrappers over existing stores. Wire executor to prefer adapters; keep old paths as fallback behind config flag. ✅ — adapters are now the only path: `execute_search` and `retrieve_knowledge` route exclusively through injected sources.
4. **Port Phase B** (memory + project inline retrieval) into executor as additional plan sources. Populate `ctx.memory_context`. Delete `_query_memory`/`_rank_memories` only after port. ✅
5. **Extend `RetrievalPolicy`** with Memory/Project selection + strategy variants; add `ContextAllocation`; add deterministic merge/rank (`SourceSelector`, `ResultMerger`). ✅ (policy + `ContextAllocation` landed; `SourceSelector`/`ResultMerger` deferred — see §8)
6. **Route Phase C tools** through source contract where cheap; otherwise keep and ensure budget/dedup consistency. Remove stale `search_web` refs. ✅
7. **Recovery refactor** — runtime reads recovery signals from executor state object. ✅
8. **Feed `ctx.evidence_context`** (Phase 7 `EvidenceContext`) from merged results — makes the Phase 7 observational contract real (execution_context.py:86-89). ⏳ deferred (no multi-source merge yet)

Step 5 is where retrieval quality may shift → gate with Phase 8 eval before/after (PLAN.md:783, 793).

## 7. Regression Gates (existing tests)

**Ownership gates** (behavior must not move silently):
- test_execution_context.py: `test_execute_plan_web_only`, `test_execute_plan_knowledge_only`, `test_execute_plan_none`, `test_execute_plan_needs_grounding`, `test_execute_plan_no_grounding_needed`, `test_execute_plan_research_fallback`, `test_execute_plan_noop` (573-711) — executor dispatch contract
- test_execution_context.py: `test_full_research_pipeline_trace_ownership` (392) — full-path trace integrity
- test_execution_context.py: `test_ctx_memory_context_persists` (286), `test_ctx_to_dict_includes_memory_context` (371) — memory_context becomes real
- test_cognitive.py: `test_query_memory_empty`, `test_query_memory_returns_formatted`, `test_rank_memories_by_importance`, `test_memory_filtered_by_intent` (27-59) — ported memory behavior

**Pipeline gates:**
- test_retrieval_coordinator.py (all) — budget/dedup survive coordinator-lifecycle move + web-tool budget split
- test_evidence.py: `TestMerge`, `TestRankSources`, `TestEvidenceCollectorIntegration` — web pipeline unchanged behind adapter
- test_evidence_processing.py: `TestEvidenceProcessor` — `EvidenceContext` feed must not violate frozen contract
- test_search_pipeline.py — HTTP-400 + tuple contract preserved by web adapter
- test_trace_boundary.py — trace events unchanged when memory/project join retrieval
- test_memory_correctness.py (7) — memory storage untouched by wrapper
- test_grounding.py: `TestResolveGrounding` — plan → grounding linkage intact
- test_regression.py (55 cases) — end-to-end answer quality; primary no-regression gate
- **Phase 8 eval** (test_evaluation.py) — quality before/after (PLAN.md:793)

## 8. New Tests

- `test_retrieval_sources.py` — protocol compliance per adapter; adapter ↔ wrapped-function output parity (memory adapter ↔ `_query_memory` equivalence; web adapter ↔ `EvidenceCollector` bundle equivalence) ✅
- `test_retrieval_budget.py` — context allocation caps (`max_sources/max_results/max_context_chars`) + wiring evidence (policy plan allocation, executor trace, adapter `max_results`) ✅
- `test_retrieval_web_adapter.py` — adapter ownership boundaries: `WebRetrievalSource.collect` delegate, `execute_search` routing (quality transitions, trace events, reformulation retry), `retrieve_knowledge` formatting parity ✅
- `test_retrieval_merge.py` — deterministic multi-source merge/rank ⏳ deferred with `SourceSelector`/`ResultMerger` (step 8)
- No network/backend: all new tests mock sources/stores (matches Pre-Phase 9 consolidation)

## 9. Risks

1. **Naming collisions** (`RetrievalSource` enum vs Protocol, `RetrievalBudget` web vs context) — resolve via renames first; silent shadowing is the top footgun.
2. **Memory behavior shift** on porting `_query_memory` — adapter is a pure wrapper first, eval before/after, keep config-flag fallback through step 4.
3. **Heterogeneous scoring across sources** — normalized-score heuristics, iterate with Phase 8 eval (PLAN.md:781-782).
4. **Coordinator move breaks budget** — coordinator tests are the gate; keep web-tool budget semantics identical.
5. **ProjectIndex is heavyweight** (indexes cwd at init, services/context.py:133) — Project adapter must be lazy; never query in tests.
6. **Recovery logic entangled in runtime** — refactor reads-state-only; do not change recovery behavior in the same step as the coordinator move.

## 10. Affected Files

**Modified:**
- `cozmo/runtime/retrieval_policy.py` — enum→`SourceType`, Protocol, plan extension, budgets/merge/rank
- `cozmo/runtime/retrieval.py` — executor owns coordinator + multi-source execution + Phase B port
- `cozmo/runtime/retrieval_coordinator.py` — web-tool budget stays; context budget split out
- `cozmo/runtime/runtime.py` — delete `_query_memory`/`_rank_memories`, inline project query, coordinator build; recovery reads executor state
- `cozmo/runtime/execution_context.py` — `memory_context` populated; per-source results; naming updates
- `cozmo/services/context.py` — inject adapters/sources; remove direct memory/project wiring to prompt
- `cozmo/tools/web_search.py`, `file_ops.py`, `memory_ops.py` — optional thin delegation; remove stale `search_web` refs

**New:**
- `cozmo/runtime/sources/` — protocol, result, 5 adapters
- `cozmo/runtime/retrieval_budget.py` — `ContextAllocation`
- `docs/archive/phase9-blueprint.md` — this file

**Wrapped, not modified:** `cozmo/code_indexer.py`, `cozmo/memory/manager.py`, `cozmo/memory/knowledge_index.py`

## 11. Test Strategy

- **Step-gated:** every migration step lands with the existing suite green (403 tests, ~5.4s)
- **Adapter parity tests:** new adapters tested against exact wrapped functions
- **Quality:** Phase 8 eval (A/B: merged vs per-source) gates steps 5-8
- **No network/backend:** all new tests mock sources/stores
