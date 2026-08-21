# Cozmo — Knowledge / Memory / Context / RAG / LangGraph Architecture Contract

**Status:** Locked architecture direction. Reconciles the architecture audit (baseline) with the final decisions. **No production code changed.**
**Rule for implementers:** each future stage must compile, pass existing tests, preserve behavior unless intentionally changed, add regression tests, and keep backwards compatibility during migration. Do not delete legacy until its replacement is verified.

---

## A. Canonical Ownership Table

For each domain: owner, persistence, derived/canonical, read path, write path.

### A.1 Conversation

| Aspect | Contract |
|---|---|
| **Owner** | `Brain` (canonical identity owner — `brain.py` `_new_conversation_id`, `conv-<ts>`; `ConversationStore` never generates ids) |
| **Persistence** | `Brain ConversationStore` (SQLite `conversations.sqlite`: `conversations` + `turns` tables, WAL, append-only) |
| **Derived/Canonical** | **Canonical.** Raw turns + tool outputs are the durable record of experience |
| **Read path** | `Brain.observe` consumers, `ConversationStore.turns()`, scenario layer `ensure_for_conversation`, extraction batches, timeline `conversation.observed` |
| **Write path** | Runtime `_remember` → `brain.observe(Turn(...))` (`runtime.py:1044`); WebUI agent_memory fallback (`webui_server.py:1853`) |

Contract rule: WebUI/CLI/Telegram/schedule/queue conversation ids map onto Brain conversation identity. WebUI `current_conv_id` already flows to `coordinator.run_stream(conversation_id=...)` → `brain.observe`. The Brain id is the single canonical id going forward.

### A.2 Runtime history

| Aspect | Contract |
|---|---|
| **Owner** | `CozmoRuntime` (session) |
| **Persistence** | None (in-memory `self.history` list, capped by `runtime.max_history`; `_compact()` LLM summary in `self._summary`) |
| **Derived/Canonical** | **Ephemeral execution state.** Never a durable memory |
| **Read path** | Prompt assembly within the run only |
| **Write path** | Runtime loop appends each turn; compaction folds old turns into `_summary` |

### A.3 Knowledge

| Aspect | Contract |
|---|---|
| **Owner** | `Brain` (`KnowledgeLayer`, `Brain.learn`, `correct_memory`) |
| **Persistence** | `Brain VectorStore` (LanceDB `knowledge_items` table, typed columns: form/status/confidence/tags/sources/scenario_id/source_kind/created_at/last_seen_at/importance) + provenance edges in `RelationshipStore` (SQLite) + scenario ownership in `ScenarioStore` |
| **Derived/Canonical** | **Canonical structured knowledge state** (confidence/status/importance/provenance/verification/supersession/conflicts). Embeddings inside the vector rows are *derived data stored alongside* the canonical item, never the only durable representation |
| **Read path** | `Brain.recall` → `LayeredRetrievalResolver`; `Brain.inspect_memory`; `project_context`; tools `search_memory`/`search_knowledge` via `RetrievalSource` adapters |
| **Write path** | `Brain.observe` → extraction (candidates); `Brain.learn` (verified, immediate); `correct_memory` (supersede/demote/archive, append-only); reflection/promotion status transitions |

### A.4 Markdown

| Aspect | Contract |
|---|---|
| **Owner** | Cozmo knowledge writer (single canonical writer: `Brain.learn` path + knowledge-index writer) |
| **Persistence** | Files under configured `workspace.knowledge` (currently `D:\Projects\Cozmo\knowledge`, default `~/.cozmo/knowledge`), OKF format: YAML frontmatter (`type`, `title`, `tags`, `timestamp`) + body, WikiLinks (`[[Title]]`, `[[Title|alias]]`), relative paths, tags |
| **Derived/Canonical** | **Canonical human-readable knowledge substrate.** Must preserve enough semantic information to reconstruct durable knowledge. LanceDB index is derived from it |
| **Read path** | User/Obsidian/Markdown tooling; `KnowledgeIndex` scan; `read_knowledge` tool |
| **Write path** | `write_knowledge` tool, `Brain.learn` markdown mirror, user edits in place |

**Open debt to fix during implementation:** `tools/file_ops.py:12` hardcodes `KNOWLEDGE = Path("./knowledge")` (CWD-relative), diverging from configured `workspace.knowledge`. The contract: the configured `workspace.knowledge` dir is canonical; the CWD literal must be removed.

