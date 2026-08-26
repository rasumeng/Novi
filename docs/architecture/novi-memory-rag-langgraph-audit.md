# Novi Memory, Context, RAG, Document Ingestion & LangGraph Integration — Architecture Audit

**Scope:** Read-only architectural audit of Novi's memory/context/RAG/document-ingestion systems, and their relationship to LangGraph/LangChain.

**Status:** Investigation complete. No production code changed.

**Baseline facts:** 89 test files (84 matching `def test_`/`class Test`). LangGraph is a declared dependency but has **zero actual usage**. The Brain phase A–F architecture from `docs/architecture/brain-architecture.md` is substantially implemented. Phase G (legacy removal) is NOT done — the flat `MemoryManager` and several dead artifacts still exist.

---

## 1. Current Architecture — What Exists Today

### 1.1 LangGraph / LangChain reality

| Claim | Reality | Evidence |
|---|---|---|
| LangGraph orchestrates Novi | **False.** No `StateGraph`, `compile`, `create_react_agent` anywhere in the repo. | repo-wide `rg` scan; `STAGE0-AI-RUNTIME-AUDIT.md:136` |
| LangChain powers models | **True**, narrowly. `langchain_core.messages` (HumanMessage/SystemMessage/AIMessage), `ChatOllama`/`ChatOpenAI` via `novi.providers`, `bind_tools`. | `novi/providers/base.py`; `novi/runtime/models/factory.py` |
| LangChain "structured tools" | **True.** `StructuredTool` used for the tool loop. | `novi/runtime/tool_executor.py` |
| LangGraph dependency declared | **True.** `pyproject.toml:30`. | pyproject |

**Conclusion:** LangGraph is a placeholder dependency. The agentic loop (`NoviRuntime.run_stream`), planning, retrieval dispatch, and memory write/read are all **hand-rolled orchestration** inside `novi/runtime/runtime.py`, `novi/orchestrator/orchestrator.py`, `novi/runtime/retrieval.py`.

### 1.2 Memory system — two parallel architectures

**A) Legacy flat memory (`novi/memory/manager.py`, `MemoryManager`)** — STILL ALIVE as fallback.
- Short-term turn buffer (≤10 pairs) → every 5 turns LLM summary → keyword classification (`prefer`/`project`/`fact`) → LanceDB `novi_memories` table (`~/.novi/memory/lancedb/`).
- `query()` = type filter (buggy, unused) + importance + distance.
- `consolidate()` merges by naive word-Jaccard > 0.7.
- Used by `Brain` as fallback when no knowledge layer, and by the runtime when `brain=None`.

**B) Brain knowledge system (`novi/brain/`) — Phase A–F implemented.**
- `Brain` facade: `observe / recall / learn / resolve / reflect / inspect_memory / correct_memory / project_context` (`novi/brain/brain.py`).
- Storage: `VectorStore` (LanceDB `knowledge_items` table, **typed columns**: id, text, form, status, confidence, tags, sources, scenario_id, source_kind, created_at, last_seen_at, importance, vector) + `ConversationStore` (SQLite raw turns) + `ScenarioStore` (SQLite) + `RelationshipStore` (SQLite typed edges: derived_from / observed_in / supersedes / references / conflicts_with).
- Reasoning layer (pure, no storage imports): `extraction.py`, `promotion.py`, `verification.py`, `reflection.py`, `tiering.py`, `resolver.py`.
- Write flow: `observe(Turn)` → persist turn → emit `conversation.observed` → buffered extraction every `extract_every=5` turns → atomic KnowledgeItems (candidate) + scenario summary (composite) → provenance edges → emit `knowledge.extracted`.
- Retrieval flow: `recall(query, ctx)` → `LayeredRetrievalResolver` walks scenario → knowledge(scoped) → knowledge(global expand) → conversation, with sufficiency gate (score ≥ 0.4 stops expansion).
- Consolidation/reflection (`reflect()`): verify/promote candidate→corroborated→verified, supersede-with-history, decay stale candidates, triggered by scenario_completed/confirm_burst/idle_pending/on_demand.
- User trust surface: `inspect_memory()` (what Novi remembers + edges), `correct_memory()` (supersede/demote/archive, append-only, supersedes edges).

