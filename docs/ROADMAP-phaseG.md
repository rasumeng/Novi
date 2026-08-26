# Phase G — Legacy Removal, Cleanup, and Migration Completion

**Status:** Roadmap (post-Brain-V1). Scope is **only** what Brain V1 deferred.
**Rule:** no new Brain features, no architectural redesign. Phase G finishes
what the Brain work already decided.

---

## 0. Goal

Brain V1 is feature complete. Phase G removes the scaffolding the Brain made
obsolete, closes the technical debt it flagged, and completes the flat → layered
migration. When Phase G is done, the `brain=None` fallback and the legacy flat
memory pipeline no longer exist, and nothing in the production path bypasses the
Brain.

---

## 1. Legacy Removal (the core of Phase G)

| # | Item | Where | Disposition |
|---|---|---|---|
| 1 | Flat `MemoryManager` internals (buffer/summarize/classify) | `novi/memory/manager.py` | Delete once no `brain=None` fallback remains |
| 2 | `brain=None` fallback branches in runtime + WebUI | `runtime/runtime.py` `_remember`, `webui_server.py` agent_memory | Remove; the Brain becomes mandatory |
| 3 | `get/set_memory_manager` process-global | `memory/manager.py`, `services/context.py` | Remove; `get_brain()` is the single accessor |
| 4 | Flat `MemoryManager.query` legacy path | `manager.py` `query` | Delete; replaced by `Brain.recall` → resolver |
| 5 | `Brain.retrieve_memory_rows` compat adapter | `brain/brain.py:182` | **DONE (post-cutover stage)** — deleted; zero production callers (MemoryRetrievalSource reads `Brain.recall` directly). Guard: `test_no_retired_retrieve_memory_rows_adapter` |
| 6 | `memory_ops` raw-store reads | `tools/memory_ops.py` | Brain-only access |
| 7 | Legacy `Engine` (parallel ReAct loop) | `runtime/engine.py` | Delete once jobs migrated to `NoviRuntime` |

## 2. Dead Code / Obsolete Adapters

| # | Item | Where | Disposition |
|---|---|---|---|
| 1 | One-time Phase C→D migration adapter | `novi/brain/storage/migrations.py` | Archive to `novi/tools/` as documented manual utility, or delete |
| 2 | Dead source adapters (never instantiated) | `runtime/sources/{identity,scenario,file}.py` | Delete (exported in `sources/__init__.py` but unreachable) |
| 3 | `chroma.sqlite3` legacy artifacts | `.novi/project_index` | Purge (documented in audits) |
| 4 | `MemoryManager.query` unused `type_filter` / `MEMORY_TYPES` | `manager.py` | Remove during item 1.4 |

## 3. Technical Debt (flagged in audits, still open)

| # | Item | Where | Notes |
|---|---|---|---|
| 1 | Duplicated importance/confidence/durable-tag helpers | `reasoning/projection.py`, `reasoning/tiering.py`, `reasoning/reflection.py`, `brain/brain.py:72` | Consolidate to one `reasoning/ranking.py` constants/helpers home |
| 2 | Duplicated SQLite bootstrap + safe-json loader ×3 | `storage/{conversation,scenario,relationship}_store.py` | Shared `_sqlite.py` helper |
| 3 | `KnowledgeLayer.list_items` vs `list_objects` | `layers/knowledge.py` | Align naming |
| 4 | Stale docstrings | `services/context.py:147-148`, `sources/memory.py` header | Refresh to reflect Brain-wired reality |

## 4. Migration Completion

| # | Item | Where |
|---|---|---|
| 1 | `write_knowledge` markdown + index dual-write → single `Brain.learn` writer, index gap closed | `tools/file_ops.py` |
| 2 | `LessonStore` — decide: fold into `Brain.learn` or document as deliberate parallel concern | `runtime/lessons.py` |
| 3 | WebUI conversation store — migrate to `ConversationStore` as source of truth, or document the split | `webui_server.py` |
| 4 | Runtime history compaction (`_compact`) — summarizer behind the reasoning layer, or stays session-local | `runtime/runtime.py` |

## 5. Deliberately deferred *past* Phase G (design's "future research")

- Scheduler-driven / idle reflection triggers (design §8.2) — wire when the app needs silent self-maintenance.
- Scenario lifecycle completion detection (auto-advance `COMPLETED/ARCHIVED`).
- `extract_every` cadence tuning, decay-horizon personalization.
- Learned relevance ranking (explicitly non-V1).

---

## 6. Definition of Done

- No `brain=None` fallback, no `MemoryManager` globals, no flat write path.
- `migrations.py` and dead source adapters gone or archived.
- Tiering/durable-tag helpers consolidated; shared SQLite helper landed.
- All production read/write paths route through the Brain (architecture test enforced).
- Full suite green; architecture tests extend to the new deletions.
