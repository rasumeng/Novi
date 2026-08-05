# Cozmo Brain V1 — Release Audit

**Auditor:** deepseek-v4-flash-free · **Date:** 2026-08-05
**Scope:** `cozmo/brain/`, runtime wiring, storage, docs, git state.
**Baseline:** working tree = 805 tests passing (~8.3s, no network deps).
**Mode:** findings + recommendations only. **No code changed.**

---

## Executive Summary

The Brain **structure is genuinely solid**: typed-column vector store (the
metadata-LIKE anti-pattern is gone), clean layer→storage direction, no circular
imports, append-only provenance edges, a real bounded reflection coordinator,
and a derived trust surface. Six of the nine Phase F increments landed and are
tested.

But the claim "Brain V1 is the durable cognitive foundation" is **not yet true**,
because three of the architecturally-defining capabilities are **wired but not
active in the production path**:

1. **Lexicographic retrieval tiering (`tiered=True`) is OFF by default** and
   `context.py` never turns it on → Phase F §5 retrieval intelligence is dead code.
2. **The layered resolver's `recall()` is never called by the runtime.** The live
   memory path goes `MemoryRetrievalSource → brain.retrieve_memory_rows` (a flat
   "temporary" compat adapter), so the designed top-down scenario-first retrieval
   never runs in production.
3. **`Brain.learn` (the "unified knowledge writer") is not connected.** The
   `write_knowledge` tool still writes markdown + refreshes the index directly,
   and `LessonStore` is a separate writer → Rule #6 is violated in two places and
   the stale-index gap Phase F was meant to close remains open.

These are **wiring gaps, not structural debt** — fixing them is cheap. Everything
else in this report is cleanup that can follow.

---

## Part 1 — Architecture Audit

Verdict: layer boundaries are clean; the codebase is small and reshaped
correctly. Pain points P1/P2/P3/P5/P7/P9/P10 (from `brain-architecture.md`) are
resolved. Remaining issues:

### 1.1 Duplicate tiering/durability constants and logic across modules — MED — FIX BEFORE V1

The same "importance bucket / confidence bucket / durable-tag set / last_used"
logic is reimplemented in three files:

| Symbol | Defined in | Duplicated in |
|---|---|---|
| `_IMPORTANCE_HIGH=0.66`, `_IMPORTANCE_MED=0.33` | `projection.py:34-35` | `tiering.py:23-24` (comment admits "mirror") |
| `bucket_importance` / `bucket_confidence` | `tiering.py:35-48` | `projection._importance_tier`/`_confidence_tier` `projection.py:46-59` |
| `_DURABLE_TAGS` | `reflection.py:29` | `tiering.py:28` |
| `_last_used` (last_seen or created) | both `projection.py:62` & `tiering.py:51` & `reflection.py:74` |
| identity tags | `brain._IDENTITY_TAGS` `brain.py:72` | `reflection._DURABLE_TAGS`, `projection._CATEGORY_TAGS` |

**Why it matters:** these must stay in lockstep. A change to one bucket boundary
or durable-tag set silently diverges retrieval, projection, and reflection from
each other. Four modules currently disagree on where "identity tags" live.
**Fix:** one shared constants/helpers home (e.g. `brain/reasoning/ranking.py`) and
delete the per-module mirrors. **Severity MED, before V1** (behavior is currently
consistent, but it is a maintenance trap on the most behavioral-critical path).

### 1.2 `tiered_resolver` flag is dormant — HIGH — FIX BEFORE V1

`Brain.__init__(tiered_resolver: bool = False)` (`brain.py:117,130`),
`context.py:169-179` wires the Brain **without** passing it → `False`.
The resolver's `_tier()` (`resolver.py:185-194`) is a no-op under that flag, so
`tier_hits()` (§5 importance→confidence→scenario→recency) never runs in
production. **Design gap:** Phase F §5 "Retrieval Improvements" / plan increment 5
was specified as active behavior ("resolve...(query) → tier(importance)..."). It
is currently dead code guarded by an off switch.
**Fix:** wire `tiered_resolver=True` in `context.py` (or remove the flag and make
tiering the default). Keep the door closed until the resolver-recall path (1.3)
is live, since tiering currently has no real consumer anyway.