### 1.3 RAG / retrieval orchestration (`novi/runtime/retrieval.py`)

- `Orchestrator.analyze` → intent + evidence signals + complexity → capabilities → `RetrievalPolicy.resolve` → `RetrievalPlan` (strategies: WEB_ONLY / KNOWLEDGE_ONLY / KNOWLEDGE_THEN_WEB / MEMORY_FIRST).
- `RetrievalExecutor.execute` executes the plan, builds per-source prompt context, sets up `RetrievalCoordinator` (web budget + duplicate-query cache), and re-ranks memory via `_rank_memories` (frequency × recency × distance).
- Source adapters (`novi/runtime/sources/`): `MemoryRetrievalSource` (wraps **Brain.recall** when Brain wired, else flat MemoryManager), `KnowledgeRetrievalSource` (file KB index), `ProjectRetrievalSource` (code index), `WebRetrievalSource` (searxng), `ScenarioRetrievalSource`, `IdentityRetrievalSource`, `FileRetrievalSource` (**NoOp stub**).
- `ResultMerger` exists (deterministic cross-source dedup) but the memory path still re-ranks independently — merge not fully wired into memory/knowledge/project path.
- **Evidence pipeline** (`novi/evidence/`, web-evidence only): `EvidenceProcessor` (rank→facts→conflicts→confidence→compress) used ONLY in `novi/evaluation/evidence_ab.py`; the runtime `execute_search` produces raw `EvidenceBundle` without the processor. FactExtractor is web-only, not chat-capable.

### 1.4 Document / project ingestion (`novi/code_indexer.py`, `novi/memory/knowledge_index.py`)

- **ProjectIndex** (`~/.novi/project_index/<project-sha1>/`, table `project_index`): **whole-file embedding — no chunking**, no mtime re-index guard, no dedup on re-index. `.novi/project_index/` has leftover `chroma.sqlite3` (legacy migration artifact).
- **KnowledgeIndex** (`~/.novi/knowledge_index/lancedb/`, table `knowledge_index`): scans `workspace.knowledge` dir (default `~/.novi/knowledge`, currently configured to `D:\Projects\Novi\knowledge` — **which is EMPTY**). OKF Markdown (YAML frontmatter: type/title/tags/timestamp), overlapping paragraph chunking (1000 chars, 150 overlap), deterministic ids `<rel>::<chunk>` so re-index replaces rows, cross-encoder rerank (sentence-transformers), hybrid vector+keyword.
- **Config:** `embedding.backend=ollama`, `embedding.model=nomic-embed-text`, `dimension=768` (defaults; sentence-transformers backend available). `EmbeddingService`/`RerankerService` are facades over a provider registry (`novi/services/embedding.py`, `embedding_providers.py`). Config default has `model=""` — resolved via discovery/selection, never silently substituted. `memory/rebuild.py` drops vector stores when embedding backend changes; brain SQLite persists.
- **Embedding model mismatch risk:** Ollama nomic-embed-text = 768-dim; sentence-transformers all-MiniLM = 384-dim. Backend switch requires full re-embed (handled by rebuild).

### 1.5 Conversation persistence — fragmented across four stores

1. **Runtime in-memory `history` list** (capped, compacted by `_compact()` into `self._summary`). Not persisted.
2. **WebUI markdown chats** `~/.novi/chats/<id>.md` + `index.json` — UI-owned, runtime-unaware, raw text, no frontmatter.
3. **Brain `ConversationStore`** (`~/.novi/brain/` SQLite) — raw turns + tool outputs, canonical Brain identity (`conv-...`).
4. **Legacy flat memory summaries** — lossy 5-turn summaries (only durable artifact in the old system).
Plus **TimelineStore** (`~/.novi/timeline/timeline.jsonl`, bounded 500, newest-first) and **JobStore** (`~/.novi/jobs/*.json`, checkpointed executions).

