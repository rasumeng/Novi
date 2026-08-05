# Phase F — Cognitive Completion: Implementation Plan

**ARCHIVED:** All increments shipped as Brain V1 (2026-08-05). Historical implementation record — see `docs/architecture/phaseF-design.md` for the design and `docs/architecture/brain-evolution.md` for what landed.

**Status:** Plan (implemented). Pre-requisite: `docs/architecture/phaseF-design.md` (approved, with refinements).

**Direction:** Phase F is NOT AGI, research-grade cognition, or a parallel system. It finishes the brain Cozmo was created to become: a reliable personal assistant that understands what matters, maintains continuity, and improves over time.

**Principle:** extend, never parallel. Every step reuses existing stores, statuses, edges, events, and the resolver. No new storage. Append-only writes. Bounded reflection. Incremental, each step independently green.

**Baseline:** current suite = 740 passing tests.

---

## Increment Map

Read the increments in order; each gate is "full suite green + the new pure tests for this step."

| # | Increment | Core artifact |
|---|---|---|
| 1 | Memory Consolidation | dedup-across-corpus + corroboration wiring |
| 2 | Knowledge Evolution | confidence, conflict, temporal edges |
| 3 | Reflection Coordinator | bounded pure `reflection.py` guard on `reflect()` |
| 4 | Personal Context Projection | derived read-only projection |
| 5 | Retrieval Improvements | lexicographic tiering in resolver |
| 6 | Decay / Archive | staleness demotion + read-time reweight |
| 7 | Testing & Evaluation | inspection/correction surface + E2E evaluation |

---

## Step 1 — Memory Consolidation

**Goal:** Repeated observations become corroboration, not duplicates. A new claim matching the corpus advances the existing item; the corpus stays clean.

**Files affected:**
- `cozmo/brain/reasoning/verification.py` — reuse `corroboration()`; add a cross-corpus "find nearest verified/candidate item" helper (already token-based, pure).
- `cozmo/brain/types.py` — add `last_seen_at` field to `KnowledgeItem` (default `created_at`).
- `cozmo/brain/storage/vector_store.py` — column `last_seen_at` (nullable, backward-compatible add); populate on write; `_row`/`item_from_row` round-trip.
- `cozmo/brain/layers/knowledge.py` — `store_extracted()`: before adding, check corpus for a near-dup; if found, advance `last_seen_at` instead of inserting.
- `cozmo/brain/reasoning/extraction.py` — extend `_dedup` semantics note (corpus-level dedup now lives in the layer; keep batch dedup as-is).

**Existing components reused:** `KnowledgeExtractor` (batch), `verification.tokens`/`corroboration`, `_dedup`, `KnowledgeItem`, `VectorStore`.

**New code required:** a corpus-membership helper (nearest-claim search by token overlap) used by the layer; `last_seen_at` column plumbing (small, additive).

**Risks:**
- False-positive merge of two distinct claims → mitig: high token overlap threshold (reuse ≥0.5 ratio), conflicts route to edges not merges (§9.2).
- Schema change touches LanceDB table → additive nullable column, existing rows default on read; re-open path already validates model.
- Regresses extraction round-trip tests → keep `item_from_row` back-compat on `last_seen_at` missing.

**Tests required** (`tests/test_consolidation.py`, new):
- same claim twice → one stored item, `last_seen_at` advances, no sibling row.
- distinct claims with partial overlap → not merged.
- `store_extracted` dedup does not break `test_vector_store`/`test_brain` existing counts.
- `last_seen_at` survives `_row`/`item_from_row` round-trip and reopen.

---

## Step 2 — Knowledge Evolution

**Goal:** Confidence rises by proof; contradictions and corrections produce typed edges; temporal "current vs historical" is derivable.