### 1.3 Layered resolver `recall()` is not the production read path — HIGH — FIX BEFORE V1

`LayeredRetrievalResolver.recall` (`resolver.py:87`) implements the designed
top-down walk (scenario→scoped knowledge→global→conversation, sufficiency gate).
But nothing calls it through the runtime: there is **no `brain.recall` call in
`cozmo/runtime/`** (grep confirms one `retrieve_memory_rows` hit, none via the
resolver). The runtime's `MemoryRetrievalSource` goes
`brain.retrieve_memory_rows` → `recall` → resolver *only if a resolver is set*,
and even then the source consumes **flat rows**, not recall structure. In
practice memory retrieval is still flat `MemoryManager.query` behind a compat
shim.
**Why it matters:** the entire Phase E cognitive payoff (scenario-anchored,
sufficiency-gated, relationship-constrained retrieval) is unreachable from the
assistant's real run. Tests exercise the resolver; production ignores it.
**Fix:** point runtime memory retrieval at `Brain.recall`/resolver and consume
`RecallResult`, deleting `retrieve_memory_rows` (or keep it only as the legacy
no-brain fallback). Defer the hard part of this to a hardening phase if needed,
but it must not ship as the silent default path.

### 1.4 `Brain.learn` / "unified knowledge writer" not wired — HIGH — FIX BEFORE V1

Design Phase F: "Brain.learn unifies write_knowledge + LessonStore — single
knowledge writer, no stale index gap." Reality: `write_knowledge`
(`tools/file_ops.py:188-226`) writes the MD file and calls
`knowledge_index.index_file` directly, bypassing `Brain.learn` (`brain.py:280`);
`LessonStore` (`runtime/lessons.py`) is a separate writer; `Brain.learn` has no
caller. **Rule #6 violated at two write sites.** The stale-index gap that
`write_knowledge` ↔ index refresh should have consolidated is still split across
markdown + a manual index refresh.
**Fix:** route `write_knowledge` through `Brain.learn` (and, at minimum, document
LessonStore as a deliberate separate concern or fold it into `learn`).

### 1.5 Dead storage code: `migrations.py` — MED — clean up before/just-after V1

`storage/migrations.py` is a one-time offline Phase C→D migration: it reads the
legacy flat `cozmo_knowledge` LanceDB table via the *old* `LanceStore`
(`memory/lancedb_store.py`) and re-embeds into the typed store. **It is obsolete
legacy adapter code** with no runtime dependency.
**Recommendation:** move to `cozmo/tools/` as a documented manual utility, or
archive; do not ship it as a live `storage/` module. Defer 1 release or handle now
(item is trivial).

### 1.6 Dead source adapters — LOW/MED — clean up

`runtime/sources/identity.py`, `runtime/sources/scenario.py`, and the
`file.py` NoOp stub are exported (`sources/__init__.py`) but **never instantiated
or selected** → unreachable. `ScenarioRetrievalSource` / `IdentityRetrievalSource`
are the design's Phase E additions; they have no live caller.
**Fix:** either wire them (they're the natural home of 1.2/1.3's layered path) or
delete until Phase E is actually exercised. Defer but flag.

### 1.7 Duplicated SQLite bootstrap across scalar stores — MED — defer

`conversation_store.py:51-60`, `scenario_store.py:43-52`,
`relationship_store.py:37-46` each repeat `sqlite3.connect + check_same_thread
\= False + row_factory=Row + WAL + RLock + mkdir + executescript + close`, and the
safe `json.loads(...)→()` list loader is repeated (`conversation_store.py:143`,
`scenario_store.py:137`). The `storage/base.py` protocols exist but offer no
shared SQLite base.
**Fix:** a small shared `_sqlite.py` connection/row-loader helper. **Defer** (pure
DRY, no behavior change, medium risk of churn).

### 1.8 `Brain` facade is large & multi-responsibility — LOW — watch