### A.5 Relationships

| Aspect | Contract |
|---|---|
| **Owner** | `Brain` (`RelationshipStore`) |
| **Persistence** | `relationships.sqlite` (SQLite `relationships` table: source_id, target_id, kind, created_at; indexed both directions) |
| **Derived/Canonical** | **Canonical structured edges** (`EdgeKind`: derived_from, observed_in, supersedes, references, conflicts_with) |
| **Read path** | `RelationshipStore.outgoing/incoming/list`; `Brain.inspect_memory` (edge view); resolver neighborhood traversal |
| **Write path** | `Brain._write_provenance_edges` (extraction), `correct_memory` (supersedes), `reflection` (supersedes/conflicts); **future:** WikiLink extractor writes `references` edges |

Bidirectional contract with Markdown WikiLinks: WikiLinks ↔ RelationshipStore represent the same relationship model. WikiLinks are the human-readable form; RelationshipStore is the structured form. Both are write-through: a WikiLink added in Markdown creates a `references` edge; an edge of kind `references` can be materialized as a WikiLink in the markdown mirror.

### A.6 Embeddings

| Aspect | Contract |
|---|---|
| **Owner** | `EmbeddingService`/`RerankerService` facades (provider registry: Ollama default, sentence-transformers alternative) |
| **Persistence** | Inside LanceDB rows (derived data, stored with canonical items); also raw vectors in `cozmo_memories`/`knowledge_index`/`project_index` tables |
| **Derived/Canonical** | **Always derived.** Never the only durable representation. Must be rebuildable from Markdown + Brain state |
| **Read path** | `VectorStore.query`, `KnowledgeIndex.search`, `ProjectIndex.query`, `LanceStore` hybrid search |
| **Write path** | Index writers on ingest/learn/consolidate; `memory/rebuild.py` on embedding-backend change |

Critical invariant (locked): if LanceDB is deleted or embeddings change, Cozmo rebuilds all retrieval indexes from durable Markdown + Brain state.

### A.7 Project files

| Aspect | Contract |
|---|---|
| **Owner** | `ProjectIndex` (per-project LanceDB index, `~/.cozmo/project_index/<project-sha1>/`, table `project_index`) |
| **Persistence** | Derived index only (embeddings of parsed file content). Files themselves live in the user's project tree — never copied into the index |
| **Derived/Canonical** | **Derived.** Project files are the canonical content |
| **Read path** | `ProjectRetrievalSource` (retrieval-time), `ProjectIndex.query` |
| **Write path** | `ProjectIndex.index_all` on set_directory/create_project/import_from_chat |

**Locked improvement:** project files must be chunked (not whole-file embedded), and indexing must be incremental, mtime-aware, deterministic, deduplicated, and capable of removing stale chunks — using `KnowledgeIndex` as the model.

### A.8 Execution state

| Aspect | Contract |
|---|---|
| **Owner** | `JobStore`/`JobManager` (`~/.cozmo/jobs/*.json`), `TaskStore` (orchestrator), `TimelineStore` (JSONL `~/.cozmo/timeline/timeline.jsonl`) |
| **Persistence** | JSON checkpoints; SQLite/task JSON; bounded JSONL timeline |
| **Derived/Canonical** | **Derived/canonical execution records.** Not knowledge; resumability + observability |
| **Read path** | Continuation resolver, timeline REST, job UI |
| **Write path** | `JobLifecycle`/`TaskLifecycleProjection` subscribe to runtime plan events; `TimelineService` subscribes to Brain bus events |

### A.9 WebUI chat representation

| Aspect | Contract |
|---|---|
| **Owner** | WebUI server layer (presentation) |
| **Persistence** | `~/.cozmo/chats/<conv_id>.md` + `index.json` (Markdown, UI-owned) |
| **Derived/Canonical** | **View/export/rendering layer — NOT a competing memory store.** Must not hold knowledge the Brain does not hold |
| **Read path** | `/api/conversations`, chat tab, `import_from_chat`, timeline deep-links |
| **Write path** | `saveConversation` → `PUT /api/conversations` → `.md` + `index.json` |

**Migration debt:** the split (Brain SQLite raw turns vs WebUI markdown) must be reconciled so the Brain ConversationStore is the single canonical conversation identity/state and the WebUI markdown is derived for display. `import_from_chat` currently re-reads WebUI `.md` — it must read canonical Brain turns.