### 1.6 Knowledge write paths (Rule #6 routing)

- `write_knowledge` tool: writes OKF markdown to `./knowledge` (**CWD-relative `KNOWLEDGE = Path("./knowledge")`**, NOT the configured `workspace.knowledge` — divergence!), refreshes file index, then `brain.learn(content)` → `KnowledgeLayer.write` → verified atomic item, immediately discoverable.
- `search_memory` / `search_knowledge` tools go through the shared `RetrievalSource` adapters (Brain-aware).
- `agent_memory` WebUI message type: `save` → `brain.learn`; `recall` → `brain.recall`.
- `Brain.learn` without layers falls back to `store_fact` (flat).

### 1.7 Model selection (no hardcoded LLM)

- `ModelService.resolve(workload)` reads `llm.workloads.<workload>.model` verbatim; `WORKLOADS = ["general", "research", "code"]`.
- No fallback, no substitution. `ModelUnavailableError` raised and propagated verbatim by `SimpleLLM` and `ModelRuntime`.
- `ModelRuntime` (`novi/runtime/models/factory.py`) is a thin execution boundary: resolved identity → provider → LangChain model. Never selects/picks.
- `deep_research` UI flag routes to `force_intent="research"` workload (`webui_server.py:539`).

---

## 2. Source-of-Truth Hierarchy (current)

```
Raw turns            → Brain ConversationStore (SQLite)     [canonical raw experience]
                         + WebUI chats/*.md                  [UI copy, duplicated]
Extracted knowledge  → Brain VectorStore knowledge_items    [canonical KNOWLEDGE, typed]
                         + flat MemoryManager novi_memories [legacy fallback]
File knowledge base  → workspace.knowledge/*.md (OKF)       [canonical file KB]
                         + KnowledgeIndex (LanceDB)          [derived index, mtime-tracked]
Project files        → project_index (LanceDB)              [derived index, whole-file]
Lessons              → lessons.json                          [separate, unintegrated]
Timeline             → timeline.jsonl                        [bounded feed]
Executions           → jobs/*.json                           [checkpoints]
```

Knowledge relationships: ownership via `scenario_id` column; provenance via `derived_from`/`observed_in` edges; change via `supersedes` edge (append-only, never in-place mutation).

---

## 3. Gap Analysis

### 3.1 CURRENT → what's missing vs the target vision

| # | Vision | Current | Gap |
|---|---|---|---|
| G1 | **LangGraph as orchestrator** | Hand-rolled ReAct loop + planner + retrieval executor + jobs | LangGraph unused entirely; all control flow in `NoviRuntime` |
| G2 | **Markdown = canonical knowledge, human-readable** | Brain knowledge lives in LanceDB `knowledge_items`; markdown KB separate and thin | Brain-extracted knowledge is NOT exported to markdown; no single canonical writer |
| G3 | **WikiLinks as relationships** | Edges live in SQLite `relationships` table; no `[[wikilink]]` syntax anywhere | Zero WikiLink support in files, index, or UI |
| G4 | **Document ingestion (Obsidian-ready)** | KnowledgeIndex reads OKF markdown with YAML frontmatter; no link resolution | No wikilink parsing, no backlink/outlink extraction, no title-based linking |
| G5 | **Embeds = index only, Markdown = truth** | Partly true: file KB re-indexes from md. But Brain knowledge has no md mirror | Brain knowledge is only in vectors+SQLite; source of truth split |
| G6 | **Chunked document indexing** | KnowledgeIndex chunks (1000/150 overlap); ProjectIndex **whole-file, no chunking** | ProjectIndex unusable for large files |
| G7 | **Unified retrieval merge** | `ResultMerger` exists but memory path still re-ranks separately; `_rank_memories` duplicates `search_with_importance` | 4 ranking formulas, one wired |
| G8 | **Evidence-driven research pipeline** | `EvidenceProcessor`/`FactExtractor` exist but **web-only, not wired into runtime** | No evidence processing in the live agent loop |
| G9 | **Stale index hygiene** | `index_file` removes old chunks via `metadata LIKE '%"path"...'` (fragile JSON string match); ProjectIndex no re-index guard | JSON-string filters remain (P7 from brain-architecture) |
| G10 | **Conversation unification** | 4 conversation stores, no shared id | Brain has canonical id but WebUI + runtime + flat memory don't join on it |

