# Brain V1 — Hardening Audit (Wiring Focus)

**Date:** 2026-08-05 · **Mode:** findings + recommendations, **no code modified.**
**Prior:** `AUDIT-Brain-V1.md` (architecture/cognitive/quality). This pass verifies
**every production read & write path** routes through the intended Brain
abstraction.

Baseline confirmed independently: 805 tests green; `brain.recall` and `brain.learn`
have **one caller each inside `brain.py` only** — no production caller.

---

## 1. Read-path verification (Brain façade)

| Path | Actually does | Verdict |
|---|---|---|
| Runtime memory read (`runtime/retrieval.py` → `MemoryRetrievalSource(brain)`) | `source.retrieve` → `brain.retrieve_memory_rows` (flat compat adapter) | ✅ through Brain, ❌ *not* through `Brain.recall`/layered resolver |
| Runtime knowledge read | `KnowledgeRetrievalSource(brain)` → `brain.retrieve_knowledge` | ✅ through Brain |
| Runtime project read | `ProjectRetrievalSource(brain)` → `brain.retrieve_project` | ✅ through Brain |
| WebUI `agent_memory` recall (`webui_server.py:1367-1374`) | `MemoryRetrievalSource(mem)` — **raw MemoryManager, not Brain** | ❌ **bypass** |
| `search_memory` tool (`memory_ops.py:15-27`) | `MemoryRetrievalSource(mem)` — raw MemoryManager | ❌ **bypass** |

**Root cause of the two read bypasses:** `MemoryRetrievalSource` is Brain-*aware*
(`sources/memory.py:87-96`, uses `isinstance(self._memory, Brain)`) but callers
pass a raw `MemoryManager`, so the adapter never sees the Brain. The tool/WebUI
simply don't hand it `get_brain()`.

Notably the **runtime** path is already Brain-routed. The hole is only in the
tool + WebUI entry points.

---

## 2. Write-path verification (Brain façade)