---

## B. Synchronization Rules

Explicit behavior for each state-change event.

### B.1 Brain learns knowledge
1. `Brain.learn(statement, source=...)` → `KnowledgeLayer.write` → verified atomic `KnowledgeItem` (append-only) → vector row.
2. **Write-through to Markdown:** append/render the statement into `workspace.knowledge` (OKF file, identity-tagged by source class) — canonical knowledge write.
3. WikiLink extraction on the markdown body → `references` edges.
4. Re-index affected files (mtime-aware) → LanceDB updated.
5. Emit `knowledge.extracted`/`knowledge.promoted` as appropriate.

### B.2 User edits Markdown
1. Detected on next `KnowledgeIndex.index_all` (mtime change) or a watcher.
2. Re-parse OKF frontmatter + body; deterministic chunk ids (`<rel>::<chunk>`) replace stale chunks for that file.
3. **Reconcile with Brain state:** content that was previously a Brain knowledge item and no longer appears in the markdown → supersede (append-only), never hard-delete; new content → candidate/verified item via `Brain.learn` semantics.
4. WikiLinks re-extracted → edges diffed (add/remove `references` edges).
5. User edits are authoritative for *representation*; Brain confidence/status/provenance are preserved where the claim still stands.

### B.3 Markdown is deleted
1. `index_all` sweep removes that file's chunks from the index (deterministic ids).
2. Brain knowledge items that cite that file as a source are **not deleted** — the underlying claim may remain valid from conversation provenance. Items whose only provenance was the deleted file are flagged for decay/supersession on the next `reflect()` pass.
3. `references` edges pointing at the deleted note are removed (dangling link cleanup).

### B.4 WikiLink changes
1. Re-extract links from the changed file.
2. Diff against existing `references` edges for that source: add new targets, remove stale, resolve `[[Title|alias]]` → canonical target id (title/path resolution).
3. Dangling links recorded as metadata/warnings — **never** break ingestion.
4. Backlinks computed from the incoming-edge index.

### B.5 Embedding model changes
1. Config `embedding.backend`/`embedding.model`/`dimension` changes.
2. `memory/rebuild.py` drops/rebuilds **all derived vector stores**: `cozmo_memories`, `knowledge_index`, `knowledge_items`, `project_index`.
3. Re-embed from durable sources: Markdown (knowledge files), Brain state (knowledge_items rows carry their content text), project files.
4. Non-vector durable state (SQLite conversations/relationships/scenarios, markdown) is untouched.
5. Invariant holds: no durable knowledge depends on the old vectors.

### B.6 LanceDB is rebuilt
1. Delete/recreate LanceDB stores.
2. Rebuild `knowledge_items` from Brain state (rows carry content + typed columns), `knowledge_index` from markdown scan, `project_index` from project files.
3. Verify count parity + a smoke recall query; relationships/ownership restored from SQLite + markdown.

### B.7 User corrects memory
1. `correct_memory(item_id, statement, action=superseded|demote|archive)` → status transition (append-only).
2. Superseding with a new statement writes a new verified item + `supersedes` edge.
3. Correction outranks corroboration going forward.
4. Markdown mirror updated: superseded claim annotated/deprecated, correction rendered.

### B.8 Conversation ends
1. Final `Brain.observe` turn persisted.
2. Buffered extraction flush (if `extract_every` not reached, pending batch runs at conversation close).
3. Scenario lifecycle: link/close scenario, update scenario summary from extracted knowledge.
4. Conversation durable knowledge extracted → markdown + brain state; WebUI markdown view finalized (display only).
5. Emit `conversation.observed` + `knowledge.extracted`; timeline records.

### B.9 Project files change
1. mtime-aware `ProjectIndex` scan (incremental; chunk-level diff).
2. Removed files → stale chunks removed.
3. WikiLink extraction where applicable (docs/code referencing notes).
4. Re-embed changed chunks only (deterministic chunk ids).

---

## C. Final Data Flow

