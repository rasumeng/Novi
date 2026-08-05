# The Evolution of the Cozmo Brain — A Technical History

**Status:** Durable reference. Written at Brain V1 finalization (2026-08-05).
**Purpose:** Explain **why** every major architectural decision was made, so a new
contributor understands the reasoning behind the current `cozmo/brain/` code —
not just what it does today.
**Reading order companion:** `docs/architecture/brain-architecture.md` (design),
`docs/architecture/phaseF-design.md` (cognitive layer design).

This is **not** a changelog and **not** a how-to. It is the design trajectory that
led from a flat memory table to a layered, append-only cognitive foundation.

---

## 1. The Beginning: Legacy `MemoryManager`

The original Cozmo (v0.1) used ChromaDB-backed memory with auto-summarization.
By the time the retrieval work reached Phase 9, memory lived behind one class,
`MemoryManager` (`cozmo/memory/manager.py`), on a single LanceDB table.

### 1.1 The shape

```
Conversation ──→ LLM summary ──→ keyword class ──→ flat vector row
                                            |
                                            └──→ semantic similarity over everything
```

Every interaction was buffered in a short-term turn buffer (≤10 pairs). Every
5 turns, an LLM produced a 2–3 sentence summary; a keyword matcher assigned a
`type` string (`conversation` / `preference` / `fact` / `project`); the row was
embedded and appended to one table. Retrieval was semantic similarity over that
whole table, optionally filtered by an intent→type mapping.

### 1.2 What it did well

- **Simple.** One class, one store, one mental model.
- **Fast to ship.** It worked for demo conversations and short sessions.
- **One write path.** `add_interaction` was the single entry point from the
  runtime's `_remember`.

### 1.3 The design's core assumptions — and where they broke

| Assumption | Reality |
|---|---|
| A 2–3 sentence summary is enough | Tool outputs — frequently the actual knowledge payload — were dropped. Summaries were lossy by construction. |
| A keyword `type` string is a schema | "preference" and "project" became ad-hoc, fragile string tags; classification was crude (word matching, no confidence). |
| One flat table is a memory | Conversations, preferences, facts, and project knowledge were indistinguishable except by a string. There was no hierarchy, no provenance, no grouping. |
| Similarity = relevance | "Most similar embedding" returned the most *recent* and most *similar* text, not the most *important* knowledge. Recency was treated as importance. |
| `MemoryManager` *is* memory | It was a god-object: buffer + summarize + classify + embed + store + rank + dedup + consolidate. Nothing could be swapped underneath it; it was simultaneously facade and implementation. |

By the architecture audit of 2026-08-03 (`docs/architecture/brain-architecture.md`),
these broke down into concrete pain points: a **god-object** (P1), **flat
storage** (P2), **raw summaries as primary retrieval** (P3), **lossy extraction**
(P4), **fragile keyword classification** (P5), **4× duplicated ranking logic**
(P6), an **untyped JSON `metadata` string** used for filters (P7), and a
**fragmented, three-way conversation representation** (P11).

The Brain rewrite is the answer to precisely this list.

---

## 2. Foundation Migration: ChromaDB → LanceDB

This migration predates the Brain and is the substrate the Brain is built on.

- Changelog `v0.1.0` records the original "ChromaDB-backed memory".
- The move to LanceDB gave **typed schemas**, **SQL predicate filtering**,
  **column-level filters** (rather than JSON-substring matching), and a cleaner
  upgrade path to hybrid (vector + scalar) queries.
- Remnant artifacts (`chroma.sqlite3` in `.cozmo/project_index`) lingered and
  were flagged for cleanup in the audits.

**Lesson carried forward:** storage must support *structured filtering*, not
string matching. That insight — "metadata is not a database" — is the direct
ancestor of the typed-column design in `cozmo/brain/storage/vector_store.py`.

---

## 3. From Flat to Typed Columns

`LanceStore` originally modeled every row the same way:

```
(id, text, metadata: str, vector)
```

Metadata was a `json.dumps` string. Structured queries used fragile SQL like
`metadata LIKE '%"type": "preference"%'` — slow, order-dependent, and easy to
break.

Brain Phase D (`cozmo/brain/storage/vector_store.py`) replaced this with **typed
top-level columns**: `knowledge_id`, `scenario_id`, `source_kind`, `timestamp`,
`status`, `confidence`, `tags`, `last_seen_at`. Filters became real predicates;
provenance-constraining columns (scenario, conversation) became first-class.

**Why typed columns won:**

1. **Correctness.** Column predicates are structurally guaranteed; string
   matching on JSON is not.