`brain.py` = 701 lines and now owns observe/recall/learn/resolve/reflect/project/
inspect/correct/retrieve_knowledge/retrieve_project + 3 emit helpers + 2 private
resolver builders. It is drifting back toward the god-object shape P1 was meant
to kill. **Recommendation:** this is acceptable at the *facade* boundary by
design ("cognition API"), but watch it; if it grows past ~900 lines split trust
surface (`inspect`/`correct`) into a small `BrainTrust` or move to `projection.py`.
**Defer**, no action now.

### 1.9 Two competing process-global memory mechanisms (P8 persists) — LOW/MED — defer to Phase G

`context.py` registers **both** `set_memory_manager` (`.memory`, always built,
`context.py:128`) *and* `set_brain` (`context.py:180`). Legacy
`MemoryManager` + the flat pipeline remain live as the brain=None fallback, and
`memory_ops.search_memory` (`tools/memory_ops.py:26`) still calls
`get_memory_manager()` directly — a Rule #6 read bypass even when the Brain is set.
**Fix:** Phase G removes the flat fallback/global; for V1 at least stop
`memory_ops.search_memory` from bypassing the Brain. Defer removal, fix the bypass
soon.

### 1.10 `retrieve_memory_rows` is mislabeled "temporary" — LOW — fix soon

`brain.py:189` calls it a "Temporary compat adapter," but it is **the live memory
path** (only caller used by the runtime source). Mislabeled → future dev may
"helpfully delete" it. **Fix:** rename (e.g. `recall_flat`) or migrate runtime to
`RecallResult`. Do with 1.3.

### 1.11 Consistent naming — LOW

- `KnowledgeLayer.list_items` (dicts) vs `list_objects` (objects) — same thing,
  two names (`knowledge.py:105,109`). Pick one.
- Overloaded "project": `project()` function (`projection.py:83`), `project_context()`
  method (`brain.py:306`), `Project` context object, `project` tag, `ProjectIndex`.
  Distinct concepts sharing one word; acceptable but document.
- `correct_memory("demote"→CORROBORATED, "archive"→CANDIDATE)` maps names that
  don't obviously match the enum ladder (`brain.py:431-437`). Fine, but confirm
  the labels match user intent.

---

## Part 2 — Cognitive Audit (implementation vs design)

Implemented faithfully: **observation pipeline** (persist raw turns → emit
`ConversationObserved`), **knowledge extraction** (pure `extraction.py`, dedup,
heuristic/LLM hooks), **consolidation** (corroboration + cross-corpus dedup in
`KnowledgeLayer.store_extracted`), **bounded reflection** (`reflection.py` budget
+ oldest-first + trigger gating), **confidence evolution** (promotion with
`_VERIFY_CORROBORATIONS=3` / confirmation override), **trust model**
(`inspect_memory`, `correct_memory`, append-only supersede), **personal context
projection** (derived `project()`), and **conflict/supersede edges** that preserve
history. Six of the nine DoD items from phaseF §14 are met. Gaps:

- **C1 (HIGH) — Retrieval reweighting not active.** phaseF §5 (importance→
  confidence→scenario→recency tiering) exists as `tier_hits` but is gated off
  (see 1.2). DoD #4 ("a rarely-mentioned stable preference outranks a recently
  discussed topic") and #7 ("Brain.recall returns important, verified,
  scenario-appropriate knowledge by default") are **not satisfied in production**.
- **C2 (HIGH) — Layered top-down recall not on the runtime path** (see 1.3).
  Responsibility C2/cognitive "scenario-anchored retrieval" is only hit by tests.
- **C3 (HIGH) — Unified knowledge writer not delivered** (see 1.4). Phase F §6.2
  "merge-before-promote" + "conflict detection" landed, but the writer unification
  that removes the stale-index gap did not.
- **C4 (MED) — Reflection triggers are dormant.** `should_reflect` supports
  `scenario_completed/confirm_burst/idle_pending`, but `Brain.reflect()` defaults
  `on_demand=True` and **nothing in runtime/scheduler passes the other triggers**.
  So reflection effectively only runs on manual invocation; the §8.2 "idle after
  activity via Scheduler" and "scenario completion" triggers are unrealized.
  Acceptable for V1 if documented; the app cannot yet silently maintain itself.
