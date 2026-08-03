# Phase 9.5 — Unified Retrieval Policy Completion: Implementation Blueprint

**Status:** Planned (read-only architecture approved, not yet implemented).

**Source spec:** `PLAN.md` §5.5 (lines 680-815) deferred items + `docs/phase9-blueprint.md` §8 (steps deferred). This phase completes the Phase 9 deferred step 8 and the spec components `SourceSelector` / `ResultMerger`.

**Baseline:** Commit `1f84e9c` — Phase 9 complete. 546 tests passing.

**Naming:** Explicitly **NOT** `PLAN.md` §5.6 "Phase 10 — Planner & Long-running Tasks". That phase is untouched. This is **Phase 9.5**, the completion of the unified retrieval policy.

---

## 1. What Phase 9 Left Deferred

| Deferred item | Current state | Gap |
|---|---|---|
| `SourceSelector` | None — `RetrievalPolicy.resolve` is a hand-coded `if/elif` tree (`retrieval_policy.py:123-184`) | No pluggable selection strategies (`PLAN.md:776`) |
| `ResultMerger` | None — no multi-source merge/rank exists | No deterministic cross-source ranking (`PLAN.md:777`) |
| Cross-source ranking | Each adapter normalizes score to `[0,1]` (`sources/base.py:59`) but scales are incomparable (memory/knowledge `1.0-distance`, web `rerank` score, project none) | No merge-time normalization |
| `ContextAllocation` enforcement | `max_results` honored as `k` per adapter; `max_sources` sets metadata only (`retrieval_policy.py:211`); `max_context_chars` never enforced (advisory, `retrieval_budget.py:14`) | No char-budget gate at render |
| `EvidenceContext` feed | `ctx.evidence_context` exists but never set — observational only (`execution_context.py:87-90`) | Phase 7 contract not wired to runtime |
| Multi-source execution | Executor runs per-strategy branches (`retrieval.py:573-689`): web XOR knowledge, memory/project as separate `_setup_*` string paths | No unified plan → sources → merge pipeline |

**Retrieval outputs today (per run):** flat strings only — `ctx.grounding_text` (web bundle `merged_text` XOR knowledge text), `ctx.memory_context`, `ctx.project_context`, plus quality/error/plan/recovery/trace. No structured per-source results retained.

---

## 2. Final Architecture

### 2.1 Ownership contract (preserved from Phase 9)

| Component | Owns | Does NOT own |
|---|---|---|
| `RetrievalPolicy` | Decides source selection + strategy + allocation | I/O, ranking math, source impls |
| `SourceSelector` | Pluggable selection strategies (query class → sources) | Policy decision, execution |
| `RetrievalExecutor` | Orchestrates: plan → source execution → merge → render → EvidenceContext; recovery + coordinator lifecycle | Selection logic, ranking math |
| `RetrievalSource` adapters | Store access, query shaping, per-source relevance, source-typed errors | Selection, ranking, merging, budget decisions |
| `ResultMerger` | Deterministic cross-source rank + dedup + merge | I/O, rendering, decision |
| `ContextRenderer` | Merged result → prompt string; char-budget enforcement | Ranking |
| `EvidenceProcessor` | `EvidenceContext` (facts, conflicts, confidence, summary) | Retrieval, decision |

### 2.2 Data flow

```
RetrievalPolicy.resolve ──uses──> SourceSelector.strategies
        │
RetrievalPlan (sources, strategy, allocation)
        │
RetrievalExecutor.execute
        │  for each source in plan.sources:
        ▼
adapter.retrieve(query, allocation) ──> RetrievalResult (per source)
        │
        ▼
ResultMerger.merge(results, query, allocation) ──> MergedRetrievalResult
        │  rank (α·prior + β·(1−pos/k) + γ·overlap) + dedup
        │
        ├── ContextRenderer.render ──> grounding_text / memory_context /
        │        project_context (parity formatting, max_context_chars gate)
        │
        └── EvidenceProcessor.process_results ──> ctx.evidence_context
                     (facts / conflicts / confidence / summary)
```

### 2.3 New components

**`SourceSelector`** (`cozmo/runtime/source_selector.py`, pure)
- Pluggable strategy objects: `strategy_classify(signals, intent) -> list[SourceType]`.
- Existing policy tree (`retrieval_policy.py:123-184`) becomes the default strategy; strategy registry mirrors `evidence/ranking.py:19` `_SCORERS` pattern.
- Policy keeps decision ownership; delegates selection to the selected strategy. No runtime deps.

**`ResultMerger`** (`cozmo/runtime/result_merger.py`, pure)
- Input: `list[RetrievalResult]`, `query`, `ContextAllocation`. Output: `MergedRetrievalResult`.
- Deterministic given identical inputs.
- Cross-source normalization per item:

```
final = α·source_prior(source) + β·(1 − pos/k) + γ·overlap(query, item.text)
```

  - `source_prior`: memory > project > knowledge > web (mirrors `_SOURCE_TYPE_BOOST`, `evidence/ranking.py:22-30`); configurable.
  - `(1 − pos/k)`: within-source positional rank — scale-agnostic, fixes incomparable scores.
  - `overlap`: term overlap (reuse pattern at `retrieval.py:303-309`).
  - α+β+γ=1, weights configurable. Result clamped `[0,1]`.
  - Provenance recorded: `item.metadata["merge_norm"]`, original score, rank position — feeds Phase 8 eval.