2. **Performance.** Vector stores can exploit column-level pruning that string
   scans cannot.
3. **Readability.** A row now *says* what it is; the schema is the documentation.

The migration was a one-time, offline re-embed into the new schema
(`cozmo/brain/storage/migrations.py`) — deliberately a manual utility rather than
a live adapter, because runtime migration of an internal schema is a deployment
problem, not a runtime feature.

---

## 4. The Knowledge-Centric Reframing

The most important conceptual turn happened in `docs/architecture/brain-architecture.md`:

> This document deliberately avoids the word *memory*. The design question is
> not "where do we store memories?" but **"how does Cozmo organize what it
> knows?"**

That single reframing moved the architecture from **storage-centric** to
**knowledge-centric**:

- Storage became an *implementation detail* of individual layers, never the
  organizing principle.
- The five layers became context objects that *organize* knowledge: Identity,
  Projects, Scenarios, Knowledge, Conversations.
- Write direction: **bottom-up** (conversation → knowledge → scenario → project
  → identity). Retrieval direction: **top-down** (resolve context, then walk
  down).
- Raw conversations are never the primary retrieval mechanism again.

The tension this resolves: a "memory model" wants a storage shape; a "knowledge
model" wants a meaning shape. Cozmo chose meaning.

---

## 5. TencentDB Agent Memory — Influences Adopted and Rejected

The layered design draws on the family of "agent memory" architectures
popularized by Tencent's **Agent Memory** research (a.k.a. COG-DB / the
hybrid-memory line of work — conversational + knowledge + relationship +
reflection layers). Below is an explicit ledger of what Cozmo took and what it
deliberately changed.

### 5.1 Adopted

| Concept | How Cozmo embodies it |
|---|---|
| **Layered memory** (Conversation / Knowledge / Scenario / Reflection) | The five-layer `cozmo/brain/` model plus a bounded Reflection coordinator (`reasoning/reflection.py`). |
| **Reflection as a real cognitive pass** | `Brain.reflect()` — a bounded, trigger-gated consolidation + promotion pass, not a storage artifact. |
| **Knowledge, not raw transcripts, as the durable unit** | Extraction turns conversations into atomic `KnowledgeItem`s (Phase C); the conversation layer is the *source*, not the primary store. |
| **Consolidation / deduplication across the corpus** | `KnowledgeLayer.store_extracted` corroborates repeats instead of inserting siblings (Phase F). |
| **A "scenario" as the retrieval anchor** | `ScenarioLayer` — a scenario is *why* a conversation happened; it scopes retrieval. |
| **Structured memory with schema** | Typed-column vector store (Phase D). |
| **Forgetting through criteria, not deletion** | Decay demotes; `SUPERSEDED` preserves history (Phase F). |

### 5.2 Adopted but changed

| Tencent idea | Cozmo change | Why |
|---|---|---|
| **Full property-graph / graph reasoning** | **Bounded edges only** (`derived_from`, `observed_in`, `supersedes`, `conflicts_with`, `references`, `contains`) | General graph traversal is overkill and adds a shop-worth of complexity for the single task Cozmo needs: *provenance* and *supersession*. Non-goal in `brain-architecture.md` §"Non-goals". |
| **Events as an event-sourcing substrate** | **Notification model** — events are best-effort post-persistence broadcasts, never the source of truth | A dead consumer must never break a write. State is written transactionally at the source; events *notify*. |
| **Cloud-based / shared knowledge** | **Local-first only** (SQLite + LanceDB + local models) | Cozmo is a privacy-first local platform. Tencent's design assumed a server. |

### 5.3 Rejected outright

| Tencent idea | Why rejected |
|---|---|
| **AGI-adjacent autonomy** | Cozmo explicitly declares "No AGI": no self-improvement loops, no meta-learning, no autonomous goal-setting, no background daemon. In the Phase F constraints. |
| **Learned / adaptive relevance ranking** | Cozmo uses a static lexicographic tiering function. A learned ranker is deferred as future research. |
| **Recency-as-primary-importance** | Rejected explicitly — recency is a *tiebreaker*, never a co-equal multiplier (Phase F §5). |

The thesis: Cozmo adopted Tencent's **layered structure and the idea that a
memory must reflect** but rejected the **graph generality, event-sourcing
strictness, and autonomy** in favor of a bounded, local, deterministic, append-only
design.

---

## 6. The Layered Architecture (Final)