- **C5 (MED) — Scenario lifecycle is not advanced.** `ScenarioLayer` always sets
  `ACTIVE` and never sets `completed_at`; no completion detection exists
  (`scenarios.py:48`). Design's lifecycle field is present but inert. Defer — but
  note it means decay/reflection "scenario completion" trigger can never fire.
- **C6 (LOW) — Single-observation rule honored, but extraction cadence is legacy.**
  `extract_every=5` buffered batch (`brain.py:116`) matches legacy; fine.
- **C7 (LOW/GOOD) — "No silent synthesis."** The projection only groups *stated*
  items and never invents attributes; verified by reading `projection.py`. Correct
  vs design §3.2.

---

## Part 3 — Code Quality Cleanup List

| # | Item | Location | Action |
|---|---|---|---|
| 1 | Obsolete one-time migration adapter | `brain/storage/migrations.py` | archive → `tools/` or delete |
| 2 | Dead source adapters (identity/scenario/file NoOp) | `runtime/sources/{identity,scenario,file}.py` | wire or delete |
| 3 | Duplicate tier/durable-tag/importance constants+helpers | `projection.py`, `tiering.py`, `reflection.py`, `brain.py:72` | consolidate to one module |
| 4 | Off-by-default tiering flag (`tiered_resolver=False`) | `brain.py:117` + `context.py:169` | enable or remove flag |
| 5 | Mislabeled "temporary" compat adapter (live path) | `brain.py:189 retrieve_memory_rows` | rename or replace |
| 6 | Duplicate SQLite bootstrap / safe-json loader ×3 stores | `storage/{conversation,scenario,relationship}_store.py` | shared helper (defer) |
| 7 | `memory_ops.search_memory` bypasses Brain | `tools/memory_ops.py:26` | route through Brain |
| 8 | `write_knowledge` bypasses `Brain.learn` | `tools/file_ops.py:188-226` | route through `learn` |
| 9 | Reflection trigger params unconsumed at call site | `brain.py:331-337` | document or wire scheduler |
| 10 | `list_items` vs `list_objects` naming | `layers/knowledge.py:105` | align |
| 11 | Legacy flat `MemoryManager` global + fallback, still registered | `services/context.py:128`, `memory/manager.py:33` | Phase G |
| 12 | `LayerClassifier`'s LLM hook unused in context wiring | `context.py:175` (no classifier passed) | confirm intended |

No `TODO`/`FIXME` comments remain in `cozmo/brain/` (grep clean); the only stray
`# TODO` hits are a *feature* of the diagnostics tool, not debt.

---

## Part 4 — Repository Audit

State: `main` is **12 commits ahead of origin**. Latest brain series is committed
(`569b616`→`8b5dbd5` = D/E/F write+layered-retrieval+identity). Working tree =
26 modified + untracked (4 source, 2 docs, 7 test files) = the tail of Phase F
(consolidation, reflection, projection, tiering, trust surface). All 805 tests
green.

**Proposed commit boundaries (3 commits):**

- **Commit 1 — `brain: Phase F cognitive completion (consolidation, reflection, projection, tiering, trust)`**
  `brain/brain.py`, `brain/events.py`, `brain/types.py`, `brain/projection.py` (new),
  `brain/reasoning/reflection.py` (new), `brain/reasoning/tiering.py` (new),
  `brain/reasoning/promotion.py`, `brain/reasoning/verification.py`,
  `brain/reasoning/resolver.py`, `brain/reasoning/extraction.py`,
  `brain/layers/knowledge.py`, `brain/storage/base.py`, `brain/storage/vector_store.py`,
  `brain/__init__.py`; tests `test_reflection.py`,`test_decay.py`,`test_consolidation.py`,
  `test_evolution.py`,`test_projection.py`,`test_tiering.py`,`test_acceptance.py`,
  `test_brain.py`,`test_resolver.py`,`test_architecture.py`.