**Files affected:**
- `cozmo/brain/reasoning/verification.py` — add explicit-confirmation strengthening (already has `is_confirm`); surface single-observation rule (one mention stays `CANDIDATE`).
- `cozmo/brain/reasoning/promotion.py` — reuse `decide`; wire `EdgeKind.CONFLICTS_WITH` in addition to `SUPERSEDES` (§9.2).
- `cozmo/brain/brain.py` — `_reflect_knowledge()` writes both edge kinds; emits `knowledge.promoted`.
- `cozmo/brain/events.py` — add `KNOWLEDGE_PROMOTED` + `KNOWLEDGE_PROMOTED` payload (follows existing pattern: canonical ids, emit after persistence).
- `cozmo/brain/storage/relationship_store.py` — already stores all `EdgeKind`; no change, only result-type use.
- `cozmo/brain/types.py` — (from Step 1) no additional change.

**Existing components reused:** `promotion.decide`, `verification.is_confirm`/`corroboration`, `RelationshipStore.add_many`, `EdgeKind`, existing event-bus emit helper.

**New code required:** a small conflict-resolution decision (pure, in `promotion` or `verification`): when promoting to `VERIFIED` and an existing `VERIFIED` item conflicts, produce `CONFLICTS_WITH` + `SUPERSEDES` edges + demote the old item. Deterministic, no LLM.

**Risks:**
- Silent overwrite regression → conflict always yields an edge; old stays `SUPERSEDED` (append-only).
- Confidence "only grows, never decays" bug → decay handled in Step 6; here only promotion upward.
- Event emission before persist → follow "emit after persistence" invariant; test ordering.

**Tests required** (`tests/test_evolution.py`, new):
- confirmation promotes to `VERIFIED`.
- single mention stays `CANDIDATE`.
- contradiction → new item `VERIFIED`, old `SUPERSEDED`, both edges written.
- user correction (signature phrase) demotes old, records correction as new verified.
- `knowledge.promoted` emitted only after durable write.

---

## Step 3 — Reflection Coordinator

**Goal:** A bounded, deterministic, pure coordinator sits in front of `reflect()` — budget, trigger gating, pure decisions, no background daemon.

**Files affected:**
- `cozmo/brain/reasoning/reflection.py` — **new**, thin, pure. Budget `N` items (default 200), oldest `last_seen_at` first. Calls the existing `corroboration`/`decide`/conflict logic. Returns a decision list + `ReflectionReport` extension.
- `cozmo/brain/brain.py` — `reflect()` delegates to the coordinator instead of the inline loop; applies status/edge mutations.
- `cozmo/brain/types.py` — extend `ReflectionReport` (add `decays`, `conflicts`, `touched_ids`).
- `cozmo/runtime/lessons.py` — do NOT touch (tool-lesson reflection is a separate, existing concern, explicitly not unified here).

**Existing components reused:** `Brain._reflect_knowledge` loop → becomes the decision source; `update_status`, `RelationshipStore`, `verification`, `promotion`.

**New code required:** the pure coordinator module; a cheap trigger predicate (scenario-completed / idle-with-pending / on-demand / confirm-burst) using `Scheduler` tick + a "pending candidates" count.

**Risks:**
- Unbounded scan cost → hard budget `N`, oldest-first ordering, deterministic.
- Surprise mid-conversation writes → triggers only (§8.2); idle gate requires no active conversation.
- Double-run/race with extraction → serialized through `Brain`; a "last run" watermark prevents re-processing.

**Tests required** (`tests/test_reflection.py`, new):
- budget respected (processing stops at N).
- deterministic order (oldest `last_seen_at` first).
- trigger gating: no pass when no pending candidates; pass on scenario-completion / confirm-burst.
- decision list applied by Brain; report counts accurate.
- no-write race: reflect with in-flight extraction buffer does not double-apply.

---

## Step 4 — Personal Context Projection

**Goal:** A derived, read-only projection answers "what does Cozmo know about me" from existing Identity tags + Scenarios + Projects. Never invents attributes; not a store.

