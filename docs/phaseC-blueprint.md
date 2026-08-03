# Phase C — Extraction + Scenario Layer: Implementation Blueprint

**Status:** Planned (design approved 2026-08-03, not yet implemented).

**Source spec:** `docs/brain-architecture.md` §459–481 (Phase C) + Part 3 (layered model, reasoning tier, events).

**Baseline:** Commit `f5b0cfa` — Phase B complete (ConversationStore + write pipeline). 634 tests passing.

**Relation to other work:** `docs/phase9.5-blueprint.md` + retrieval merge work are an unrelated, uncommitted stream and are NOT part of this phase.

---

## 1. What Phase B Left

| Component | State at Phase C start | Gap |
|---|---|---|
| `Brain.observe` | persists turn → legacy `add_interaction` shim → emits `ConversationObserved` | Legacy shim writes lossy 5-turn summaries only; no knowledge items, no scenarios, no provenance |
| `MemoryManager` | `_summarize_and_store` buffers 5 turns → LLM summary → keyword class → flat LanceDB row | P2/P4/P5: lossy, no atomic facts, fragile keyword classification |
| `MemoryManager.query` | returns flat `type`-tagged rows | No scenario summaries, no knowledge items |
| `ConversationStore` | raw turns, `scenario_id` column exists but never set | No scenario linkage |
| Scenarios | none | Entire layer missing |

## 2. Scope

**IN**
- `cozmo/brain/reasoning/extraction.py` (pure — no storage imports): chat-capable `KnowledgeExtractor` (moves `cozmo/evidence/extractor.py` core), `Summarizer`, `LayerClassifier`.
- `cozmo/brain/storage/scenario_store.py` (SQLite): rich `Scenario` object, lifecycle.
- `cozmo/brain/storage/knowledge_store.py` (LanceDB via `LanceStore`): `KnowledgeItem` persistence, table `cozmo_knowledge`.
- `cozmo/brain/layers/scenarios.py` + `cozmo/brain/layers/knowledge.py`: layer managers — own their store only, respond to Brain requests.
- Replace `_summarize_and_store` write path: turns → `KnowledgeItem`s → scenario links.
- `MemoryManager.query` preserved as compat shim: inject `knowledge_store` + `scenario_store`; merge legacy rows + knowledge items + scenario summaries.
- Remove legacy `add_interaction` shim from `observe()`.
- `KnowledgeExtracted` event.

**OUT (deferred to later phases)**
- Promotion / verification / identity (Phase F).
- Typed vector schema + `Relationship` edge table (Phase D).
- Layered resolver + sufficiency gate (Phase E).
- Unified writer incl. `write_knowledge` + `LessonStore` (Phase F).
- Scenario completion detection, regrouping, project anchoring (Phase F/G).
- `_compact` unification, `MemoryManager` internals deletion, flat-row cleanup (Phase G).
- Background/async extraction via `Scheduler` (post-G hardening).

## 3. Approved Design Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Extraction runs synchronously in `observe()`, buffered every **5 turns** (legacy cadence) | Deterministic, testable, mirrors legacy latency; persistence still happens first |
| D2 | Knowledge items stored in `LanceStore` flat schema, new table `cozmo_knowledge` | Additive, no migration machinery; Phase D migrates to typed schema |
| D3 | One scenario per conversation at extraction time (degenerate 1:1) | Honors "scenario is why a conversation happened"; regrouping deferred |
| D4 | `MemoryManager.query` merges legacy + knowledge + scenario; stores injected (default None → byte-identical legacy) | Only consumers are `Brain.recall` + `MemoryRetrievalSource`; single seam; MemoryManager becomes explicit compat shim (removed Phase G) |
| D5 | `store_fact` / `store_preference` keep writing legacy flat rows | Unified writer deferred to Phase F |
| D6 | `KnowledgeExtracted` emitted after durable knowledge write; order = persist → extract → emit | Events never describe uncompleted work |

## 4. Final Architecture

### 4.1 Ownership contract

| Component | Owns | Does NOT own |
|---|---|---|
| `Brain` | cognition API, wiring, event emission, extraction orchestration | reasoning math, storage |
| `reasoning/extraction.py` | turn → `KnowledgeItem`s (extraction, classification, scenario summary) | storage, I/O, persistence decisions |
| `layers/scenarios.py` | scenario domain, lifecycle, its store | other layers, reasoning |
| `layers/knowledge.py` | knowledge items, `scenario_id` ownership links, its store | scenarios, reasoning |
| `storage/scenario_store.py` | scenario persistence | invariants, provenance |
| `storage/knowledge_store.py` | knowledge item persistence + vectors | reasoning, classification |
| `MemoryManager` | **compat shim only**: `query` merging legacy + knowledge + scenario | write pipeline (replaced) |

### 4.2 Write flow (new)

```
turn completes
  → Brain.observe(turn)
      → conversation_store.append(turn, conv_id)                 [always]
      → emit ConversationObserved                                  [after durable persist]
      → if extractor wired:
          buffer turn (max 5); when 5 reached:
            extractor.extract(batch)  (pure)                      [KnowledgeItems + scenario summary + tags]
            knowledge layer persists items                        [cozmo_knowledge, scenario_id ownership]
            scenario layer creates/updates scenario (1:1 conv)    [scenarios.sqlite]
            conversation_store.set_scenario_id(conv_id, scen_id)
            emit KnowledgeExtracted                                [after durable write]
      → legacy add_interaction shim REMOVED
```

### 4.3 Components

