# Novi Brain — Architecture Review & Redesign Blueprint

**Status:** Design proposal (no code changed). Revised after architecture review — adds the
Reasoning layer, the form-axis knowledge model, first-class relationships, and a cognition-styled
Brain API.

**Framing:** This document deliberately avoids the word *memory*. The design question is not
*"where do we store memories?"* but **"how does Novi organize what it knows?"**

That shift changes the architecture from **storage-centric** to **knowledge-centric**. Storage
becomes an implementation detail of individual layers, not the organizing principle.

**Baseline:** Commit `1f84e9c` (Phase 9 unified retrieval). 594 tests passing.

---

## Part 1 — Current Brain Architecture Map

### 1.1 Component inventory

| Component | File | Purpose | Public API | Dependencies | Coupling |
|---|---|---|---|---|---|
| `LanceStore` | `novi/memory/lancedb_store.py` | Low-level LanceDB vector store. Flat schema `(id, text, metadata:str, vector)`. Hybrid search + importance scoring. | `add_texts`, `similarity_search`, `hybrid_search`, `search_with_importance`, `increment_frequency`, `count`, `list_all`, `delete`, `query_sql` | lancedb, pyarrow, embed_func | Storage engine for all three indexes. No domain concept. |
| `MemoryManager` | `novi/memory/manager.py` | **Flat** long-term store. Short-term turn buffer → LLM summary → keyword classification → LanceStore. | `add_interaction`, `query`, `store_preference`, `store_project_context`, `store_fact`, `consolidate`, `list_all`, `count`, `delete`, `query_sql` | `LanceStore`, `EmbeddingService`, LLM (`router_llm`), config | God-object: buffers + summarizes + classifies + embeds + stores + ranks + dedups. |
| `KnowledgeIndex` | `novi/memory/knowledge_index.py` | Indexes `knowledge/*.md` (OKF frontmatter) into its own LanceDB table. Overlapping chunking, deterministic ids, cross-encoder rerank. | `index_all`, `index_file`, `search`, `search_by_tag`, `count`, `get_paths` | `LanceStore`, `EmbeddingService`, `RerankerService` | Separate table, separate metadata convention. |
| `ProjectIndex` | `novi/code_indexer.py` | Indexes project files (whole file = one row) into `project_index` table. | `index_all`, `query` | `LanceStore`, `EmbeddingService` | Whole-file rows (no chunking), no re-index guard, no mtime tracking. |
| `EmbeddingService` | `novi/services/embedding.py` | Shared sentence-transformer wrapper. | `encode`, `model_name`, `dimension`, `clear` | sentence-transformers | Well-factored. |
| `RerankerService` | `novi/services/embedding.py` | Shared cross-encoder wrapper. | `rerank`, `model_name`, `clear` | sentence-transformers | Well-factored. |
| `NoviContext` | `novi/services/context.py` | Composition root. Lazily wires all services; holds globals via side effects. | `memory`, `embedding_service`, `init_knowledge_index`, `create_runtime`, `warmup` | everything | Sets process-global managers. |
| `RetrievalExecutor` | `novi/runtime/retrieval.py` | Single retrieval entry point. Executes plans, web/knowledge search, sets up coordinator, builds memory/project prompt context, recovery. | `execute`, `execute_search`, `recommend_*`, `commit_recovery`, `retrieve_knowledge` | sources, policy, coordinator, budget | Still hand-rolls per-strategy branches; re-ranks memory again. |
| Source adapters | `novi/runtime/sources/*.py` | `RetrievalSource` protocol wrappers: `Memory`, `Knowledge`, `Project`, `Web`, `File`(stub). | `retrieve(query, budget) -> RetrievalResult` | underlying stores | Clean adapters; thin over existing APIs. |
| `RetrievalPolicy` | `novi/runtime/retrieval_policy.py` | Pure decision: sources + strategy + allocation. | `resolve(...) -> RetrievalPlan` | `SourceSelector`, `ContextAllocation` | Clean, pure. |
| `SourceSelector` | `novi/runtime/source_selector.py` | Pure pluggable strategy layer. | `select(...) -> SourceSelection` | none | Clean, pure. |
| `ResultMerger` | `novi/runtime/result_merger.py` | Deterministic cross-source rank + dedup. | `merge(results, query, allocation) -> MergedRetrievalResult` | none | Clean, pure. |
| `RetrievalCoordinator` | `novi/runtime/retrieval_coordinator.py` | Web budget + duplicate-query cache during tool loop. | `intercept`, `record`, `seed_cache` | none | Web-only; knowledge/memory tools not coordinated. |
| `NoviRuntime` | `novi/runtime/runtime.py` | Agentic loop. `_remember` writes to memory; `_compact` summarizes in-memory history. | `run_stream`, `run`, `reset` | everything | Writes memory directly via `memory.add_interaction`. |
| `LessonStore` | `novi/runtime/lessons.py` | Tool-success/failure lessons, persisted to `lessons.json`, injected into prompts. | `record`, `get_context`, `count`, `list_all`, `clear` | none | Second, unrelated memory system. |
| `FactExtractor` | `novi/evidence/extractor.py` | Deterministic sentence-split fact extraction with heuristic/LLM confidence. | `extract(text, query)`, `merge_facts` | none | Currently **web-evidence only**. |
| `EvidenceProcessor` | `novi/evidence/processor.py` | Post-collection refinement: rank → facts → conflicts → confidence → compress. | `process(bundle) -> EvidenceContext` | extractor, conflicts, confidence, compressor | Consumes `EvidenceBundle` (web) only. |
| WebUI conversation store | `novi/webui_server.py` | Markdown chats + `index.json` under `~/.novi/chats`. | REST `/api/conversations` | none | UI-owned; runtime unaware of it. |