### 3.2 GAP → TARGET

- **G1:** Introduce LangGraph state-graph for the agent loop (retrieval → reason → tool → reflect → answer), with the existing `NoviRuntime` components (tools, sources, planner) as node callables. Keep current loop as fallback during migration.
- **G2/G3:** Make markdown the canonical knowledge substrate: Brain-extracted knowledge emits OKF markdown files (scenario-named), relationships become `[[wikilinks]]`; LanceDB index is derived.
- **G4:** Add wikilink parsing to ingestion: resolve `[[Title]]` → file path, create backlink edges in the relationship store, embed link context.
- **G6:** Add chunking + mtime guard to `ProjectIndex`.
- **G7:** Wire `ResultMerger` across memory/knowledge/project/identity/scenario sources; retire `_rank_memories` + `search_with_importance`.
- **G8:** Wire `EvidenceProcessor` into `execute_search`; make `FactExtractor` chat-capable.
- **G9:** Replace `metadata LIKE` filters with typed-column predicates (already done for Brain `VectorStore`; `KnowledgeIndex`/`LanceStore` still legacy).
- **G10:** Make Brain `ConversationStore` the canonical conversation id; WebUI markdown becomes a render-only view.

---

## 4. Target Architecture

```
                     ┌─────────────────────────────────────────────┐
                     │           LangGraph StateGraph              │
                     │   (compile → agent loop, replaces hand-     │
                     │    rolled run_stream loop)                  │
                     │                                             │
                     │  nodes: analyze → retrieve → reason →       │
                     │         act(tool) → reflect → answer        │
                     │  state: messages, plan, evidence, budget,   │
                     │         context(project/scenario)           │
                     └───────────────┬─────────────────────────────┘
                                     │
              ┌──────────────────────┼──────────────────────────────┐
              ▼                      ▼                              ▼
   Orchestrator (analyze)   RetrievalExecutor (RAG)         Brain (knowledge)
   intent/evidence/         sources: memory/knowledge/      observe/recall/learn/
   complexity/capabilities  project/web/scenario/identity   resolve/reflect
              │                      │                              │
              ▼                      ▼                              ▼
   Capabilities registry     EvidenceProcessor (rank/       Reasoning layer
   + tools (LangChain        facts/conflicts/conf/          (pure)
   StructuredTool)           compress)                      │
                                                            ▼
                                              Markdown (OKF + WikiLinks)  ◄── CANONICAL
                                              │       │
                                              ▼       ▼
                                   KnowledgeIndex (LanceDB) + relationship edges
```

**Source of truth order:** Markdown files (with WikiLinks) > Brain knowledge items (derived, typed, provenanced) > LanceDB indexes (derived) > flat legacy memory (migrate/deprecate).

---

## 5. LangGraph Workflow (proposed)

```
StateGraph (State = messages + plan + retrieval + evidence + context)

  __start__ → analyze_node (Orchestrator: intent/evidence/complexity)
           → retrieve_node (RetrievalExecutor.execute → EvidenceBundle + memory/knowledge/project context)
           → reason_node   (model invocation with bound tools)
           → act_node      (tool_executor.execute; conditional routing back to reason)
           → reflect_node  (Brain.reflect gated; evidence post-processing)
           → answer_node   (final token stream) → __end__
```