**Files affected:**
- `cozmo/brain/projection.py` — **new**. Pure: takes identity-tagged items + active scenarios + project anchors, groups by category (preference/goal/skill/project/event/relationship), ranks by the §5 hierarchy (importance → confidence → scenario → recency-tiebreak).
- `cozmo/brain/brain.py` — add `project_context()` facade (read-only; returns projection dict).
- `cozmo/brain/layers/knowledge.py` — expose identity-tagged item read (already via `list_objects`).
- `cozmo/brain/layers/scenarios.py` + `ScenarioStore.list` — supply active scenarios (existing).

**Existing components reused:** `KnowledgeLayer.list_objects`, `ScenarioStore.list`, `_IDENTITY_TAGS`, `KnowledgeStatus`.

**New code required:** the pure grouping/ranking projection + a thin Brain facade. No persistence.

**Risks:**
- Projection presents inferred facts as truth → policy (§3): projection groups *stated* items only; confidence/status shown alongside; no synthesis of new attributes.
- Cost on hot read path → fully derived, D0 when unused (lazy facade); rank function is cheap/bucketed.
- Staleness → projection always recomputed from current store, never cached.

**Tests required** (`tests/test_projection.py`, new):
- groups items by category correctly.
- ranks by importance-first; a rare stable preference beats a recent low-importance item.
- excludes `SUPERSEDED`; marks inferred/decayed items transparently.
- never invents an attribute (empty output when nothing stated).

---

## Step 5 — Retrieval Improvements

**Goal:** Replace "most similar memory" with "important, verified, scenario-relevant knowledge." Lexicographic tiering, recency as tiebreak only.

**Files affected:**
- `cozmo/brain/reasoning/resolver.py` — `query_knowledge` post-score tiering (§5.2); keep sufficiency gate; `superseded` excluded unless requested.
- `cozmo/brain/layers/knowledge.py` — `query_scoped` returns `KnowledgeHit` carrying status/confidence/last_seen so the resolver can tier (already returns objects).
- `cozmo/brain/brain.py` — pass `last_seen_at`/status through `_default_resolver` wiring if needed.
- Back-compat: a flag to enable tiering (default preserved) so runtime behavior is unchanged until step 7 validates.

**Existing components reused:** `LayeredRetrievalResolver`, `KnowledgeHit`, sufficiency gate, `_dedup_text`.

**New code required:** a pure bucketing/tiering function (importance → confidence → scenario-neighborhood → recency-tiebreak). One constants location. No learned model.

**Risks:**
- Regression on existing recall tests (`tests/test_resolver.py`, `tests/test_brain.py`) → back-compat flag; tiering monotone within similarity band; run full suite as gate.
- Recency wrongly dominant → prioritize importance first (test asserts stable preference beats recent topic).
- `superseded` leakage into results → explicit exclusion + toggle.

**Tests required** (`tests/test_tiering.py`, new):
- a rare stable preference outranks a recently discussed temporary topic (the §5 example).
- verified > corroborated > candidate within equal importance.
- scenario-anchored knowledge beats out-of-scenario at equivalent tiebreak.
- recency only breaks equal-(importance, confidence) pairs.
- superseded excluded by default; included when flag set.
- full suite (esp. `test_resolver`, `test_brain`, `test_retrieval_*`) stays green.

---

## Step 6 — Decay / Archive

**Goal:** Forgetting = priority reduction + archival, never deletion. Stale, un-corroborated claims demote and drop from retrieval/projection — but remain queryable.

**Files affected:**
- `cozmo/brain/reasoning/reflection.py` — add decay decision: stale `VERIFIED`/`CORROBORATED` (no new `last_seen_at` within horizon) demote to `CANDIDATE`.
- `cozmo/brain/brain.py` — apply decay demotions in `reflect()`; `ScenarioStatus.COMPLETED/ARCHIVED` triggers a pass.
- `cozmo/brain/types.py` / `vector_store.py` — reuse `last_seen_at` (Step 1); `update_status` already supports demotion to `CANDIDATE`.
- `cozmo/brain/reasoning/resolver.py` — read-time recency reweight already in Step 5 tiering; add explicit "archive = exclude unless requested" for items demoted past a threshold.