### 1.2 Storage layout on disk

```
~/.novi/
  memory/            → LanceStore table "novi_memories"      (MemoryManager)
  knowledge_index/   → LanceStore table "knowledge_index"     (KnowledgeIndex)
  lessons/           → lessons.json                            (LessonStore)
  chats/             → <conv_id>.md + index.json              (WebUI conversations)
  projects/          → (WebUI project imports)
  skills/  agent_state/  attachments/  tasks/
<project>/.novi/
  project_index/     → LanceStore table "project_index"       (ProjectIndex)
```

### 1.3 Write flow (today)

```
user turn + assistant reply
  → NoviRuntime._remember            runtime.py:851
      → history list (in-memory, capped)
      → _compact()                    LLM summary of trimmed history (in-memory)
      → MemoryManager.add_interaction runtime.py:103
          → short_term buffer (≤10 pairs)
          → every 5 turns: LLM summary → _classify (keyword) → LanceStore
  → WebUI additionally: raw markdown conversation → ~/.novi/chats
  → Tool results during loop → LessonStore.record → lessons.json
```

Notes:
- **Conversation summaries** are the only durable artifact of a chat.
- **Tool outputs are not persisted** except when a 2-3 sentence summary happens to capture them.
- Knowledge writes (`write_knowledge`) go to markdown; the index only refreshes on `index_all` (mtime scan).

### 1.4 Retrieval flow (today)

```
user query
  → Orchestrator.analyze                orchestrator.py:179
      intent → evidence signals → complexity → grounding
      → RetrievalPolicy.resolve → RetrievalPlan(sources, strategy, allocation)
  → RetrievalExecutor.execute           retrieval.py:394
      per strategy:
        WEB_ONLY / KNOWLEDGE_ONLY / KNOWLEDGE_THEN_WEB / MEMORY_FIRST
      memory context: MemoryRetrievalSource → MemoryManager.query
                      (intent type-filter × importance × _rank_memories re-rank)
      project context: ProjectRetrievalSource → ProjectIndex.query
  → prompt injection: memory_context, project_context, grounding_text, lessons
  → mid-loop tools: search_memory / search_knowledge / web_search (coordinator gates web only)
```

### 1.5 Fundamental shape