- Existing callables map directly: `analyze`→`Orchestrator`, `retrieve`→`RetrievalExecutor.execute`, `reason`→`ModelRuntime.bind_tools`, `act`→`ToolExecutor.execute`, `reflect`→`Brain.reflect`, memory write→`Brain.observe` as a terminal node side effect.
- Streaming maps to LangGraph `.astream_events` (existing `run_stream` yields token/tool_call/thinking events the WebUI consumes).
- Single-flight `GenerationOwner` in the frontend stays untouched — LangGraph is backend-only.

---

## 6. Memory-Consolidation Workflow (current + target)

```
Current:
turn → Brain.observe(Turn)
  → ConversationStore.append (SQLite, always)
  → emit conversation.observed
  → buffer turns; at 5 → KnowledgeExtractor.extract(batch)
      → claims (atomic candidates) + summary (composite)
      → ScenarioLayer.ensure_for_conversation
      → KnowledgeLayer.store_extracted (dedup via verification.find_near_duplicate; corroborate → advance last_seen_at)
      → RelationshipStore: derived_from(conv) + observed_in(scenario)
      → emit knowledge.extracted
  → Brain.reflect() on trigger:
      → reflection.make_plan → promote/verify/corroborate/supersede/conflict
      → decay_plan → demote stale candidates
      → write supersedes/conflicts edges
      → emit knowledge.promoted

Target additions:
  → knowledge items ALSO written as OKF markdown (scenario folder) with [[wikilinks]]
  → Brain.learn / correct_memory maintain the markdown mirror (md = truth)
```

---

## 7. Ingestion Workflow (target)

```
Files (md/txt/project src) → walker
  → OKF frontmatter parse + wikilink extraction ([[Title]], [[Title|alias]])
  → chunking (paragraph-aware, overlap)  [ProjectIndex currently skips this]
  → embed (EmbeddingService, backend-configurable)
  → write LanceDB rows (deterministic ids) + relationship edges (wikilink → backlinks)
  → mtime-tracked re-index (KnowledgeIndex does this; ProjectIndex doesn't)
  → brain.learn for explicitly written knowledge (immediately discoverable)
```

---

## 8. Obsidian Strategy

- Keep `workspace.knowledge` as a plain folder of OKF markdown files with YAML frontmatter (title/tags/timestamp/type) — Obsidian-readable as-is.
- Add `[[wikilink]]` support: resolve links at index time (title→path), store backlink edges in `RelationshipStore` (`references` kind), embed link-aware context.
- Markdown stays human-editable; vectors always derived. Re-index on mtime; deletions handled by stale-row cleanup (deterministic ids).
- Obsidian itself is NOT required at runtime — the format is.

---

## 9. LangChain / LangGraph Recommendations

1. **Keep LangChain for model integration** (`ChatOllama`/`ChatOpenAI` via `novi.providers` + `ModelRuntime`). It is correctly layered today.
2. **Adopt LangGraph for the agent loop only** — as the orchestrator over existing nodes. Do NOT rewrite storage/Brain.
3. Use `langgraph.checkpoint` (SQLite-backed) to unify with the existing `JobStore`/`TaskStore` durable-execution story (or keep JobStore and bypass graph checkpoints).
4. Keep streaming compatible: wrap graph in a streaming adapter behind the existing `run_stream` API.
5. Use LangChain `Runnable`/`StructuredTool` as-is for tool binding; no change needed.

---

## 10. Migration Strategy (phased, per existing convention: compiles / tests pass / behavior preserved)

- **M1 (additive):** Add LangGraph graph as a new execution path beside `run_stream`; keep hand-rolled loop default. Test both against the same fixtures.
- **M2:** Wire `ResultMerger` into memory/knowledge/project path; delete `_rank_memories`, `search_with_importance` (or mark legacy).
- **M3:** Markdown mirror for Brain knowledge (write OKF + wikilinks on extract/learn); read-back for recall consistency.
- **M4:** Wikilink ingestion (parse + backlinks) in `KnowledgeIndex`; add chunking + mtime guard to `ProjectIndex`.
- **M5:** Wire `EvidenceProcessor` into `execute_search`; make `FactExtractor` chat-capable.
- **M6 (removal = Phase G):** Delete flat `MemoryManager` internals, `FileRetrievalSource` NoOp (or implement), legacy `knowledge_index` globals, `Engine`, unused `MEMORY_TYPES`/`type_filter`, `chroma.sqlite3` artifacts. Unify conversation stores on Brain id.
- **M7:** Cutover default loop to LangGraph; delete hand-rolled loop after soak.