```
Conversation ──┐
Files ─────────┤
Projects ──────┤  Ingestion / Observation
User Knowledge ┤
Imported ──────┘
       │
       ▼
   Brain  (observe / learn / ingest)
       │   raw turns → ConversationStore (SQLite, canonical id)
       │   extraction → atomic KnowledgeItems (candidate) + scenario summary
       │   provenance edges (derived_from / observed_in)
       │   verification → corroborate / promote / supersede / conflict (append-only)
       ▼
  Markdown + Structured State   (canonical durable layer, single writer)
   workspace.knowledge/*.md (OKF + WikiLinks)  ⟷  Brain knowledge_items + edges
       │
       ▼
   Relationships
     RelationshipStore (SQLite edges) ⟷ WikiLinks (human-readable)
       │
       ▼
   Embeddings / Indexes   (derived, rebuildable)
     LanceDB: knowledge_items · knowledge_index · project_index · cozmo_memories
       │
       ▼
   Retrieval   (LayeredRetrievalResolver + RetrievalExecutor + ResultMerger)
     scenario → knowledge (scoped) → knowledge (expand) → conversation · sufficiency gate
     semantic + keyword + metadata filter + project/scenario scoping + recency +
     importance + relationship/WikiLink traversal + dedup + context budget
       │
       ▼
   Context Assembly   (minimum sufficient context for the current task)
       │
       ▼
   LangGraph Agent   (orchestration only — see §D)
       │
       ▼
   ModelRuntime   (resolved workload model, verbatim; no fallback)
       │
       ▼
   Response / Tools   (ToolExecutor; tool outputs captured for observation)
       │
       ▼
   Memory Consolidation   (reflect: verify/promote/merge/supersede/decay)
       │
       └──► back to Markdown + Structured State
```

---

## D. LangGraph Boundary

### D.1 Inside LangGraph (owned)

| Concern | Ownership |
|---|---|
| Execution state / graph state | LangGraph |
| Graph transitions | LangGraph |
| Conditional routing (analyze → retrieve → reason → act-loop → reflect → answer) | LangGraph |
| Agent workflow structure | LangGraph |
| Checkpointing / resumability | LangGraph (where appropriate; may coexist with existing `JobStore` checkpoints — decide in migration, do not duplicate) |
| Node composition + streaming (`astream_events`) | LangGraph |

Proposed graph (locked shape from §8 of the decisions):

```
START
  ↓
Analyze            → Orchestrator (intent/evidence/complexity/capabilities)
  ↓
Retrieve Context   → RetrievalExecutor (RAG, sources, budget)
  ↓
Reason             → ModelRuntime (bound tools, selected workload model)
  ↓
Tool?
 ├── yes → Act → ToolExecutor → back to Reason
 └── no
  ↓
Reflect            → Brain.reflect (gated) + evidence post-processing
  ↓
Answer             → final stream
  ↓
END
```

### D.2 Outside LangGraph (unchanged domains)

| Concern | Owner | Must NOT move into LangGraph |
|---|---|---|
| Intent/capability analysis | `Orchestrator` | — |
| Retrieval | `RetrievalExecutor` + `RetrievalPolicy` + sources + `ResultMerger` | — |
| Knowledge/memory | `Brain` (observe/recall/learn/reflect) | LangGraph is NOT the memory system, NOT the knowledge store, NOT a Brain replacement |
| Model execution | `ModelRuntime` / `ModelService` | LangGraph must respect the selected workload model verbatim; never silently substitute; propagate `ModelUnavailableError` |
| Tools | `ToolExecutor` + capability registry | — |
| Evidence processing | `EvidenceProcessor` | — |
| Storage/persistence | `ConversationStore`, `VectorStore`, `RelationshipStore`, `ScenarioStore`, `KnowledgeIndex`, `ProjectIndex`, markdown writer | LangGraph does not write storage directly |
| Embeddings | `EmbeddingService`/`RerankerService` | — |
| Markdown ↔ Brain sync | canonical knowledge writer | — |

**Do not rewrite working components merely to make them "LangChain/LangGraph-native."** Use LangGraph where it provides real architectural value (workflow orchestration), and keep Cozmo's internal interfaces Cozmo's own. No LangChain-specific memory abstractions.

---

## E. Migration Dependencies

Ordered dependency graph (with reasons). The locked sequence is confirmed against the codebase; one adjustment is noted.