**`reasoning/extraction.py`** (pure, no storage imports)
- `KnowledgeExtractor.extract(turns) -> ExtractionResult` — sentence-split candidate atomic claims (core from `evidence/extractor.py`), LLM classification hook for confidence + tags, deterministic heuristics fallback, dedup.
- `LayerClassifier.classify(text) -> (tags, target_layer)` — replaces `_classify` keyword matching; LLM-assisted; confidence-gated; falls back to heuristics.
- `Summarizer.summarize(turns) -> str` — scenario summary prompt (abstracts `SUMMARIZE_PROMPT`); used for scenario summaries only, `_compact` untouched.

**`storage/knowledge_store.py`**
- Reuses `LanceStore`; table `cozmo_knowledge`.
- `add(item)` — persists `KnowledgeItem` (form/status/tags/sources/`scenario_id`/confidence in metadata).
- `query(text, k, distance_threshold, tags=None)` — vector search.
- `set_status`, `delete`, `count`.

**`storage/scenario_store.py`** (SQLite)
- `create`, `get`, `update`, `list`, lifecycle transitions (`created`→`active`; completion deferred).

**`layers/scenarios.py` / `layers/knowledge.py`**
- Thin managers over their stores; know their own store + Brain objects only.

**`brain.py`**
- Constructor gains optional `extractor`, `knowledge_layer`, `scenario_layer` (all default None → Phase-B-equivalent behavior).
- `observe()` implements §4.2; `recall`/`learn`/`resolve`/`reflect` unchanged (recall path continues via `MemoryManager.query` shim).

**`memory/manager.py`**
- Constructor gains optional `knowledge_store=None`, `scenario_store=None`.
- `query()`: when stores present, merges legacy flat rows + knowledge items + scenario summaries (dedup by text, rank by score, top-k); when absent, byte-identical legacy.
- `add_interaction` / `_summarize_and_store` / `_classify` stay intact for the `brain=None` legacy fallback path only.

**`conversation_store.py`**
- Add `set_scenario_id(conversation_id, scenario_id)` (column already exists).

**`events.py`**
- `KNOWLEDGE_EXTRACTED = "knowledge.extracted"` + `KnowledgeExtracted` payload: `knowledge_ids`, `conversation_id`, `scenario_id`.

**`services/context.py`**
- Wire real extractor (`router_llm` + `embedding_service`), `knowledge_store`, `scenario_store` into `Brain` + `MemoryManager`.

## 5. Callers of `add_interaction` after shim removal

Only legacy fallbacks, both guarded by `brain is None`:
- `cozmo/runtime/runtime.py:864` (brain-less runtime construction)
- `cozmo/webui_server.py:1363` (brain-less backend)

`MemoryManager.add_interaction` definition remains (legacy fallback + tests). Phase G deletes it.

## 6. Untouched

`MemoryRetrievalSource`, `RetrievalPolicy`, `SourceSelector`, `ResultMerger`, `RetrievalExecutor`, `_compact`, `LessonStore`, `knowledge_index`, `ProjectIndex`, tools, scheduler, `Engine`, webui (already routed through Brain), all read paths.

## 7. Equivalence Guarantee

- Old prompt context = 2-3 sentence window summaries. New = scenario summaries (same shape) + atomic knowledge items.
- Type-filtered queries still match: legacy rows (`metadata.type`), scenario summaries tagged `type="conversation"` (temporary), knowledge items via tag overlap.
- `store_fact`/`store_preference` rows unchanged.

## 8. Invariants

1. `reasoning/` imports zero storage (sqlite3, lancedb, ConversationStore, KnowledgeStore, ScenarioStore).
2. Persistence precedes events; events never describe uncompleted work.
3. Extraction failure never blocks conversation persist or `ConversationObserved`.
4. `MemoryRetrievalSource` / retrieval policy / sources adapters byte-identical.
5. No new globals — inject via `CozmoContext`.
6. Legacy flat rows remain readable until Phase G.

## 9. Mistakes to Avoid

- No LLM in the persist critical path — persist first, extract after.
- No scenario topic clustering / LLM grouping in Phase C.
- No typed vector schema, no edge table, no `metadata LIKE` filters (Phase D).
- Don't touch `MemoryRetrievalSource` / retrieval policy / source adapters.
- Don't delete `MemoryManager` internals (needed by `brain=None` fallback; removed Phase G).
- Don't emit `KnowledgeExtracted` before the durable write.
- Keep extractor pure; persistence lives in layers/Brain.
- Buffer trigger (5 turns) mirrors legacy cadence so equivalence holds.

## 10. New Tests

- `tests/test_extraction.py` — pure: sentence→items, LLM hook, heuristic fallback, dedup, tags, no storage imports.
- `tests/test_scenario_store.py` — create/get/update/list/lifecycle.
- `tests/test_knowledge_store.py` — add/query/status round-trip.
- `tests/test_brain.py` (extend) — observe 5 turns → `KnowledgeExtracted` after write; extractor absent → Phase-B behavior; extraction failure → persist survives + `ConversationObserved` still emitted.
- `tests/test_memory_query_merge.py` — query returns legacy + knowledge + scenario; default-None path byte-identical.
- `tests/test_architecture.py` (extend) — `cozmo/brain/reasoning/` imports no storage; no new `MemoryManager._summarize_and_store` callers.

## 11. Order of Work

1. `reasoning/extraction.py` (pure) + `test_extraction.py`.
2. `storage/knowledge_store.py` + `storage/scenario_store.py` + their tests.
3. `layers/knowledge.py` + `layers/scenarios.py`.
4. `events.py` — `KnowledgeExtracted`.
5. `conversation_store.py` — `set_scenario_id`.
6. `brain.py` — observe pipeline, remove shim.
7. `memory/manager.py` — query merge shim + `test_memory_query_merge.py`.
8. `services/context.py` — wiring.
9. Architecture guards.
10. Full suite green → single Phase C commit.