```
Conversation ──→ LLM summary ──→ keyword class ──→ flat vector row
                                          │
                                          └──→ semantic similarity over everything
```

This is exactly the "Conversation → Embedding → Vector Search" anti-pattern the redesign targets.

---

## Part 2 — Architectural Pain Points

### P1. God-object: `MemoryManager` violates separation of concerns
Buffers, summarizes, classifies, embeds, stores, searches, ranks, re-ranks, and consolidates.
Principle 1 says these must be separate services. It is simultaneously a facade and an
implementation — nothing else can be swapped underneath it.

### P2. Flat "one big vector table" — no hierarchy
Conversations, preferences, facts, projects, learning all live in one table distinguished by a
keyword-classified `type` string. No scenario grouping, no project linkage, no provenance chain.
A 5-turn summary becomes a row with no link to the conversation it came from.

### P3. Raw conversation summaries ARE the primary retrieval mechanism
`MemoryManager.query` returns `conversation`-type summaries directly. `_MEMORY_TYPE_FILTERS`
(intent → types) in `retrieval.py:47` is a weak proxy for layered retrieval. Still semantic
similarity over everything.

### P4. Lossy extraction destroys knowledge
Only a 2-3 sentence summary per 5-turn window survives. No atomic facts, no confidence, no
evidence provenance, no verification. `consolidate()` merges by naive word Jaccard > 0.7
(`manager.py:222`), which can merge unrelated word-overlapping sentences.

### P5. Keyword classification is fragile
`_classify` (`manager.py:132`) matches words like "prefer"/"project"/"fact". No LLM fallback, no
confidence, no identity (preference about whom?). OKF is claimed but the classification is crude.

### P6. Ranking logic duplicated 4× with different formulas
- `LanceStore.search_with_importance` — relevance × recency × frequency (`lancedb_store.py:233`)
- `MemoryManager.query` — type filter + truncation (`manager.py:180`)
- `RetrievalExecutor._rank_memories` — frequency × recency × distance again (`retrieval.py:508`)
- `ResultMerger` — weighted source-prior cross-source normalization (`result_merger.py:110`)

The first three re-score the same rows with incompatible formulas; `ResultMerger` exists but is
not wired into the memory/knowledge/project path.

### P7. `LanceStore` metadata is an untyped JSON string
Every write does `json.dumps`, every read `json.loads`. Structured filters use string matching:
`metadata LIKE '%"type": "preference"%'` (`manager.py:196`, `knowledge_index.py:199`) — fragile,
slow, format-order-dependent. `MemoryManager.query` even builds `type_filter` then never uses it
(`manager.py:196-198`). `increment_frequency` interpolates ids into SQL (`lancedb_store.py:274`).

### P8. Process-global singletons
`_memory_manager` / `_global_knowledge_index` are module globals. WebUI shares one memory backend
across all sessions via `NoviContext`. Tests must patch globals. `get_knowledge_index()` is read
at runtime construction time (`runtime.py:240`).

### P9. No Scenario or Project layer in the Brain
Projects exist only as a `ProjectIndex` (file embeddings) + a `project` memory type. No active
project, no workspace registry, no per-project knowledge, no initiative/feature/planning grouping.
`ProjectRetrievalSource` returns a flat string. The scenario layer is entirely missing.

### P10. No Identity layer
User preferences are flat `preference` rows plus a `personality` config string. No long-term
goals, no user model, no skills inventory, no "what I know about the user."

### P11. Conversation storage is fragmented across three representations
- Runtime in-memory `history` list (no persistence)
- WebUI markdown chats + `index.json` (UI-owned, runtime-unaware)
- Lossy memory summaries (only durable artifact)

No shared conversation ID, no provenance from conversation → fact. `import_from_chat` in the WebUI
is an ad hoc conversation→knowledge migration.

### P12. Two summarizers, no abstraction
`_compact` (runtime history compaction, `runtime.py:860`) and `_summarize_and_store` (`manager.py:111`)
are independent LLM prompts. No `Summarizer` interface, no reuse, no progressive abstraction.