---

## 11. Risks & Tradeoffs

- **R1 (highest):** LangGraph adoption touches the live agent loop — highest-churn, highest-regression surface. Mitigate with M1 dual-path.
- **R2:** Dual source-of-truth (LanceDB knowledge_items vs markdown mirror) can drift. Mitigate: single writer (`Brain.learn`/extract) writes both; index always derived.
- **R3:** Embedding backend/dimension mismatch on switch — handled by `memory/rebuild.py` but requires re-embed of everything; must also rebuild `knowledge_items`, `knowledge_index`, `project_index`.
- **R4:** `KNOWLEDGE = Path("./knowledge")` (CWD-relative in `tools/file_ops.py`) diverges from configured `workspace.knowledge` — writes can land in the wrong KB. Must reconcile.
- **R5:** WikiLink resolution failure modes (dangling links, title collisions) must not break ingestion — degrade to plain text, record warnings.
- **R6:** WebUI markdown conversation store vs Brain ConversationStore duplication — pick canonical id, keep WebUI as render view.
- **R7:** `metadata LIKE` JSON-string filters are fragile and order-dependent (legacy LanceStore/KnowledgeIndex) — already fixed in Brain VectorStore; must finish for legacy tables.
- **R8:** Strict model selection (no fallback) is intentional and correct per constraint; LangGraph nodes must propagate `ModelUnavailableError` verbatim, never silently substitute.

---

## 12. Files to Change (target)

| File | Change |
|---|---|
| `novi/runtime/runtime.py` | Wrap loop in LangGraph graph; keep streaming API |
| `novi/runtime/retrieval.py` | Wire `ResultMerger`; wire `EvidenceProcessor` into `execute_search` |
| `novi/runtime/retrieval_policy.py` / `result_merger.py` | Extend strategies for scenario/identity; unify ranking |
| `novi/code_indexer.py` | Chunking + mtime guard + dedup |
| `novi/memory/knowledge_index.py` | Wikilink parsing + backlinks + typed-column filters |
| `novi/brain/brain.py` | Markdown mirror on extract/learn; reconciliation |
| `novi/brain/reasoning/extraction.py` | Chat-capable fact extraction |
| `novi/evidence/` | Wire into runtime; chat-capable extractor |
| `novi/tools/file_ops.py` | Fix `KNOWLEDGE` path to configured `workspace.knowledge` |
| `novi/services/context.py` | Wire markdown mirror + new components |
| `pyproject.toml` | LangGraph runtime deps (already present) |
| `docs/architecture/brain-architecture.md` | Phase G completion checklist |
| New: `novi/graph/` (LangGraph nodes/state), `novi/memory/wikilink.py`, tests |

## 13. What NOT to Change

- **`novi/providers` + `ModelRuntime` model construction** — correct layering, keep.
- **Strict model selection + `ModelUnavailableError` propagation** — hard constraint, preserve.
- **Brain storage design** (typed columns, edges, append-only supersedes, scenario ownership) — sound.
- **`LayeredRetrievalResolver` sufficiency-gate retrieval** — sound, keep as retrieval strategy.
- **`EmbeddingService`/`RerankerService` provider facades** — keep.
- **Frontend streaming/`GenerationOwner` single-flight model** — backend change only.
- **WebUI OKF chat export rendering** — keep as a view; do not delete until Brain store is the single canonical source.
- **Local-first, no-cloud constraint** — preserved by all proposals.