```
1. Canonical ownership
      │  establish Brain as canonical conversation/knowledge identity;
      │  fix KNOWLEDGE path divergence (file_ops) EARLY — it blocks every
      │  markdown-mirror stage
      ▼
2. Conversation unification
      │  WebUI markdown becomes view; Brain ConversationStore canonical;
      │  runtime history stays ephemeral; flat memory deprecated
      ▼
3. Markdown/Brain synchronization
      │  single canonical writer (Brain.learn write-through), markdown mirror,
      │  supersession-aware reconciliation, rebuild-from-markdown invariant
      ▼
4. WikiLinks
      │  parsing, resolution (title/path/alias), backlinks, dangling-link
      │  tolerance, RelationshipStore write-through (references edges)
      ▼
5. Unified retrieval
      │  wire ResultMerger across memory/knowledge/project/scenario/identity;
      │  retire duplicate ranking (_rank_memories / search_with_importance);
      │  keep LayeredRetrievalResolver + sufficiency gate
      ▼
6. Project indexing improvements
      │  chunking, mtime-aware incremental, deterministic, stale-chunk removal
      │  (modeled on KnowledgeIndex)
      ▼
7. Evidence pipeline wiring
      │  EvidenceProcessor + chat-capable FactExtractor into the live loop
      ▼
8. LangGraph migration
      │  dual-path first (graph beside run_stream), then cutover
      ▼
9. Legacy removal  (Phase G completion)
      │  flat MemoryManager internals, brain=None fallback, Engine,
      │  dead source adapters, chroma.sqlite3, migrations.py
```

**Why not strict top-to-bottom:** step 3 (markdown/Brain sync) partially depends on step 4 (WikiLinks) for relationship fidelity, and step 7 (evidence) is independent of steps 4–6 and may run in parallel with them. Step 1 must precede all. LangGraph (8) must wait until retrieval (5) and indexing (6) are stable because the graph nodes consume them. Legacy removal (9) must be last — never delete before the replacement is verified.

---

## F. Explicit Non-Goals

Deliberately NOT doing:

1. **No dedicated graph database** — RelationshipStore + WikiLinks are sufficient to establish the graph model.
2. **No Obsidian runtime dependency** — Cozmo creates an Obsidian-compatible knowledge base (Markdown, YAML frontmatter, relative paths, WikiLinks, tags), not an Obsidian-dependent application.
3. **No vendor-specific memory architecture** — no LangChain memory abstractions, no cloud memory, local-first.
4. **No wholesale LangChain rewrite** — keep LangChain narrowly (ChatOllama, ChatOpenAI, message types, StructuredTool, tool binding, model interop).
5. **No model fallback** — strict `ModelService.resolve`, `ModelUnavailableError` propagation, no silent substitution. Locked hard boundary.
6. **No giant rewrite** — Add → Integrate → Test → Migrate → Verify → Remove legacy, one stage at a time.
7. **No replacement of Brain with LangGraph** — LangGraph orchestrates the workflow only.
8. **No replacement of Markdown with LanceDB** — LanceDB is a derived, rebuildable index; no durable knowledge exists exclusively in a vector index.
9. **No replacement of Brain's structured semantics with raw Markdown alone** — confidence, status, importance, provenance, scenarios, verification, supersession, conflicts, and relationships remain Brain-structured state; Markdown and Brain state are complementary durable layers.
10. **No re-litigation of the knowledge architecture** — decisions are locked.

---

## Implementation-Readiness Notes (from audit, reconciled)

- **Already aligned with decisions:** Brain layered storage + append-only supersession + provenance edges (§3, §11 sound); `LayeredRetrievalResolver` sufficiency gate (§6 preserved); strict model selection (§10 locked); `KnowledgeIndex` chunking model for `ProjectIndex` (§7); EventBus → timeline/overview bridge (§A.8).
- **Requires work before LangGraph:** unified conversation store (A.1/A.9), markdown mirror + single writer (A.4/B.1), WikiLinks (A.5/§3), unified retrieval merge (§6), project chunking (A.7), evidence wiring (§7).
- **Provenance classes (§5 of decisions) map onto existing primitives:** `source_kind` (explicit/extraction; extend to user-authored/observed/imported/inferred/generated/external), `confidence`, `status` (candidate/corroborated/verified/superseded), `sources` (provenance), `importance`. A user-stated fact must not share trust semantics with a Cozmo inference — encoded via source_kind + confidence + status today; extended as needed during step 3.
- **Tests to add with each stage:** markdown↔brain reconciliation, rebuild-from-durable after LanceDB deletion, wikilink resolution + dangling tolerance, conversation canonical identity join, unified merge parity with legacy ranking, LangGraph dual-path parity, legacy-removal architecture tests (no storage imports above `brain/storage/`, no flat write path, no `brain=None` fallback).