```
                    Brain  (facade — cognition API)
                      │  observe / recall / learn / resolve / reflect
                      ▼
                   Reasoning    pure operations on knowledge objects
                   ─────────    (no storage imports, no I/O)
                   extraction · promotion · verification ·
                   reflection · tiering · resolver · projection
                      │  operate on Brain objects only
                      ▼
             Identity  Projects  Scenarios  Knowledge  Conversations
                (the five layers of organized knowledge — context objects)
                      │
                      ▼
                   Storage   SQLite (relational) + LanceDB (vectors)
                   implementation detail — never named above this line
```

### 6.1 The five layers

- **Conversation Layer** — raw turns + tool outputs, persisted always (SQLite
  `conversation_store`). The *source* of everything else.
- **Knowledge Layer** — atomic `KnowledgeItem`s (LanceDB typed schema). The
  durable unit; carries `status`, `confidence`, `tags`, provenance.
- **Scenario Layer** — *why* a conversation happened (SQLite `scenario_store`).
  The retrieval anchor; participates in the top-down walk.
- **Relationship Layer** — bounded typed edges (`relationship_store`). Provenance
  (`derived_from`, `observed_in`) and change (`supersedes`, `conflicts_with`).
- **Identity Layer** — not a store; a *derived* accumulation of verified,
  identity-tagged knowledge (`preference` / `goal` / `skill`). "What Cozmo knows
  about the user."

### 6.2 The reasoning tier

Retrieval is not all the Brain does. It also *consolidates, verifies, promotes,
forgets, resolves conflicts,* and *merges duplicates*. Those are reasoning
operations, not storage operations — so they live in a tier that operates purely
on Brain objects and is **completely ignorant of persistence**:

| Module | Responsibility |
|---|---|
| `reasoning/extraction.py` | raw turns → atomic `KnowledgeItem` candidates |
| `reasoning/promotion.py` | candidate → corroborated → verified lifecycle |
| `reasoning/verification.py` | corroboration counting, confirmation detection |
| `reasoning/reflection.py` | bounded, deterministic consolidation/decay coordinator |
| `reasoning/resolver.py` | layered top-down retrieval |
| `reasoning/tiering.py` | lexicographic importance→confidence→scenario→recency ordering |
| `projection.py` | derived read-only personal context |

The purity rule is enforced by the architecture tests (`tests/test_architecture.py`):
no reasoning module imports storage.

---

## 7. Append-Only Design Philosophy

One invariant drives the entire knowledge lifecycle:

> **KnowledgeItems are immutable historical observations.** Change is represented
> as a *new* observation linked to the *old* one by a typed edge. The store
> never mutates a claim in place; it keeps full history. Current state is always
> *derived* — the newest verified, non-superseded observation.

Consequences:

- A preference change ("uses TUI" → "uses WebUI") is two rows + a `supersedes`
  edge, never an overwrite. Both facts remain inspectable.
- **User corrections are append-only.** `correct_memory` demotes (via
  `update_status`) and records the correction as a new verified item; it never
  deletes.
- **Forgetting is decay/archive, never deletion.** Stale items demote to
  `CANDIDATE` and drop out of default retrieval/projection, but stay queryable.
- The `status` ladder (`CANDIDATE → CORROBORATED → VERIFIED → SUPERSEDED`) is the
  single lifecycle; nothing changes in place.

This was a deliberate reaction to the flat table, where "current truth" was
whatever the latest overwrite said, with the past erased.

---

## 8. The Knowledge Pipeline, Step by Step

### 8.1 Observation (write)

```
turn completes
  → Brain.observe(turn)
      → ConversationStore persists (always)          [durable, never lossy]
      → emit ConversationObserved
      → buffer → (every 5 turns) → KnowledgeExtractor
          → atomic KnowledgeItem candidates (dedup'd within batch)
          → scenario ensure/link, provenance edges (derived_from, observed_in)
          → emit KnowledgeExtracted
```

Why **always** persist the raw turn? Because the legacy system's fatal flaw was
that a 2–3 sentence summary was the *only* durable artifact. Persisting the turn
(cheap) and extracting atomic claims from batches is the inverse: durable raw
source + lossy-but-recoverable extract.

### 8.2 Consolidation & evolution (corroboration)

Repeated observations do not create siblings. `KnowledgeLayer.store_extracted`
checks the corpus for a near-duplicate (token-overlap ≥ 0.5); if found, it
**advances `last_seen_at` and corroboration count** on the existing item.
Consolidation keeps the corpus clean.

Confidence grows by **proof, not volume**:

- 1 mention → `CANDIDATE`.
- explicit confirmation (`verification.is_confirm`, "remember that I…") → `VERIFIED` instantly.
- ≥ 2 corroborations → `CORROBORATED` / `VERIFIED`.