- **Commit 2 — `runtime: route retrieval sources through the Brain + memory inspection tool`**
  `runtime/retrieval.py`, `runtime/runtime.py`, `runtime/sources/{__init__,knowledge,memory,project}.py`,
  `services/context.py`, `memory/manager.py`, `tools/__init__.py`,
  `tools/memory_inspection.py` (new); tests `test_memory_query_merge.py`,
  `test_retrieval_sources.py`, `test_retrieval_phaseb.py`.

- **Commit 3 — `docs: Phase F design + implementation plan`**
  `docs/phaseF-design.md` (new), `docs/phaseF-plan.md` (new).

**Per-file disposition:**

- **Commit with Brain V1:** all files in C1 + C2 (they form the tested Phase F
  tail — commit together, not piecemeal, so the green suite is one logical unit).
- **Commit separately:** C3 docs (or fold into C1; docs-only is cleaner).
- **Delete:** `cozmo/brain/storage/migrations.py` (obsolete adapter); dead
  `runtime/sources/{identity,scenario,file}.py` **only if** not wired by the
  resolver-recall fix; neither is required by the 805-test suite.
- **Keep uncommitted:** nothing should ship uncommitted to a stable V1 branch.
- **Move to future phase (Phase G / hardening):** `MemoryManager` removal &
  brain=None fallback (`manager.py`, `context.py:128`), flat `query()` legacy path,
  engine legacy, dead adapters if kept, scheduler-driven decay triggers.

---

## Part 5 — Documentation Audit

Current `docs/`: 6 files — 1 durable design + 5 phase plans.

| File | Assessment | Recommendation |
|---|---|---|
| `brain-architecture.md` | **Durable architecture doc** — the design reference for the Brain | keep, move to `docs/architecture/` |
| `phaseF-design.md` | Durable design + DoD for the cognitive layer | keep in `docs/architecture/` |
| `phaseF-plan.md` | Implementation plan, work now done | **`docs/archive/`** (historical) |
| `phase9-blueprint.md` | Implemented, superseded by E/F | **`docs/archive/`** (historical) |
| `phase9.5-blueprint.md` | Planned→superseded by E/F | **`docs/archive/`** (historical) |
| `phaseC-blueprint.md` | Implemented (Phase C shipped) | **`docs/archive/`** (historical) |

**Proposed structure:**
```
docs/
  README.md            (new: index/entry point explaining the tiers below)
  architecture/        (durable specs, never archived)
    brain-architecture.md
    phaseF-design.md
  plans/               (optionally keep current-phase plan; else archive)
  archive/             (all phase9*, phaseC*, completed-phase plans)
```
Add `docs/README.md` so a new contributor reads: architecture → phaseF-design →
(optional) implementations, and never stale plans. Move AUDIT docs
(`AUDIT*`) out of repo root or into `docs/audits/` to keep the root clean.

---

## Part 6 — Devlog Entry (draft, Phase F)

Add to `DEVLOG.md`:

---
## Phase F — Cognitive Completion (Brain V1)

### Context
Phases D/E landed typed columns, provenance edges, and layered retrieval. The
Brain could organize and retrieve knowledge but could not reason about it.
Phase F closes the cognition loop: what stays true, what changed, what matters,
what should fade. Design: `docs/phaseF-design.md`; plan increment map followed.

### What Phase F delivered
- **Memory consolidation** — `verification.corroboration` + `find_near_duplicate`;
  `KnowledgeLayer.store_extracted` corroborates repeats across the corpus instead
  of inserting sibling rows (dedup-across-corpus).
- **Knowledge evolution** — `promotion.decide` drives candidate→corroborated→
  verified; supersede-with-history (`supersedes`/`conflicts_with` typed edges);
  explicit-confirmation detection (`is_confirm`) promotes instantly.
- **Bounded reflection coordinator** — `reasoning/reflection.py`: pure, budgeted
  (`N=200`, oldest-first), trigger-gated (`should_reflect`), decay horizon 90d;
  `Brain.reflect()` applies outcomes + emits `knowledge.promoted`.