### P13. Two knowledge write paths, one stale index
`write_knowledge` writes markdown; the index refreshes only on mtime scan (`index_all`). Memory
summaries never reach the knowledge index. Knowledge is duplicated conceptually (markdown vs
index) with no single writer.

### P14. `LessonStore` is a second, unintegrated memory system
JSON file, unrelated to the vector store, separately injected into prompts. It is really a
Knowledge/Scenario concept (tool expertise) that escaped into the runtime.

### P15. Dead and legacy code
- `FileRetrievalSource` stub (never selected)
- `MemoryManager.query` unused `type_filter`
- Unused `MEMORY_TYPES` dict (`manager.py:54`)
- `Engine` (`runtime/engine.py`) — legacy parallel ReAct loop coexisting with `NoviRuntime`;
  referenced only by jobs + tests
- `chroma.sqlite3` legacy artifacts in `.novi/project_index`

### P16. `_remember` is unconditional and lossy
Every exchange is buffered; only summaries persist. Tool outputs (often the actual knowledge
payload) are never stored. There is no notion of "temporary context" vs "durable knowledge" —
everything or nothing.

---

## Part 3 — Proposed Brain Architecture

### 3.1 Shape: Brain → Reasoning → Layers → Storage

The Brain does not expose stores. The primary abstraction is the knowledge model, and above the
layers sits a **Reasoning** tier where cognition happens.

```
                    Brain  (facade — cognition API)
                      │  observe / recall / learn / resolve / reflect
                      ▼
                   Reasoning     pure operations on knowledge objects
                   ──────────    (no SQLite, no LanceDB, no I/O)
                   promotion        candidate → corroborated → verified
                   verification     corroboration counting, confirmation handling
                   conflicts        disagreement between knowledge
                   consolidation    merge duplicates, supersede, decay, retention
                   resolver         layered retrieval resolution
                   extraction       summarize / classify / extract knowledge from turns
                      │  operate on Brain objects only
                      ▼
            Identity  Projects  Scenarios  Knowledge  Conversations
               (the five layers of organized knowledge — context objects)
                      │
                      ▼
                   Storage   SQLite (relational) + LanceDB (vectors)
                   implementation detail — never named above this line
```

Retrieval direction: **top-down**. Write direction: **bottom-up**.

```
Conversation ──→ KnowledgeItems ──→ Scenarios ──→ Projects ──→ Identity
```

Raw conversations are never the primary retrieval mechanism.

Why the Reasoning tier exists: retrieval is not the only thing the Brain does. It also
consolidates, verifies, promotes, forgets, resolves conflicts, and merges duplicates. Those are
**reasoning operations**, not storage operations. They belong in a tier that operates entirely on
Brain objects and is completely ignorant of how they are persisted.

### 3.2 Knowledge model — form axis, not kind axis

One `KnowledgeItem`. The axis that matters is **form**, not kind:

| Form | Meaning | Examples |
|---|---|---|
| `atomic` | single claim, confidence-scored | "prefers Python", "learned X", "compiler failed", "user likes Y" |
| `composite` | document / chunk, RAG-retrieved | knowledge-base docs, indexed project files |
| `episodic` | observation with context, no claim yet | a raw turn, a tool output, a log line |

A preference, a lesson, and a fact are **the same structural object** — only the semantic tags
differ. A kind-based enumeration would recreate the old `type = "preference"` anti-pattern
(P2). Form-based modeling keeps the reasoning surface uniform.

```python
@dataclass
class KnowledgeItem:
    id: str
    form: str                # atomic | composite | episodic
    content: str
    confidence: float
    status: str              # candidate → corroborated → verified → superseded
    tags: tuple[str, ...]    # soft labels only: preference, lesson, fact, ...
    sources: tuple[str, ...] # provenance: conversation ids / doc paths
    created_at: datetime
    embedding: list[float] | None = None
```

Identity, scenarios, projects, and conversations are **context objects** that organize knowledge
items — they are not themselves knowledge items. This keeps reasoning uniform (it operates on
knowledge) while the layers stay meaningful.