**Existing components reused:** `update_status`, `ScenarioStatus`, `last_seen_at`, `Scheduler`, resolver tiering.

**New code required:** decay-horizon constant + predicate (pure); read-time archive filter toggle.

**Risks:**
- Reads as data loss → never `DELETE`; archived/superseded remain queryable (search flag).
- Over-aggressive demotion of stable-but-rare preferences → preference/goal/identity tags exempt from decay horizon (they are durable by policy, §7.2 rule 4).
- Constant tuning → single constant, configurable; no adaptive heuristic (future work).

**Tests required** (`tests/test_decay.py`, new):
- stale candidate demotes and drops from default retrieval; still returned when archive/search requested.
- identity/preference item does NOT decay.
- scenario-completed trigger runs a pass; confirmation burst re-confirms a pre-decay item.
- full store history preserved after any decay.

---

## Step 7 — Testing, Evaluation & Trust Surface

**Goal:** Verify the cognitive layer end-to-end, and give the user a memory inspection + correction surface (trust model, §4).

**Files affected:**
- `cozmo/brain/brain.py` — wire `project_context()` + an `inspect_memory()` facade + `correct_memory()` (user demote/supersede/archive → `update_status` + edges).
- `cozmo/tools/*` — add a memory inspection/correction tool (reuse existing tool pattern; **do not** add a global bypass store). Reads via Brain facade only.
- `tests/test_consolidation.py`/`test_evolution.py`/`test_reflection.py`/`test_projection.py`/`test_tiering.py`/`test_decay.py` — integrated acceptance cases (§14 of design doc).
- Optional `cozmo/evaluation/` — a small synthetic user-profile scenario evaluating precision of projection + retrieval after N repeated/contradictory observations (bounded, deterministic, no LLM dependency).

**Existing components reused:** Brain facade, events, `update_status`, edges, resolver, projection.

**New code required:** inspect/correct facades + the tool; the bounded evaluation harness. No new store.

**Risks:**
- Inspection/correction surface expands scope → keep to read-path + `update_status` + edges; no new persistence; correction outranks corroboration (§4.4).
- Evaluation harness becomes a research platform → keep synthetic, bounded, deterministic; gate is pass/fail on fixed assertions, not model tuning.
- Tool bypasses Brain (like deferred memory_ops globals) → route exclusively through `Brain` facade (Architecture Rule #1).

**Tests required:** the six step suites integrated + a final acceptance scenario + full-suite regression (740 baseline remains green).

---

## Cross-Cutting Constraints (all steps)

1. **No new storage.** Every step reuses `VectorStore`/`ScenarioStore`/`ConversationStore`/`RelationshipStore`. The only schema delta is the additive `last_seen_at` column (Step 1).
2. **Append-only.** Writes are status/edge mutations + verified inserts. No content overwrite, no `DELETE`.
3. **Bounded reflection.** Hard item budget, deterministic ordering, defined triggers, serialized through `Brain`. No background daemon, no surprise mid-conversation writes.
4. **Extend, don't parallel.** No parallel retrieval/reflect/store systems. `MemoryManager` brain=None fallback stays untouched.
5. **No AGI.** No self-improvement, no meta-learning, no autonomous goal-setting, no learned ranking.
6. **Pure reasoning tier.** All new decision logic lives in `cozmo/brain/reasoning/` (no storage imports); Brain/one layer applies the resulting mutations.
7. **Events follow the pattern.** "Emit after persistence, canonical ids only." `knowledge.promoted` / `identity.updated` reuse the existing event bus.

---

## Definition of Done (gates)

- Step 1–6 each: new pure tests pass **and** the existing 740-suite stays green.
- Step 7: all seven step suites + a synthetic acceptance scenario pass; inspect/correct surface works through the Brain facade.
- Final: `docs/phaseF-design.md` §14 acceptance items verifiable in tests (consolidation, contradiction, decay, importance>recency, projection, inspect/correct, append-only, green suite).
- No new storage systems, no AGI abstractions, no parallel retrieval/reflect paths introduced.