- **Personal-context projection** — `projection.py`: derived, read-only grouping
  of identity-tagged items, ranked importance→confidence→scenario→recency; never
  invents attributes.
- **Retrieval tiering** — `reasoning/tiering.py`: lexicographic reorder behind a
  `tiered` flag (see Known Gaps).
- **Trust surface** — `Brain.inspect_memory` / `correct_memory` (append-only),
  exposed via `tools/memory_inspection.py`; decay is demotion, never delete.
- **Events** — added `knowledge.promoted`; extraction/promotion emit after
  durable persistence with canonical ids.

### Architectural changes
- `KnowledgeItem` gained `last_seen_at`, `importance` (default-safe); `status`
  ladder is the single lifecycle. Append-only invariant respected — no in-place
  mutation, change always a `supersedes` edge.

### Test count changes
Suite grew from 740 (phaseF baseline) to **805 passing**, spanning
`test_{reflection,decay,consolidation,evolution,projection,tiering,acceptance}` +
expanded `test_brain`, `test_architecture`, `test_resolver`. ~8.3s, no network.

### Major design decisions
- Forgetting = decay/archive on the read path + occasional consolidation demotion,
  **never** delete; durable (identity-tagged / verified) items exempt.
- Reflection is bounded + synchronous + serialized through the Brain (no daemon,
  no surprise background writes).
- Trust: corrections outrank corroboration; user correction writes are append-only.

### Remaining future work
- Enable retrieval tiering on the production path (currently `tiered=False`).
- Route the runtime's live memory retrieval through the layered `recall()`
  instead of the flat compat adapter.
- Wire the unified writer: `write_knowledge` → `Brain.learn`; decide LessonStore.
- Scenario lifecycle progression (completion) + scheduler-driven decay triggers.
- Phase G legacy removal: flat `MemoryManager`/`brain=None` fallback, dead
  source adapters, obsolete migration adapter.

---

## Part 7 — Final Verdict

1. **Is the Brain production-ready for Cozmo V1?** **Not yet.** Structurally
   sound and well-tested, but its two most expensive cognitive capabilities
   (lexicographic retrieval tiering and layered top-down recall) are not on the
   production path, and the unified knowledge writer is disconnected. Shipping as
   the "permanent foundation" today would silently deliver flat, un-tiered
   retrieval.

2. **Biggest remaining weaknesses:**
   - Retrieval tiering disabled by a dormant default flag (1.2 / C1).
   - Layered resolver `recall()` not used by the runtime; flat compat adapter is
     load-bearing (1.3 / C2).
   - `Brain.learn`/unified writer unwired; two Rule #6 write bypasses (1.4 / C3).

3. **Must fix before calling the Brain complete:**
   - Wire `tiered` retrieval on AND route runtime memory retrieval through
     `Brain.recall`/the resolver (or, failing that, explicitly retire the layered
     resolver so the code isn't lying about being active).
   - Route `write_knowledge` (and `memory_ops.search_memory`) through the Brain.
   - Consolidate the three duplicated tiering/durable-tag modules.
   - Resolve the `retrieve_memory_rows` "temporary" labeling so the live path is
     honest.

4. **Should intentionally be deferred:**
   - Flat `MemoryManager`/`brain=None` fallback removal (Phase G).
   - Dead source adapters & obsolete `migrations.py` cleanup.
   - Shared SQLite base for the three scalar stores.
   - Scheduler-driven/idle reflection triggers and scenario completion detection.
   - `extract_every` cadence tuning, decay-horizon personalization.

5. **Does the Brain provide a stable foundation for the remainder of the
   assistant?** **Yes — with the caveat above.** The foundation (typed-column
   storage, append-only provenance, clean layering, bounded reasoning, trust
   surface, 805-green suite) is genuinely maintainable and directionally correct.
   The three wiring gaps are small, local changes that deliver the promised
   cognitive behavior; none require re-architecture. Close those, and Brain V1 is
   a defensible permanent foundation.

---
*End of audit. No code modified.*