### 3.3 Relationships — first-class, bounded

Every object may hold relationships, so the Brain can **traverse** rather than only score.

- **Ownership** — fixed parent links stored as columns
  (`conversation.scenario_id`, `scenario.project_id`, `knowledge.scenario_id`). Traversing down
  the hierarchy is a column lookup.
- **Edges** — typed cross-layer relationships in one edge table.

```python
EDGE_KINDS = {"derived_from", "observed_in", "supersedes",
              "references", "conflicts_with", "contains"}

@dataclass
class Relationship:
    source_id: str
    target_id: str
    kind: str
    created_at: datetime
```

Provenance is the `derived_from` edge kind — an explicit relationship, not a subsystem. Retrieval
becomes hybrid in a new sense: **relationships constrain/locate, vectors score.** Walk the
hierarchy to find the neighborhood, then score within it.

### 3.4 Scenario — first-class reasoning context

The scenario is *why* a conversation happened: the retrieval anchor. It is not a grouping tag.

```python
@dataclass
class Scenario:
    id: str
    name: str
    purpose: str
    project_id: str | None
    status: str              # created → active → paused → completed → archived
    goal: str = ""
    summary: str = ""
    participants: tuple[str, ...] = ()
    started_at: datetime = ...
    updated_at: datetime = ...
    completed_at: datetime | None = None
```

Resolve the scenario, and the scenario resolves its conversations and knowledge. Lifecycle
transitions are triggered by the reasoning layer (completion detection) or explicit events.

### 3.5 Identity — accumulated evidence, not configuration

No `set/get`. Identity is accumulated, confidence-weighted evidence:

```
conversation
  → candidate preference        (atomic KnowledgeItem)
  → corroborated                (same normalized claim observed again)
  → verified                    (explicit user confirmation, or high corroboration)
```

Explicit user confirmation ("remember that I...") promotes instantly. Identity keeps history:
change is a `supersedes` edge, never an overwrite. Verified atomic knowledge tagged
`preference` / `goal` / `skill` forms the Identity layer.

### 3.6 Package structure

```
novi/brain/
  brain.py            # facade: observe / recall / learn / resolve / reflect
  types.py            # knowledge model + relationship + context object types
  events.py           # domain events (ConversationObserved, KnowledgeExtracted, ...)

  reasoning/          # pure reasoning operations — no storage imports
    extraction.py     # summarize / classify / extract knowledge from turns
    promotion.py      # candidate → corroborated → verified
    verification.py   # corroboration counting, confirmation handling
    conflicts.py      # conflict detection between knowledge
    consolidation.py  # merge duplicates, supersede, decay, retention
    resolver.py       # layered retrieval resolution

  layers/             # per-domain knowledge management — know own store + interfaces only
    identity.py
    projects.py
    scenarios.py
    knowledge.py
    conversations.py

  storage/            # implementation detail
    base.py           # storage protocols
    sqlite_store.py   # relational core
    vector_store.py   # LanceDB vectors (typed schema)
    conversation_store.py
```

### 3.7 Brain API — cognition, not storage

```python
class Brain:
    def observe(self, turn: Turn) -> None:
        """Capture raw experience: conversation + tool outputs."""

    def recall(self, query: str, context: QueryContext) -> RecallResult:
        """Retrieve organized knowledge to ground a response."""

    def learn(self, statement: str, source: str | None = None) -> None:
        """Explicit acquisition: user asks to remember, write_knowledge, lesson."""

    def resolve(self, query: str) -> ContextResolution:
        """Determine active project + scenario for a query."""

    def reflect(self) -> ReflectionReport:
        """Reasoning pass: promote, verify, merge, consolidate, supersede, summarize."""
```

The lifecycle reads like cognition: the runtime **observes** turns, the prompt builder **recalls**
knowledge, tools **learn** explicitly, the resolver **resolves** context, and a nightly job
**reflects** — promoting, merging, consolidating, superseding, and summarizing.

### 3.8 Events — "events as truth" (notification model)