A contradiction produces a `supersedes`/`conflicts_with` edge and demotes the old
item — history preserved, current state derived.

### 8.3 Reflection

`Brain.reflect()` is a **bounded, deterministic, pure** coordinator:

- Budget `N` items (default 200), oldest `last_seen_at` first.
- Trigger-gated: scenario-completion / idle-with-pending / confirmation-burst /
  on-demand. **Not** on a surprise clock; nothing runs mid-conversation.
- Applies status + edge mutations through the Brain; emits `knowledge.promoted`
  **after** durable write.

The design principle (Phase F §6.1 / G5): reflection cost must be `< constant`.
Hence no daemon, no unbounded scan, no background "thinking".

### 8.4 Personal-context projection

`Brain.project_context()` / `projection.project()` answers "what does Cozmo know
about me": a **derived, read-only** grouping of identity-tagged items by category
(preference/goal/skill/project/event/relationship), ranked by the §tiering
hierarchy. It is never cached, never stored, and **never invents an attribute** —
it groups only *stated* items, showing `confidence`/`status` alongside. No silent
synthesis (C7 in the audit).

### 8.5 Layered retrieval (read)

```
query → Brain.recall → resolver.recall
   1. resolve context (project → scenario)
   2. load scenario (goal, status, summary, participants)
   3. traverse provenance to the scenario's knowledge
   4. score within that neighborhood (vector similarity)
   5. sufficiency gate → only if steps 2–4 < sufficient, consult conversations
```

This is the Phase E payoff: retrieval is **scenario-anchored and
relationship-constrained**, then vector-scored — the opposite of "similarity over
everything." Within the result, `reasoning/tiering.py` applies a lexicographic
order:

```
importance → confidence → scenario-relevance → recency (tiebreak only)
```

So a rarely-mentioned stable preference outranks a freshly discussed temporary
topic — directly the Phase F DoD #4.

### 8.6 Trust surface

- `inspect_memory()` — read-only audit of what the Brain remembers, per-category,
  with status/confidence/evidence and supersession edges.
- `correct_memory()` — user demote / supersede / archive; append-only; correction
  **outranks** corroboration history going forward.
- Exposed through the `memory_inspection` tool.

---

## 9. The Hardening Pass (Wiring Closures)

After the architecture and cognitive audits, three HIGH gaps remained: retrieval
was not actually tiered, the layered resolver was not the runtime read path, and
the unified knowledge writer was disconnected. The hardening pass closed all three:

| Gap | Fix |
|---|---|
| Tiering off by default | `tiered_resolver=True` default + in `services/context.py` wiring |
| Layered recall not live | `MemoryRetrievalSource` now feeds `Brain.recall` → layered resolver; consumes `RecallResult` |
| `Brain.learn` unwired | `write_knowledge` → `brain.learn`; `memory_ops.search_memory` → `get_brain()` |

These were wiring closures (pointing existing abstractions at their intended
callers), *not* re-architecture — which is why they were judged safe and cheap.
Result: the layered, tiered, unified-writer Brain is now the actual production
path, and the 805-test suite is green.

---

## 10. What Brain V1 Is Not (Non-Goals)

- **Not** an event-sourcing system — events notify; state is transactional.
- **Not** a graph database — bounded edge kinds only.
- **Not** AGI — no self-improvement, no learned ranking, no autonomy.
- **Not** a cloud memory — local-first (SQLite + LanceDB + local models).
- **Not** the flat `MemoryManager` — that survives only as a `brain=None`
  fallback, slated for removal in Phase G.

---

## 11. Final Shape (Brain V1)

```
Brain (cognition facade)
  observe · recall · learn · resolve · reflect · project_context
  inspect_memory · correct_memory · retrieve_knowledge · retrieve_project
  ↓
Reasoning (pure; no storage)
  extraction · promotion · verification · reflection · resolver · tiering · projection
  ↓
Layers
  identity (derived) · scenarios · knowledge · conversations · relationships
  ↓
Storage (implementation detail)
  SQLite (conversation / scenario / relationship) + LanceDB (typed vector store)
```

- **805/805 tests green** (~8.7s, no network deps).
- **Layer boundaries enforced** by `tests/test_architecture.py`.
- **Append-only** everywhere: supersede/archival edges, never in-place mutation.
- **Bounded reasoning**: fixed budget, deterministic triggers, serialized through
  the Brain.

The Brain V1 is the durable cognitive foundation Cozmo needed: it stores raw
experience, extracts durable knowledge, keeps a full history, reasons about what
matters, and lets a user audit and correct it — while staying local,
deterministic, and genuinely maintainable.