- Dedup across sources: token-overlap threshold (mirror `_find_duplicate`, `retrieval_coordinator.py:99-126`). Later/higher-prior source wins the slot; duplicate still counted in attribution.

**`MergedRetrievalResult`** (`cozmo/runtime/sources/base.py` or `result_merger.py`)
```python
@dataclass(frozen=True)
class MergedRetrievalResult:
    query: str
    items: tuple[RetrievedItem, ...]          # cross-source ranked, attributed
    source_results: tuple[RetrievalResult, ...]
    quality: RetrievalQuality
    allocation_used: ContextAllocation
    metrics: dict                             # per-source contribution, dedup count, rank provenance
```
- Frozen; shared across subsystems like `EvidenceContext`.

**`ContextRenderer`** (`cozmo/runtime/result_merger.py` or `cozmo/runtime/render.py`, pure)
- Turns `MergedRetrievalResult` into prompt strings. **Source-aware formatting for byte-parity:**
  - web → the source's `merged_text` (from `EvidenceCollector.collect` via `WebRetrievalSource.collect`, `sources/web.py:30-45`) — byte-identical to current `ctx.grounding_text`.
  - knowledge → line format of `retrieve_knowledge` (`retrieval.py:373-390`).
  - memory → `_format_memory_context` format (`retrieval.py:531-545`).
  - project → single item text (`retrieval.py:568-569`).
- Merge = ranked per-source sections, deduplicated, **enforced under `max_context_chars`** (global char budget; lower-ranked sources/items truncated first). `max_results` per source already honored as `k` at adapter level.
- Produces `grounding_text` + `memory_context` + `project_context` as derived renderings (compat shims for `runtime.py:632-641`), plus a structured section list for the prompt when `evidence_context` is unavailable.

**`EvidenceProcessor.process_results`** (`cozmo/evidence/processor.py`, new entry point)
```python
def process_results(query: str, items: list[RetrievedItem], ...) -> EvidenceContext
```
- Maps `RetrievedItem` → `evidence.Source`: `source_type=item.source`, `authority=source_prior`, `relevance=merged score`. `Source.url` optional (`evidence/context.py:30`), fits memory/project.
- Fact extraction per item, conflict detection with source-kind attribution, confidence, compression — reusing `processor.py:74-115` stages.
- `fallback=True` when merged `quality` is weak/empty/failed or no facts (`processor.py:81-105`).
- `RetrievedItem` imported under `TYPE_CHECKING` only (preserves `evidence/` leaf status, mirrors `processor.py:18`).

### 2.4 Conflicts between sources

- **Reuse Phase 7 `Conflict` + `ConflictDetector`** (`evidence/context.py:38-45`, `evidence/conflicts.py`). No new conflict type.
- Retrieval layer carries **provenance only** (`RetrievedItem.source` already labels kind). `MergedRetrievalResult.metrics` records per-source-pair disagreement count (prompt awareness signal).
- EvidenceContext renders verdicts: `ConflictDetector` over merged facts with `sources=` carrying source-kind labels; resolution = higher-confidence (existing, `conflicts.py:78-84`) with a second tier "fresher web beats stale memory" for cross-source pairs.

---

## 3. Migration Order (each step keeps 546 green)

1. **Types + `ResultMerger` in isolation.** `MergedRetrievalResult`, normalization (α/β/γ), dedup. New `test_retrieval_merge.py`. No runtime wiring.
2. **`SourceSelector` strategies.** Refactor `retrieval_policy.py:123-184` tree into strategies. `test_retrieval_policy.py` outcomes unchanged.
3. **Executor generic plan execution** behind config flag. Replace per-strategy branches (`retrieval.py:573-689`) with uniform `for source in plan.sources → retrieve → merge → render`. **Byte-parity gates:** web-only ≡ `EvidenceCollector._merge`; knowledge-only ≡ `retrieve_knowledge`; memory ≡ `_format_memory_context`.
4. **Real `ContextAllocation` enforcement.** `max_context_chars` gate at `ContextRenderer`; `max_sources` enforced at plan→execution; verify `max_results` as `k`.
5. **EvidenceContext feed.** `EvidenceProcessor.process_results` → `ctx.evidence_context`; runtime prompt (`runtime.py:636-641`) prefers `evidence_context` when `fallback=False`, else rendered merged text.
6. **Eval gates** before/after steps 3-5 (Phase 8 `MetricSet` + evidence A/B `D_merged` mode).
7. **Doc sync.** Update `docs/phase9-blueprint.md` §8 status, `PLAN.md` §5.5 status. **Do not touch `PLAN.md` §5.6 (Phase 10 planner).**

---

## 4. Affected Files

**New:**
- `cozmo/runtime/source_selector.py`
- `cozmo/runtime/result_merger.py` (+ `MergedRetrievalResult`, `ContextRenderer`)
- `tests/test_retrieval_merge.py`
- `tests/test_source_selector.py`
- `docs/phase9.5-blueprint.md` (this file)