The Brain emits domain events onto the existing `EventBus` after each write:

```
ConversationObserved → KnowledgeExtracted → ScenarioUpdated → ProjectUpdated → IdentityUpdated
```

- **Notification model, not event sourcing.** State is written transactionally at the source;
  events are best-effort notifications. A dead consumer never breaks a write.
- Consumers (WebUI, eval, tracing, and later the background summarizer via `Scheduler`) observe
  independently — this matches the project's existing "events as truth" principle (PLAN.md).
- Events **replace a coordinating Kernel**: layers update themselves in response to the events
  they care about; layers never import each other.

### 3.9 Data flows

#### Write flow

```
turn completes
  → Brain.observe(turn)
      → conversations layer persists raw turn + tool outputs      [always]
      → emit ConversationObserved
  → extraction (reasoning, debounced)
      → KnowledgeItems (atomic candidates) from turn batch
      → classify: tags + target layer
      → link: derived_from conversation, observed_in scenario
      → emit KnowledgeExtracted
  → layer updates via events:
      ScenarioUpdated (new knowledge / richer summary)
      ProjectUpdated  (scenario drift / summary)
      IdentityUpdated (knowledge promoted to verified identity)
```

#### Retrieval flow (layered, relationship-constrained)

```
Brain.recall(query, context)
  1. resolve context      project → scenario          (Brain.resolve)
  2. load scenario        goal, status, summary, participants
  3. traverse edges       scenario → its knowledge    (derived_from / contains)
  4. score neighborhood   vector similarity within that subgraph
  5. sufficiency gate     conversations retrieved ONLY if steps 2-4 < sufficient
```

#### Reflection flow (progressive abstraction)

```
Brain.reflect()   (nightly / on trigger)
  → verify:        corroborate candidates → verified; supersede-with-history on change
  → consolidate:   merge duplicate knowledge (canonical ids), decay stale episodic
  → summarize:     knowledge → scenario summary → project knowledge → identity
  → emit per-layer events
```

### 3.10 Responsibilities

| Component | Owns | Does NOT own |
|---|---|---|
| `Brain` | cognition API, event emission, wiring | reasoning math, storage |
| Reasoning | promotion, verification, conflicts, consolidation, resolution, extraction | storage, I/O |
| Layers | their domain knowledge, response to their events | other layers, reasoning |
| Storage | persistence, indexes | invariants, provenance |

---

## Part 4 — Phased Migration Plan

Constraints per phase: **compiles, tests pass, behavior preserved, no large rewrites.**
Style matches `docs/archive/phase9-blueprint.md` / `docs/archive/phase9.5-blueprint.md`. Each phase is a
separate commit.

### Phase A — Knowledge model + cognition facade (no behavior change)

- Add `novi/brain/types.py`: `KnowledgeItem`, `Scenario`, `Project`, `IdentityEntry`,
  `ConversationRecord`, `Relationship`.
- Add `novi/brain/brain.py`: facade exposing `observe/recall/learn/resolve/reflect`, each
  delegating to today's components behind the scenes.
- Add `novi/brain/storage/base.py` protocols.
- Existing `MemoryManager`, `KnowledgeIndex`, `ProjectIndex` unchanged; the facade wraps them.
- Acceptance: all 594 tests still pass; existing imports untouched.

### Phase B — ConversationStore (new capability, additive)

- `novi/brain/storage/conversation_store.py` (SQLite) — raw turns + tool outputs.
- `Brain.observe` appends to it **in addition to** `MemoryManager.add_interaction` (no
  replacement yet); emits `ConversationObserved`.
- Runtime `_remember` routes through `Brain.observe` instead of calling `MemoryManager` directly.
- Acceptance: conversations persisted and queryable; existing memory behavior byte-identical.

### Phase C — Extraction + Scenario layer replaces MemoryManager internals

- `novi/brain/reasoning/extraction.py`: move `novi/evidence/extractor.py` here, now
  chat-capable; add `Summarizer` + `LayerClassifier`.