| Path | Actually does | Verdict |
|---|---|---|
| Runtime `_remember` (`runtime.py:863-873`) | `brain.observe()`, falls back to `memory.add_interaction` only when brain is None | ✅ through Brain when wired |
| WebUI `agent_memory` save (`webui_server.py:1350-1363`) | `mem.store_preference` / `mem.store_fact` for preference/fact — **raw MemoryManager**; `brain.observe` only in the generic else-branch | ❌ **bypass** |
| `write_knowledge` tool (`file_ops.py:187-226`) | writes markdown + `knowledge_index.index_file()` — **never touches Brain** | ❌ **bypass** (Rule #6) |
| `Brain.learn` (`brain.py:280`) | no production caller | ⚑ dead-ends |
| `Brain.correct_memory` / `inspect_memory` via `memory_inspection.py` | `get_brain()` → trust surface | ✅ correct (tool registered in `tools/__init__.py`) |
| LessonStore (`runtime/lessons.py`) | separate writer wired in runtime | ⚑ deliberate parallel system |
| VectorStore writes | only reachable via `KnowledgeLayer`/`ScenarioLayer`; no direct `vector_store.add` outside brain | ✅ correct |

**Summary:** three production write paths bypass the Brain, and the two "unified
writer" methods (`learn`, `correct_memory`) built to absorb them have no callers.

---

## 3. Retrieval — is `Brain.recall` exercised in production?

**No.** Grep confirms the only `recall` successors are inside `brain.py` itself
(`retrieve_memory_rows` → `self.recall`, line 196). The runtime never calls
`brain.recall`; `MemoryRetrievalSource` consumes flat compat rows from
`retrieve_memory_rows`, so even when the Brain is wired, layered top-down recall
(scenario → scoped knowledge → sufficiency gate → conversation) never runs live.

### Concrete migration plan (to put `Brain.recall` on the production read path)

1. **Replace the flat adapter with a resolver-backed recall.** Change
   `MemoryRetrievalSource._query`'s Brain branch to consume `RecallResult`
   (`.items`), not `retrieve_memory_rows`. Map each `RecallItem` → `RetrievedItem`
   (`source=item.source`, `score=item.score`, `metadata=item.metadata`).
2. **Route through the layered resolver.** The resolver is already auto-built by
   `Brain._default_resolver()` when both layers are wired (which `context.py`
   does). So step 1 alone activates scenario-anchored, sufficiency-gated recall —
   no new wiring.
3. **Delete/rename `retrieve_memory_rows`** once nothing consumes flat rows.
4. **Enable tiering simultaneously** (see §5) — otherwise recall returns
   un-tiered similarity order, and DoD §14 items 4 & 7 stay unfulfilled.
5. **Cover with a runtime-level test** asserting the resolver was exercised
   (assert `RecallResult.metrics["layers"]` on a live runtime retrieval), not just
   the unit resolver test.

Risk: low-to-moderate. RecallResult already carries score+metadata; the adapter
translation is mechanical. Main behavioral risk is ranking changes from tiering —
mitigate by enabling tiering in the same change and running the existing 805-suite
(which guards recall regressions). This is the single highest-value hardening step.

---

## 4. WebUI + tool bypasses (read and write)

These are separate from §3 and must be fixed regardless of the recall migration.

- **`write_knowledge` → `Brain.learn`.** Route the tool through `get_brain()` →
  `learn(statement, tags)`; keep the markdown side-effect if desired, but make
  Brain the canonical writer and drop the manual `index_file` call (Brain.learn
  writes a verified item that is immediately discoverable — that's the entire
  point of the unified writer).
- **WebUI `agent_memory` save** → send preference/fact through the trust surface:
  `brain.learn` for facts, identity tag selection for preferences (via the
  `source`→tag mapping already in `_tags_for_source`), and `brain.observe` for
  conversations. Do not call `mem.store_*` directly.
- **WebUI / `search_memory` recall** → construct `MemoryRetrievalSource(get_brain())`
  (or the §3 recall-backed adapter). One-line change; the adapter already knows
  how to unwrap a Brain.

Uncertainty note: I would **not** delete the raw `mem.add_interaction` / flat
fallback branches yet — they are the brain=None safety net and are Phase G work.
I only recommend redirecting the *wired* (brain present) branch.

---

## 5. Tiered retrieval — should it become default?

**Yes.** Recommendation: (a) flip `Brain.__init__` default to `tiered_resolver=True`,
AND (b) explicitly pass `tiered_resolver=True` in `services/context.py` wiring.
Current state is an off-by-default flag that makes §5 "retrieval improvements" a
lie. Enabling it fulfills the design's most-costly feature at near-zero cost.
Gate: run the tiering + recall unit tests and the full suite; adjust
`bucket_importance`/`bucket_confidence` boundaries only if a probe regression
shows a stable preference outranked by a fresh topic (that is exactly what the
tiering tests exist to catch, so changing code should be unnecessary).

---

## 6. Temporary compatibility adapters

| Adapter | Status | Recommendation |
|---|---|---|
| `Brain.retrieve_memory_rows` (`brain.py:189`) | **de facto permanent** — it is the live memory path (runtime source), mislabeled "temporary" | Once §3 lands, **migrate → delete / replace with `recall`**. Until then, rename to `recall_flat_rows` and document as the deliberate flat bridge, so nobody "helpfully" removes the live path. |
| `MemoryRetrievalSource` Brain-unwrap (`sources/memory.py:85-96`) | permanent wrapper | keep; it becomes cleaner once fed by recall-backed results. |
| `MemoryManager` flat pipeline (`manager.py`) | brain=None fallback | Phase G remove. Not a compat shim per se — a real fallback. |

---

## 7. Runtime wiring audit

- **`services/context.py:169-180`**: Brain fully wired (all layers + stores +
  extractor + relationship store); `set_brain` registered. **Does not pass
  `resolver=` or `tiered_resolver`** → change @§5. Docstring at 147-148 is stale
  ("MemoryManager.query merges the knowledge store") — cleanup.
- **`services/context.py:185-191`**: Scheduler is created but
  `on_trigger = lambda s: None` — **a no-op**. No reflection/degradation wiring.
- **Retrieval sources** (memory/knowledge/project): Brain-aware via `isinstance` ✓,
  but tool/WebUI construct them with raw stores (see §4).
- **Tool registration** (`tools/__init__.py`): `memory_ops` and `memory_inspection`
  both registered ✓; `register_tool` registry correct. No duplicate registrations.
- **Reflection triggers** (`Brain.reflect`, `reflection.should_reflect`): the
  `scenario_completed / confirm_burst / idle_pending` params have **zero callers**;
  `reflect()` holds `on_demand=True` default. No scheduler/WebUI/runtime hook
  invokes reflection. Design §8.2 triggers are unrealized.

---

## 8. Bucketed action list

### 🟥 Must fix before Brain V1 (all wiring, no redesign; each is 1-5 lines)

| # | Action | Reason |
|---|---|---|
| 1 | Enable `tiered_resolver=True` (default + context wiring) | §5 feature is silently dead |
| 2 | Migrate runtime memory read to resolver-backed `Brain.recall`; consume `RecallResult` | layered recall is never exercised (DoD 4&7) |
| 3 | Resolve `retrieve_memory_rows`: migrate+delete OR rename to permanent flat bridge | live path is mislabeled "temporary" |
| 4 | Route `write_knowledge` → `Brain.learn` | Rule #6 write bypass; unified-writer deliverable unmet |
| 5 | Route WebUI `agent_memory` save → `learn`/`observe` | write bypass; raw store writes |
| 6 | Route WebUI + `search_memory` recall through `get_brain()` → Brain-aware source | read bypass |
| 7 | Wire **one** reflection trigger (scheduler `on_trigger` → bounded `reflect()` when idle_pending) OR explicitly defer with doc | §8.2 design unmet; currently a no-op |

### 🟨 Safe for Phase G
- Remove flat `MemoryManager` fallback + `get/set_memory_manager` global once
  brain=None wiring is gone (`context.py:128`, `manager.py:33`, `runtime.py:869`).
- Delete dead adapters: `sources/{identity,scenario,file}.py`.
- Delete obsolete `brain/storage/migrations.py`.
- Remove `memory.add_interaction` / `store_*` fallback branches in runtime/WebUI
  when brain is mandatory.

### 🟦 Pure cleanup
- Consolidate duplicated importance/confidence/durable-tag helpers across
  `projection.py` / `tiering.py` / `reflection.py` / `brain.py`.
- Shared SQLite connection/row helper across the three scalar stores.
- `KnowledgeLayer.list_items` vs `list_objects` naming.
- Stale docstrings in `context.py` (147-148) and `sources/memory.py` header.

---

## Final note on "don't modify"

No code was changed. Items in the 🟥 bucket are wiring fixes whose correctness I
consider **unquestionable** (they point existing abstractions at intended
callers — the `isinstance` adapters were *built* to receive a Brain but were
never handed one). The only judgment call is #7 (which trigger to wire): I recommend
`idle_pending` on the scheduler no-op, run only when `pending_count>0`, because it
matches design §8.2 and carries no surprise-write risk. If you prefer to ship
reflection manual-only for V1, that is also defensible — but the no-op scheduler
hook should then be removed or documented, not left silently inert.