**Modified:**
- `cozmo/runtime/retrieval_policy.py` — delegate to `SourceSelector`
- `cozmo/runtime/retrieval.py` — generic multi-source execution, merge, render, EvidenceContext feed
- `cozmo/runtime/sources/base.py` — `MergedRetrievalResult` (or import from `result_merger`)
- `cozmo/runtime/sources/web.py` — expose `merged_text` via `metadata` on `RetrievalResult` for parity rendering (adapter owns pipeline, unchanged contract shape)
- `cozmo/runtime/execution_context.py` — `retrieval_result: MergedRetrievalResult | None`; `evidence_context` becomes populated
- `cozmo/evidence/processor.py` — `process_results` entry point
- `cozmo/runtime/runtime.py` — prompt consumes `evidence_context`, passes derived renderings
- `cozmo/evaluation/metrics.py` — merged-rank metrics (per-source contribution, dedup count)
- `cozmo/evaluation/evidence_ab.py` — `D_merged` mode
- `docs/phase9-blueprint.md` — §8 status update

**Wrapped, untouched:** `memory/manager.py`, `memory/knowledge_index.py`, `code_indexer.py`, `tools/search_pipeline.py`.

---

## 5. Test Strategy

- **Determinism:** `ResultMerger` pure function — identical inputs → identical output; fixed-item tests.
- **Parity (primary no-regression):** single-source plans byte-reproduce current formatting (web/knowledge/memory/project).
- **New unit:** normalization behavior (α/β/γ, clamping, provenance), dedup threshold, `max_context_chars` truncation order, `max_sources` enforcement, `process_results` fallback path, conflict source-kind attribution.
- **No network/backend:** all new tests mock adapters/stores.

---

## 6. Regression Gates

- **Full suite:** 546 existing tests green at every migration step.
- `test_retrieval_coordinator.py` — budget/dedup/seed_cache survive generic execution.
- `test_evidence_processing.py` — EvidenceContext frozen contract intact after `process_results`.
- `test_execution_context.py` — executor dispatch contract (`test_execute_plan_*`, 573-711) unchanged.
- `test_trace_boundary.py` — trace events unchanged.
- `test_retrieval_recovery.py`, `test_retrieval_phaseb.py` — recovery + memory/project context persistence.
- `test_grounding.py` — plan → grounding linkage.
- `test_regression.py` (55 e2e) — answer quality.
- **Eval gates:** Phase 8 `MetricSet` before/after (retrieval precision/recall, `grounding_accuracy`, answer completeness); evidence A/B `D_merged` vs `A_merged` via `compare_evidence_ab` with `REGRESSION_TOLERANCE=0.05` (`evidence_ab.py:31,328-366`).

---

## 7. Explicit Non-Goals

- `PLAN.md` §5.6 Phase 10 planner / long-running tasks.
- Phase 9 non-goals (`PLAN.md:795-813`): reflection, advanced consolidation, semantic memory evolution, importance learning, episodic/OKF redesign, knowledge graphs.
- Full-text file indexing — `FileRetrievalSource` stays NoOp.
- Project-aware retrieval semantics — project adapter stays thin.
- New source kinds / plugin framework.
- Mid-loop dynamic re-selection during ReAct — pre-loop deterministic merge only.
- Mutating `EvidenceContext` / `RankingConfig` frozen schema.
- Changes to `tools/search_pipeline.py` / `EvidenceCollector._search_multi`.

---

## 8. Risks & Mitigations

1. **Behavior shift** replacing per-strategy branches → `grounding_text` drift. Mitigate: byte-parity tests + config-flag rollout.
2. **Heterogeneous scoring distortion.** Mitigate: positional rank + priors + eval gate (`PLAN.md:782`).
3. **EvidenceContext coupling.** Memory/project lack URL/domain. Mitigate: `source_type=item.source`, optional `url`, `TYPE_CHECKING` import, frozen schema untouched.
4. **Dependency cycle** (`evidence` ↔ `runtime.sources`). Mitigate: Protocol/`TYPE_CHECKING` consumption only.
5. **Token blowup** from merged context. Mitigate: `max_context_chars` enforced at render before prompt switch.
6. **Web parity** — generic merge must not re-format the web bundle summary. Mitigate: web `merged_text` carried in `metadata`, renderer uses it verbatim.
7. **Mid-loop tools return strings**, not merged results — pre-loop scope; document gap.
8. **Phase numbering collision** — resolved: this phase is 9.5; planner stays Phase 10.

---

## 9. Success Criteria

- `ctx.retrieval_result` populated per run; `ctx.evidence_context` real (no longer observational).
- `max_sources` / `max_results` / `max_context_chars` enforced.
- Single-source plans byte-identical to Phase 9 outputs.
- Cross-source ranking deterministic and evaluable.
- Phase 8 eval shows no regression after steps 3-5.
- 546-test suite green at every step; new `test_retrieval_merge.py` / `test_source_selector.py` land.
- `PLAN.md` §5.6 untouched.