- `novi/brain/layers/scenarios.py` + scenario storage (rich scenario object, lifecycle).
- The `MemoryManager._summarize_and_store` path is replaced by extraction: turns →
  `KnowledgeItem`s → scenario links. Writes now go to knowledge + scenarios.
- `MemoryManager.query` API preserved: facade re-serves it as knowledge(scenario) + scenario
  summaries, keeping `MemoryRetrievalSource` untouched.
- Acceptance: queries return equivalent (better-provenance) results; correctness tests updated
  to assert knowledge items / scenarios rather than flat rows.

### Phase D — Relationships + typed vector schema

- `novi/brain/storage/vector_store.py`: promoted typed columns (`knowledge_id`, `scenario_id`,
  `source_kind`, `timestamp`); metadata no longer the filter medium.
- `Relationship` edge table (SQLite); provenance written as `derived_from` edges.
- Replace `metadata LIKE` filters with column predicates and edge joins.
- Migration script re-embeds existing rows (one-time, offline).
- Acceptance: `query_sql` string-matching gone; dedup/index correctness tests pass on new schema.

### Phase E — Layered retrieval via the resolver

- `novi/brain/reasoning/resolver.py`: project → scenario → knowledge → conversation traversal.
- New source adapters `ScenarioRetrievalSource` / `IdentityRetrievalSource`;
  `KnowledgeRetrievalSource` re-pointed at the knowledge layer.
- `SourceSelector` / `SourceType` extended with `SCENARIO`, `IDENTITY`; resolution order follows
  the layered shape.
- Unified `ResultMerger` for all sources — kills the duplicated `_rank_memories` / importance
  re-ranking; `LanceStore.search_with_importance` becomes legacy.
- Conversations retrieved only when the sufficiency gate fails.
- Acceptance: retrieval plans reflect layered order; web behavior unchanged; merge metrics traced.

### Phase F — Identity promotion + unified knowledge writer

- `novi/brain/reasoning/promotion.py` + `verification.py`: candidate → corroborated → verified,
  supersede-with-history. Identity seeded from existing `preference` memories (as candidates).
- Explicit-confirmation detection ("remember that I...") promotes instantly.
- `Brain.learn` unifies `write_knowledge` + `LessonStore` — single knowledge writer, no stale
  index gap.
- Acceptance: preferences surface as verified identity; knowledge writes immediately searchable.

### Phase G — Legacy removal + hardening

- Delete flat `MemoryManager` internals, old `knowledge_index` globals, `search_with_importance`,
  `Engine` (if jobs migrated to `NoviRuntime`), unused `MEMORY_TYPES`, dead `type_filter`.
- New architecture-regression tests (mirroring `test_architecture.py`): no storage imports above
  `brain/storage/`, no JSON-metadata filters, no `metadata LIKE`, no reasoning-layer I/O.
- Acceptance: full suite green; architecture tests enforce the boundaries going forward.

---

## Non-goals (this redesign)

- Backwards compatibility of the flat memory schema (storage is internal).
- **No event sourcing** — events notify; state is written transactionally at the source.
- **No graph database, no property-graph taxonomy** — bounded edge kinds only.
- Multi-user isolation, real-time sync, external memory backends (PLAN.md §5.5 deferred) — these
  become *strategies behind storage interfaces*, now trivial to add.
- Any cloud dependency. All layers remain local-first (SQLite + LanceDB + local models).

## Open questions

1. Promotion thresholds: how many corroborations before verify, and how aggressive is
   explicit-confirmation detection ("remember that...")? (Recommendation: 2 corroborations, or
   1 explicit confirmation, both with a confidence bar.)
2. Scenario activation: auto-detect from project + session context, or require explicit
   `/new-task` intent? (Recommendation: auto-detect with explicit override.)
3. Does the WebUI conversation store migrate into `ConversationStore`, or does the WebUI keep its
   markdown rendering and `ConversationStore` become the source of truth for the Brain?
4. Does the runtime history compaction (`_compact`) become a summarizer behind the reasoning
   layer, or stay in the runtime for session-local recall?
