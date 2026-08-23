# Cozmo — Development Log (DEVLOG)

> **Chronological development log.** Records *what happened, when, and why* as the
> project evolved. This is **not** an architecture reference (see `docs/architecture/`)
> and **not** a feature catalog (see `CHANGELOG.md`). It answers one question:
> *"What happened on this project over time?"*
>
> Entries are timestamped oldest-first. Estimate-dates are marked `~`.

---

## 2026-06 — Foundations: from zero to a first agent

### 2026-06-15 — Repo seeded
Initial import of the Cozmo project (`1fa4ab1`).

### 2026-06-29 — Phases 1–3; DEVLOG established
- First milestone milestone phases (goal/vision + CLI agent) marked done.
- DEVLOG introduced (per the `f7ca6d7` commit "1. Fixed DEVLOG 2. completed phase 2 and 3").
- Prepared the project for open-source release (`1c9a315`).

---

## 2026-07-01 → 07-08 — A real agent, not a classifier

### 2026-07-02 — CozmoTUI merged in
Adopted the TUI/code shell from its standalone repo and merged it into the full
project (`eadc08e`, `30a8c97`). Cozmo's Code path became usable.

### 2026-07-03 — The turn to an agentic loop
Replaced the original **one-shot classify → generate** pipeline with a real
**agentic (ReAct) loop** (`9d346d6`). This is the fork in the road: Cozmo stopped
being a markdown classifier and became an orchestrator that decides intent,
retrieval need, and tool execution at runtime.

During this era the analysis layer matured:

- **Grounding Architecture Refactor** — `GroundingDecision` (needs_grounding,
  confidence, reason, source) replaced a bare `TaskProfile.needs_grounding`
  boolean. Orchestrator owns grounding via a four-tier pipeline:
  keyword → heuristic → LLM → none. `IntentDetector` classifies; `EvidenceDetector`
  detects external-info signals only; `GroundingReasoner` LLM-judges ambiguity.
- **Trace Architecture Rewrite** — three-layer traces (internal state → `TraceEvent`
  → `TraceFormatter`) so the UI never sees raw confidence/heuristic/routing
  internals. Dual streams: user-facing `TraceEvent` + debug-only `DebugTraceEvent`.
  `ExecutionTrace` emitted once per `run_stream()`.
- **Retrieval Architecture** (first pass) — separated *whether* (grounding) from
  *where* (retrieval policy): `RetrievalPolicy` (pure decision) + `RetrievalCoordinator`
  (budget/dedup gate). Strategies `NONE/KNOWLEDGE_ONLY/WEB_ONLY/KNOWLEDGE_THEN_WEB`.
- **Retrieval Optimization** — stop `search→search→search→fetch→timeout`; enforced
  max 1 web search + 1 web fetch, duplicate detection, strategy-aware budgets.
- **Recovery** — `RetrievalQuality` (SUFFICIENT/WEAK/EMPTY/FAILED) drove two-phase
  recovery (pre-loop tool upgrade + mid-loop retry).
- **Evidence / Search** — `EvidenceCollector` + `EvidenceBundle`; SearXNG fixes;
  source ranking (text over video/image, relevance overlap).

### 2026-07-08 — CozmoBrain integrated
First integration of the standalone CozmoBrain component into the main repo
(`be1a5c0`). This is the seed of the "Brain" reasoning component that later
becomes `cozmo/brain/`.

---

## 2026-07-09 → 07-12 — WebUI maturation

### 2026-07-09 — WebUI functional
Cozmo WebUI works ("Cozmo WebUI works well"); settings, tabs, and mic wiring landed
(`91dc333`, `340afd9`).

### 2026-07-10 — Phase 1–3 feature set
File attachments, vision routing, and projects (collab/project management).
Cleanup and "entering the final phases" (`4f70624`, `99d9fa1`, `0476d70`).

### 2026-07-12 — Code mode redesign + collab projects (Phase 7–8)
Code-mode UI redesign; collaborative project management (`9e3c555`, `b0eb515`).

---

## 2026-07-13 ⇒ 07-21 — Refactor call, then stability

### 2026-07-13 ⇒ 07-14 — Separate Brain development; plan churn
Era of plan updates and a pivot: CozmoBrain pursued separately to wire up the
"fully functional Agent component" (`6781be2`, `4ea7ca3`). PLAN churned (`cebe14e`,
`ae2b8a3`, `6bd65e8`, `5dd49a3`).

### 2026-07-21 — Stable; the refactor decision
- Reached a stable state; changelog documented v0.2.0 (UI/settings) and v0.3.0
  (agent events) (`ab57945`).
- Acknowledge "time to refactor" (`1a94bb6`) — the prelude to the Phase 6.5 runtime
  stabilization that follows.

---

## 2026-07-24 — Dead-code purge

Removed unused archives: planners, reflection, session, workspace, policy,
continuation, and stub tools (`2dc7e39`). Maintenance/doc sync + dead-code removal
+ version fix (`d36b982`). The **first** wave of the "kill the legacy, keep the
lean" discipline that recurs through to Phase G.

---

## 2026-07-28 — Retrieval documented before it was unified

Documented the retrieval architecture + agent pipeline improvements (`ffdc038`,
`aa47b60`) — recording the pre-Phase-9 design before the unification that follows.

---

## 2026-07-30 — Phase 6.5: Runtime stabilization

The runtime was the worst maintainability hotspot. Stabilized it:
**1814 → ~1000 lines.** Extracted `ToolExecutor`, `RetrievalExecutor`,
`RuntimeTracer`, and a `RuntimeInterface` protocol; architecture audit complete
(`e7d6a84`). This de-god-objecting of `CozmoRuntime` is the runtime-side twin of
the later Brain refactor.

---

## 2026-07-31 — Phase 7–8 + Pre-Phase 9 correctness sprint

Phase 7 (evidence processing) + Phase 8 (evaluation/observability) complete. The
2026-07-31 memory architectural audit found correctness defects in the existing
foundations: duplicate knowledge indexing, broken WebUI memory endpoints,
unregistered memory tools, dead reranking/consolidation paths, and config values
that did not control behavior. Unified retrieval (Phase 9) had to rest on stable
ground, so a **reliability (not architecture) sprint** landed:

- Knowledge index reliability: deterministic chunk ids, idempotent re-index,
  stale-chunk removal, vector-index support.
- Memory correctness: config now actually drives behavior, active `MemoryManager`
  registered for tool access, `embed_model` stamped on records.
- Embedding/reranker lifecycle exposed; reranker wired into the index.
- Memory tools registered; regression suite added.

### Test Suite Consolidation (same period)
Killed the network/backend dependency: mocks replaced live SearXNG and the full
backend fixture. Results: **403 tests, all passing, ~25s → ~5.4s**, no network.
Also exposed an un-gated debug-trace append bug in `RetrievalExecutor`.

---

## 2026-08-01 — Phase 9: Unified retrieval policy

Retrieval unified under **adapters** (each source owns its store access) with
recovery owned by the executor, `SourceSelector` + `ResultMerger` (`1f84e9c`).
This is the last commit of the *pre-Brain* retrieval architecture — and it becomes
the baseline the Brain supersedes.

---

## 2026-08-02 — The Brain redesign blueprint

`docs/brain-architecture.md` (`df3b1fa`): the framework reframing from
**storage-centric** to **knowledge-centric**, the Reasoning tier, the form-axis
knowledge model, bounded relationships, first-class scenarios, and a cognition API.
Baseline stated: commit `1f84e9c`, 594 tests.

---

## 2026-08-03 — Brain Phases A–F land (the Brain V1 series)

Ten commits build the Brain from scaffolding to layered retrieval to identity:

| Commit | What it delivered |
|---|---|
| `b754a07` | Intro: `Brain` facade + `types.py` + `storage/base.py` protocols |
| `f5b0cfa` | Route conversation writes through `Brain.observe` → ConversationStore |
| `d833125` | Blueprint: Phase C extraction + scenario layer |
| `569b616` | Replace the memory write pipeline with extraction (Phase C) |
| `03af721` | Typed knowledge columns + provenance `derived_from`/`observed_in` edges (Phase D) |
| `b9111a0` | Layered retrieval via the resolver (Phase E) |
| `8269a33` | Pluggable `SourceSelector` + unified `ResultMerger` (Phase E) |
| `8a10811` | Layered scenario/identity retrieval tiers (Phase E) |
| `8b5dbd5` | Identity promotion + unified knowledge writer (Phase F) |

Suites tracked: 594 → 634 (B) → ~740 (F). This is where the flat `MemoryManager`
becomes a `brain=None` fallback rather than the live path.

---

## 2026-08-05 — Brain V1 finalization: Phase F tail + hardening + audits

The working-tree tail of Phase F (consolidation, reflection, projection, tiering,
trust surface) plus the audits and the wiring closure:

- **Phase F tail landed and tested** — `reasoning/{reflection,tiering}.py`,
  `projection.py`, `tools/memory_inspection.py`, `KnowledgeItem.last_seen_at`
  and `importance`, `knowledge.promoted` events, and the trust surface
  (`inspect_memory` / `correct_memory`, append-only).
- **Audits** — `AUDIT-Brain-V1.md` (architecture/cognitive) and `HARDENING-Brain-V1.md`
  (read/write path wiring) taken. Both flagged **three HIGH wiring gaps**:
  1. tiered retrieval off by default;
  2. the layered resolver not on the runtime read path (flat compat adapter was
     load-bearing);
  3. `Brain.learn` / unified writer disconnected (`write_knowledge` + `LessonStore`
     bypassing the Brain).
- **Hardening closed all three** — `tiered_resolver=True` default + wiring;
  `MemoryRetrievalSource` now consumes `Brain.recall` → layered resolver;
  `write_knowledge` → `brain.learn`; `search_memory` → `get_brain()`. These were
  *wiring closures*, not re-architecture: point existing abstractions at their
  intended callers.

**Result:** **805/805 tests passing** (~8.7s, no network). The layered, tiered,
unified-writer Brain is the actual production path. Brain V1 is declared feature
complete, and focus shifts to the rest of the assistant.

---

## After Brain V1 — what's next

See `docs/ROADMAP-phaseG.md` — legacy removal, cleanup, technical debt, migration
completion. No new Brain features.

---

## 2026-08-09 → 08-10 — Milestone 5: durable execution architecture

Milestone 5 rebuilt Cozmo's execution model around durable intent. The throughline
of the milestone is the architecture rule: *an integration provides I/O, never its
own execution pipeline.* Each phase landed as its own checkpoint commit:

- **Phase 0–4** — `conversation_id` threading; live `TaskStore`; `PlannerEngine`
  + `Plan`/`PlanStep`; sequential Plan execution with lifecycle events; Job
  lifecycle (`Job`/`Checkpoint`/`ExecutionHistory`) with recovery.
- **Phase 5A–5D** — continuation detection; `ExecutionHistory`; runtime resume and
  end-to-end continuation (new attempt, never resurrect the interrupted Job).
- **Phase E-1** — `ExecutionCoordinator`: the single ownership seam owning
  Task/Plan/Job/history for one attempt, extracted from WebUI `Session`.
- **Phase E-2** — Telegram integration boundary + SDK correctness: lazy
  `python-telegram-bot` import, thin adapter, off-loop worker bridge.

### Phase E-3 — execution surface unification (this entry)

All remaining surfaces converge on the coordinator seam; no normal surface calls
Runtime directly anymore:

- **CLI** — `CliSessionAdapter` (stable `cli:<session_id>` identity) renders the
  coordinator stream (tokens / errors / continuation candidates).
- **Telegram** — `telegram:<chat_id>` identity; full-flow tests prove an allowed
  chat drives Task/Plan/Job/History and a denied chat creates nothing.
- **TaskQueue** — worker is dispatch-only; each prompt becomes a real TaskStore
  Task via `run_background` (no fake/orphan task ids).
- **Background runs** — `run_background` replaces the old orphan-job
  (`schedule-<run_id>` fake task id) pattern with a real Task/Plan/Job/History
  chain; attempts tagged `source=background` / `run_id`.
- **Scheduler** — input producer only; `_scheduled_trigger` → coordinator;
  attempts tagged `source=schedule` / `schedule_id`. The 5E audit's duplicate
  scheduler instances are consolidated into the `CozmoContext` singleton (guard
  test asserts a single `Scheduler()` construction point).
- **E-3.6** — configuration compatibility: surfaces never hardcode model
  selection; model resolution stays centralized (ctx `ModelService` +
  orchestrator `ModelRouter`). Guard test enforces it.
- **Cross-surface regression** — one execution per surface → exactly one
  Task/Job/history entry (WebUI/CLI/Telegram/TaskQueue/Background/Scheduler).

Manual Telegram real-device validation steps live in `docs/telegram-setup.md`
(no secrets). Suite state at this checkpoint: 979 passed; the only reds are 8
pre-existing, ordering-dependent `test_tool_retrieval.py` failures (Brain-wired
`search_memory` hits a live embedding backend when run late in the suite; they
pass in isolation, unrelated to E-3).

---

## 2026-08-12 — M5.4: Connector Registry + MCP server permission consumption

Milestone 5.4 added a thin **Connector Registry** seam and closed the
`mcp.servers.<name>.permissions` gap without touching M5.1/M5.2/M5.3 behavior.

- **`cozmo/connectors/registry.py`** — `ConnectorRegistry` +
  `ConnectorDefinition`. Identity only: id/type/label/enabled/identity, plus a
  per-connector SAFE status callback the registry merely relays. Registration /
  lookup / enumeration / unregister / duplicate-raise. No lifecycle, no tool
  execution, no permission evaluation, no persistence — MCPManager and
  TelegramLifecycle keep owning their connectors. Registered as MCP + Telegram
  types; runtime state is in-memory only (MCP/Telegram sessions never enter it).
- **`cozmo/runtime/mcp_permissions.py`** — `MCPPermissionGate`, a config-derived
  deny gate consuming `mcp.servers.<name>.permissions` (`{op_key: bool}`, the
  exact shape the Connectors UI already writes). It only DENIES; it never
  force-allows, so the existing `PermissionResolver` + `ToolExecutor` path stays
  authoritative. Wired into `ToolExecutor._check_permission` and shared into
  every WebUI per-session runtime; config changes flow through the existing M5.3
  `mcp` apply hook (no second event/persistence system).
- **`cozmo/webui.py` / `webui_server.py`** — composition root builds the registry
  and gate, refreshes derived connector state on config change, and exposes an
  additive `GET /api/connectors/status` (existing MCP/Telegram endpoints and the
  Connectors UI are untouched).
- **Secret safety** — status surfaces reuse M5.2-safe connectors (MCP
  `get_lifecycle`, Telegram `get_status`); the registry never reads or returns
  raw config/env/tokens.
- **Statelessness** — MCP remains disposable runtime state; starting/reconnecting
  reconstructs from config, stopping discards it. Nothing is persisted.

Suite: 33 new M5.4 tests green; M5.2 redaction, M5.3 lifecycle, registry /
executor / configuration regressions green. Full-suite state unchanged from the
E-3 checkpoint: the only reds are the same 7 pre-existing ordering-dependent
`test_tool_retrieval.py` failures.

---

## 2026-08-12 — M5.5: MCP seams decomposition + hermetic test suite

Milestone 5.5 decomposed the former all-in-one `MCPManager` into independent,
independently-testable seams under `cozmo/runtime/mcp/` while keeping every
legacy surface (webui, CLI, `/api/mcp/test`, M5.2/M5.3/M5.4 suites) working.

- **`cozmo/runtime/mcp/lifecycle.py`** — `MCPLifecycle` seam: owns which
  configured servers run (mcp.enabled + per-server enabled gating), the
  background event loop, connect/disconnect/reconnect reconciliation, error
  isolation (one server's failure never takes the others down), and clean
  idempotent shutdown. Delegates protocol/session work to the runtime client;
  passes tool discovery to the discovery seam. No tool execution, no
  permissions, no config writes.
- **`cozmo/runtime/mcp/runtime_client.py`** — `MCPRuntimeClient` seam: one
  server's live connection/session through an injectable host factory
  (default `MCPHost`). Connect/list_tools/close, failure records `last_error`
  and re-raises, close idempotent. In-memory only, disposable.
- **`cozmo/runtime/mcp/discovery.py`** — `MCPToolDiscovery` seam: registers a
  server's discovered wrappers into the EXISTING `ToolRegistry` (never a
  second registry), replace-on-rediscover (no duplicate registrations),
  unregister removes exactly the owning server's tools. Loop-bound
  async→sync bridging is supplied by the lifecycle; tests inject identity.
- **`cozmo/runtime/mcp/status.py`** — `MCPStatus` seam: read-only observer of
  lifecycle + discovery. Safe surface only (name/enabled/connected/state/
  error/tool count). Never starts/stops/reconnects, never exposes env,
  commands, tokens, or raw config. Works loop-on and loop-off.
- **`cozmo/runtime/providers/mcp.py`** — `MCPManager` is now a thin facade that
  delegates to the same seam instances and keeps the legacy public surface and
  test-visible internals (`_loop`/`_hosts`/`_server_tools`/`_configured`).
- **Shared runtime primitives** — the CLI `mcp` command and the WebUI
  `POST /api/mcp/test` endpoint now drive connections through
  `MCPRuntimeClient` (the same seam the lifecycle uses) instead of a second,
  incompatible connection path. `/api/mcp/test` keeps test connections
  isolated from the configured lifecycle — it builds an ephemeral client and
  never touches the shared manager's sessions.

Suite: 30 new hermetic M5.5 tests green (all seams instantiated directly with a
fake runtime — no real MCP servers/subprocesses, no MCPHost, no MCPManager
facade). Full M5.2 / M5.3 / M5.4 / webui-boot regressions green. Full-suite
state unchanged: the only reds are the same 7 pre-existing ordering-dependent
`test_tool_retrieval.py` failures.
---

## 2026-08-21  M3: WikiLink Resolution + Knowledge Graph Relationships

M3 turns WikiLinks from creation-only eferences placeholders into a resolvable
relationship graph over the existing RelationshipStore (no new graph DB, no
second relationship store, no LangGraph).

- **cozmo/brain/wikilinks.py** (new): deterministic, ordered WikiLink resolution
  (exact note identity/path ? canonical title ? normalized title ? aliases),
  NoteIndex rebuilt from Markdown only, WikilinkSynchronizer doing diff-based
  idempotent reconciliation (add/remove eferences edges), dangling-link
  retention as 
ote:<Title> edges, ambiguity left unresolved with a warning.
- **cozmo/brain/storage/relationship_store.py**: emove(), list(kind=),
  has(), in-batch de-dup, INSERT OR IGNORE, and a best-effort unique edge
  index (degrades to a warning on pre-existing duplicates instead of crashing).
- **cozmo/brain/brain.py**: creation-time link materialization now resolves to
  durable Brain identities (M2's 
ote:<Title> preserved only when dangling);
  new sync_wikilinks(), acklinks(), 
eighborhood(); econcile_markdown
  runs the full diff (add/remove stale, orphan sweep on deleted/churned source
  notes, dangling recovery when a target note returns). Alias text is
  presentation-only and resolves to the canonical target.
- **	ests/test_m3_wikilinks.py** (new, 30 tests): resolution
  (exact/canonical/normalized/path/note-identity/alias/ambiguous/unresolved),
  relationships (outgoing/incoming/backlinks/dedup-deletion/stale-cleanup/
  provenance-preservation), lifecycle (dangling?created?dangling/deleted/
  recreated/source-deleted/idempotent), Obsidian compatibility
  ([[Title]]/[[Title|Alias]]/frontmatter/user links preserved), and retrieval
  preparation (lookup returns real kn- identities; traversal uses one store).

Suite: 30 new M3 tests green; 1483?1523 (+40 net) full suite, 0 failures, no
regression. LanceDB remains derived; no model fallback; no CWD-relative
knowledge paths; no LangGraph added to Brain.

---

## 2026-08-21 - M4: WikiLink-Aware Retrieval Expansion

M4 adds an additive retrieval stage: when semantic retrieval fails the
sufficiency gate, a bounded, deterministic WikiLink neighborhood walk
(`references` + backlinks through the existing RelationshipStore) discovers
neighboring knowledge and appends it to the existing result stream. No new
result type, no graph database, no second store, no ranking change.

- **cozmo/brain/reasoning/expansion.py** (new): pure traversal core.
  `traverse()` = BFS over injected `neighborhood` callables; bounded by
  `ExpansionConfig` (depth=1, max_neighbors=8, hop_decay=0.5), cycle-safe via
  visited-set, deterministic (sorted candidate ids per node), dangling
  `note:<Title>` targets skipped, callable failures degrade to no-edges.
- **cozmo/brain/reasoning/resolver.py**: new optional constructor callables
  (`neighborhood` / `fetch_knowledge`) + `expansion` config. When both
  semantic layers fail the gate, seeds (scoped+global hits, durable-id dedup)
  feed the walk; discovered neighbors enter the normal `RecallItem` stream
  tagged `origin="wikilink"`/`hops`/`via` with parent-decayed scores;
  plan gains `graph_items`, layer `"graph"`, gate `"graph"`. Zero
  discoveries fall through to the conversation/memory fallback byte-identically
  to pre-M4. Unwired callables disable the stage entirely.
- **cozmo/brain/brain.py**: default resolver wires `Brain.neighborhood` +
  new `knowledge_items(item_ids)` fetcher (durable-id lookup through the
  knowledge layer; missing/deleted ids skipped silently).
- **cozmo/memory/knowledge_index.py**: chunk metadata now carries the note's
  frontmatter `id` as `item_id` - the dedup bridge between path-chunked
  semantic rows and graph neighbors by durable identity.
- **cozmo/runtime/sources/knowledge.py**: Brain-backed sources expand gated
  (best score < 0.4 sufficiency threshold) through the same traversal core,
  appending `RetrievedItem` neighbors tagged `origin="wikilink"`,
  deduped by durable id (+ content-level fallback for pre-M4 rows), never
  touching/reordering semantic results, never raising into retrieval.
  Plain-index sources unchanged.
- **tests/test_m4_graph_retrieval.py** (new, 34 tests): sufficiency gating,
  outgoing/backlink traversal, hop/max-neighbor bounds, cycle termination,
  durable-id dedup, dangling/deleted/fetch-failure safety, deterministic
  order + score decay, legacy-equivalence when edgeless/unwired, scoping
  (incl. documented cross-scenario post-gate semantics), resolver pipeline
  integration, real-store Brain.recall end-to-end, source-level matrix.
- **tests/evaluate_retrieval_expansion.py** (new): baseline vs expanded
  measurement harness (12 linked clusters + 12 distractors, controlled weak
  semantic hits isolating graph contribution). Results:
  relevant recall 0.33 -> **1.0** (both surfaces); query success 0/12 ->
  **12/12**; irrelevant introduced 0; duplicate rate 0.0; context grows
  12 -> 36 items (~400 -> ~1500 chars) only where links exist;
  expansion adds ~21 ms mean per gate-failing query (per-neighbor
  VectorStore.get fetch dominates - optimization explicitly deferred).

Suite: 34 new M4 tests green; 1523 -> 1557 full suite, 0 failures. LanceDB
remains derived; RelationshipStore remains the only relationship store;
LangGraph untouched; model selection/provider behavior untouched.

Known limitations (deferred): graph fetch may surface superseded items
(matches current index semantics; filtering is a ranking decision);
cross-scenario neighbors reachable post-gate (same regime as global
expansion - pinned by test); un-reindexed chunks lack `item_id` until
their next mtime-triggered re-index (text-level fallback covers overlap).

---

## 2026-08-21 - M4.1: Graph Retrieval Hardening

Three validated fixes on top of M4 - no ranking redesign, no ResultMerger,
no storage-model change.

- **cozmo/brain/storage/vector_store.py**: new `get_many(item_ids)` - one
  indexed `id IN (...)` scan per batch instead of one zero-vector ANN probe
  per neighbor. Missing ids omitted, junk ids ignored, never raises.
- **cozmo/brain/brain.py**: `knowledge_items()` now fetches through
  `get_many` (per-id fallback for stores without it), deduplicates input
  ids, and filters SUPERSEDED items at the Brain boundary - superseded claims
  can no longer re-enter retrieval through the graph on either surface
  (resolver and runtime source share this path).
- **cozmo/brain/reasoning/resolver.py**: graph neighbors carry advisory
  `scenario_affinity` metadata ("same" only when an active scenario matches
  the neighbor's owner; "cross" otherwise, including no-active-scenario).
  Traversal still crosses scenarios freely - the future ranking layer can
  prefer same-scenario neighbors without hard-blocking global knowledge. The
  source surface has no query context, so it emits no affinity key rather
  than fabricating one.
- **tests/test_m4_graph_retrieval.py** (+9 = 43): batch lookup + parity with
  per-id get(), batching actually bypasses store.get(), superseded filtered
  in knowledge_items / recall e2e / source surface, affinity same/cross/
  absent-without-context, cross-scenario traversal preserved with "cross"
  affinity pinned.

Evaluation baseline preserved exactly (relevant recall 1.0, query success
12/12, 0 irrelevant introduced, duplicate rate 0.0); expansion latency drops
~21 ms -> ~7 ms mean per gate-failing query from the batched fetch.

Suite: 43 M4/M4.1 tests green; 1557 -> 1566 full suite (+9), 0 failures.

---

## 2026-08-21 - M5: Unified Retrieval + Graph-Aware Context Assembly

M5 consolidates the partially-overlapping ranking paths behind one pipeline:
filter - normalize - dedup (durable identity first) - unified ranking -
context-budget selection. Migration only: Brain/Markdown/RelationshipStore/
LanceDB/embeddings untouched; no LangGraph; no model-boundary change;
`_rank_memories` and `search_with_importance` remain ACTIVE (memory
prompt-context ranking and KnowledgeIndex candidate fetch) and are explicitly
deferred to the legacy-removal stage.

Audit result (source of truth = repo): active ranking paths were
`RetrievalExecutor._rank_memories` (freq x recency x distance), LanceDB
`search_with_importance` (relevance x recency x frequency), KnowledgeIndex
hybrid+cross-encoder rerank, LayeredRetrievalResolver (gate/tier/graph),
M4 source-level expansion with its own gate, and an UNWIRED Phase 9.5
ResultMerger. M5 wires that merger as the single merge/rank/dedup/budget
component instead of creating a second one.

- **cozmo/runtime/result_merger.py**: superseded-status filtering at the
  candidate boundary (metrics: filtered_superseded); strict durable-identity
  dedup (`item_id` / kn-prefixed `id`) - text similarity NEVER merges
  distinct knowledge; identity-less candidates keep the Phase 9.5 content
  rule byte-compatibly; bounded ranking deltas on the pinned base formula
  (documented in-module): delta = w_mem*confidence*status + w_aff*same-
  scenario - w_hop*(hops-1)/2 with defaults 0.05/0.06/0.15 - near-tie
  reordering and deep-hop demotion only, never outweighing semantic hits;
  every component recorded per item for evaluability; new `select()`
  minimum-sufficient context walk (char/item budget + query-term coverage
  early stop); origin normalization from source kind.
- **cozmo/runtime/unified_retrieval.py** (new): UnifiedRetriever composition
  root - parallel-ish discovery over injected SourceBindings, per-source
  latency/quality/contribution metrics, gate observability
  (sufficient_semantic vs expanded_graph via wikilink origins), merger +
  selection timings. Web/file deliberately excluded (evidence-pipeline
  semantics differ; documented integration point for later).
- **cozmo/runtime/retrieval.py**: `retrieve_knowledge` now routes through
  the unified pool (knowledge+graph merged with memory/project when wired);
  single-source bindings keep the pre-M5 byte-identical path; any unified
  failure falls back to legacy behavior.
- **cozmo/runtime/sources/knowledge.py**: graph neighbors now carry
  `item_id` durable-id metadata (dedup bridge parity with indexed chunks).
- **tests/test_m5_unified_retrieval.py** (new, 38 tests): superseded filter,
  identity dedup matrix (chunks-of-note / graph twin / cross-surface recall
  row / distinct-ids-kept-apart / legacy content fallback), delta ranking
  (base-formula unchanged, confidence lift, affinity bonus with neutral
  cross, hop demotion, determinism), budget selection (coverage early stop,
  char cap, max_results cap, empty-query fill, graph competes fairly,
  immutability), retriever composition/metrics/degradation/order-independence,
  executor integration incl. legacy fallback, architecture purity guards.
- **tests/evaluate_retrieval_expansion.py**: M5 arm added measuring FINAL
  selected context + context_efficiency, superseded_leakage,
  ranking_latency_mean_ms columns; corpus plants one linked superseded twin
  per cluster.

Evaluation (12 clusters + 12 distractors): baseline recall 0.33/success 0/12;
expanded 1.00 / 12-12; M5 unified selected context keeps recall 1.00 and
12/12 success at identical 36 items / 1482 chars with ZERO superseded
leakage, zero duplicates, ranking overhead ~0.09 ms/query, total ~10.5 ms.
Full suite: 1566 -> 1604 (+38), 0 failures.

---

## 2026-08-21 - Post-M5 Audit + Retrieval Hardening (superseded leak closure)

Read-only audit of the post-M5 repository traced all retrieval entry points,
ranking/dedup/budget paths, Brain-Markdown sync, reconciliation, WikiLink
synchronization, conversation persistence, legacy MemoryManager paths,
EvidenceProcessor, and LangGraph boundaries. Findings classified; one A-class
blocker found and fixed.

**Audit highlights**: LangGraph confined to cozmo/graphs (research/coding
StateGraphs, no memory role); ModelUnavailableError propagation intact end to
end; ConversationStore confined to brain/ + composition root; EvidenceProcessor
mature but unwired into the live search path (deferred - parity unproven);
_rank_memories / search_with_importance remain active legacy (parity not yet
proven - untouched per migration discipline).

**A-class finding - superseded leakage through the semantic primitive**:
`VectorStore.query` returned SUPERSEDED rows; only the optional tiering flag
masked them, and knowledge-index chunks carried no status at all. Fixed at the
read boundary, append-only intact:

- **cozmo/brain/storage/vector_store.py**: `query()` excludes superseded by
  default (NULL-status legacy rows stay eligible); `include_superseded=True`
  is the explicit history/audit escape hatch mirroring tier_hits. Point
  lookups (get/get_many) stay policy-free - Brain owns fetch policy.
- **cozmo/memory/knowledge_index.py**: chunk metadata mirrors frontmatter
  `status` at index time; `search()` drops superseded rows. Pre-mirror
  rows pass through until their next mtime-triggered re-index (self-healing).
- **tests/test_m6_retrieval_hardening.py** (new, 10 tests): store exclusion +
  escape hatch + live supersede reflection + history preservation + point-
  lookup neutrality + signature pin; index drop/pass-through/reindex-heal;
  non-tiered resolver e2e (the old leak regime) + graph fetch boundary.

Evaluation baseline unchanged (recall 1.00, success 12/12, zero leakage/dups).
Full suite: 1604 -> 1614 (+10), 0 failures.

Deferred (B/C): EvidenceProcessor wiring (needs live-path parity fixtures),
_rank_memories/search_with_importance removal (legacy stage), LangGraph
dual-path StateGraph (next major stage once evidence path settles).

---

## 2026-08-21 - EvidenceProcessor Live-Path Integration (parity-first)

Read-only audit traced both evidence paths end to end. Current live path:
`EvidenceCollector.collect` -> raw `merged_text` ("**Source i**" blocks,
top 5 x ~3000 chars, URLs+titles included) -> `ctx.grounding_text` -> system
prompt verbatim (~up to 15k chars). Processor path existed but was consumed
only by the evaluation harness. The migration seam was already designed:
`ExecutionContext.evidence_context` sat unwired ("never set by runtime").

Integration (no new frameworks; processor/extractor/ranker extended in place):

- **cozmo/evidence/rendering.py** (new): `render_evidence_context` - the
  missing parity half. Deterministic model-facing renderer that preserves what
  raw merged_text guaranteed (source titles + URLs, relevant content, input
  ordering) and adds confidence header, per-fact `[Sn]` source attribution,
  and explicit conflict/resolution surfacing. Returns "" for fallback
  contexts so callers keep raw behavior.
- **cozmo/evidence/processor.py**: duplicate-URL sources now collapse at
  build time (same source collected twice is one source - never inflates
  corroboration).
- **cozmo/runtime/retrieval.py**: new `_apply_web_evidence` wired into all
  four web-search sites. Default unchanged (raw text lands first); processing
  is best-effort - trusted non-fallback contexts upgrade the grounding to the
  rendered structured form, fallbacks keep byte-identical raw text, and any
  processor exception keeps raw grounding with `evidence_context` unset.
  KB-text grounding paths untouched; web evidence still never reaches Brain/
  knowledge storage.
- **tests/test_evidence_parity.py** (new, 20 tests): 12-category fixture
  matrix (clean multi-source, duplicates, conflicts, confidence tiers,
  temporal, irrelevant, empty, malformed, repeated queries, mixed quality,
  determinism, failure injection) asserting URL/title/content parity between
  current and processed pipelines, plus executor gating/degradation tests and
  architecture guards (evidence package purity: no LangGraph/storage/model
  selection; no Brain persistence references).

Measured (5x~7k-char sources): raw 7344 chars -> processed 381 chars rendered,
processing latency ~1 ms. Full suite: 1614 -> 1634 (+20), 0 failures.

Next LangGraph blocker identified: none in evidence - the research StateGraph
(`cozmo/graphs/research_graph.py`) can now consume structured evidence with
identical semantics; remaining pre-migration work is the dual-path runtime
scaffold itself plus legacy retrieval parity proof (_rank_memories /
search_with_importance removal stays deferred).

---

## 2026-08-21 - LangGraph Dual-Path Runtime Migration

General runtime workflow graph added behind an opt-in engine flag. Legacy
run_stream remains the default and is byte-inert when the graph is wired but
the engine stays "legacy".

- **cozmo/graphs/runtime_graph.py** (new): RuntimeWorkflowGraph - explicit
  analyze -> retrieve -> reason -> act(bounded loop) -> reflect -> answer
  StateGraph following the established research/coding injection pattern.
  Retrieve node snapshots context the runtime's RetrievalExecutor/
  UnifiedRetriever/evidence pipeline ALREADY produced (zero retrieval logic
  in-graph); every tool call routes through ToolExecutor via the injected
  execute_tool collaborator; model arrives already-bound (ModelUnavailableError
  propagates untouched - never caught); reflect is opt-in (workflow.reflect_on_run,
  default off = parity with observe-per-turn semantics). No checkpointer:
  Brain/ConversationStore/JobStore remain the only durable authorities.
  Legacy-parity details pinned: thinking/tool_call/tool_result/token event
  vocabulary+order, exact-call dedup message, max-steps wording, empty-output
  fallback string.
- **cozmo/graphs/state.py**: additive RuntimeState (events, seed_messages,
  seen_calls, observations, collaborators). ResearchState/CodingState untouched.
- **cozmo/runtime/runtime.py**: runtime_graph + workflow_engine ctor params;
  langgraph branch mirrors the one-logical-step plan lifecycle of the other
  graphs; _runtime_graph_state builds per-run collaborators incl. context
  snapshot + ToolExecutor-gated execute_tool.
- **cozmo/services/context.py / webui_server.py**: graph constructed eagerly;
  engine selected from runtime.workflow_engine ("legacy" default).
- **tests/test_runtime_workflow_graph.py** (new, 21 tests): no-tool flow,
  continuation seeding, context snapshot verbatim, single/multi tool rounds,
  event order, dedup message, max-steps bound + wording, MUE + arbitrary-error
  propagation, tool-failure-as-observation, cancellation, reflect-on-success-
  only, determinism, two live run_stream integration tests (token streaming +
  tool replay through a stubbed ToolExecutor), legacy-default inertness, and
  package purity guards (no storage/model-selection/checkpointer).

Validation: graph suite 21/21; adjacent graph/architecture/runtime/webui
suites 93 passed; full suite minus the externally-added in-flight
test_phase8b_research.py: 1684 passed, 0 failures.

EXTERNAL CONCURRENT WORK (not this stage): tests/test_phase8b_research.py +
graphs/research_intel.py landed mid-stage from another workstream (Phase 8B
research decomposition/citations). Several of its assertions currently fail
because ResearchGraph integration for decompose/manifest/truncation is still
being implemented by that stream - failures are orthogonal to this migration
(zero overlapping code paths; verified by failure signatures). Left untouched
deliberately to avoid colliding with active edits.

---

## 2026-08-21 - LangGraph Cutover Evaluation + Legacy Runtime Parity

Structured parity harness (`tests/runtime_parity_harness.py` +
`tests/test_runtime_parity.py`, 7 tests) drives legacy and
`workflow_engine=\"langgraph\"` runtimes over 15 representative workloads
(chat, memory/knowledge/project retrieval, research/web, evidence processing,
tool call, multi-tool loop, continuation, tool-gate failure, model
unavailable, cancellation, insufficient retrieval, graph expansion,
max-steps exhaustion) with scripted deterministic models/tools and a
recording Brain.

Result: ZERO behavioral differences across model identity, conversation id,
tool call order/failures, Brain observations, history length, stop reason,
final text, errors, cancellation, and non-token event vocabulary. The one
legitimate granularity difference (legacy streams token deltas via
`runnable.stream`; the graph replays the final token) is classified
intentional and documented. Latency is same-magnitude on every workload.

Regressions found and fixed during cutover: my PowerShell-based default flip
had corrupted `services/context.py`/`webui_server.py` encodings (BOM +
double-encoded UTF-8 punctuation), breaking two trace-boundary file-read
tests; repaired via surgical cp1252-inverse restoration - functional code
was never affected.

Cutover decision: **workflow_engine now defaults to \"langgraph\"** (minimal
switch: two default literals in the composition roots). Explicit escape hatch
retained: set `runtime.workflow_engine = \"legacy\"` in configuration or
pass `CozmoRuntime(workflow_engine=\"legacy\")`. Full suite GREEN under the
new default: **1778 passed, 0 failures**, confirmed stable across consecutive
runs; parity/graph/retrieval targeted suites 71 passed.

Legacy retained untouched per migration discipline: `_rank_memories`,
`search_with_importance`, MemoryManager fallbacks, and the entire legacy
run_stream ReAct path remain active escape-hatch surfaces for the later
removal stage.

---

## 2026-08-22 - Post-Cutover Stabilization + Legacy Retirement Audit

Phase 0 baseline re-verified from clean checkout state: full suite **1778
passed, 0 failures**; parity harness rerun over all 15 workloads x both
engines -> **0 behavioral differences** across model identity, conversation
id, tool calls/failures, Brain observations, history, stop reasons, final
text, errors, cancellation, and non-token event vocabulary (token-chunk
granularity remains the one intentional difference). Composition roots
(webui_server.py, services/context.py) confirmed to default
runtime.workflow_engine="langgraph".

Every listed legacy component was call-site audited before any removal:

- Legacy ReAct branch / `_run_agent_loop`: RETAINED. `_run_agent_loop` has a
  live production caller beyond the escape hatch - CodingGraph's injected
  `run_loop` executes each implement attempt through it. Removing it would
  require re-platforming the coding workflow onto RuntimeWorkflowGraph (an
  architecture project, out of scope per stage rules).
- `_rank_memories`: RETAINED. Sole memory-context ranking
  (frequency x recency x (1-distance)); ResultMerger has no frequency/recency
  semantics, so rerouting through it changes prompt-context ordering instead
  of proving parity. Documented as known duplicate-ranking debt alongside
  LanceStore.search_with_importance's own importance formula.
- `search_with_importance`: RETAINED. Store-level candidate-fetch primitive
  with live canonical consumers (KnowledgeIndex fetch_k, MemoryManager.query).
- MemoryManager / get_memory_manager: RETAINED. MemoryManager is Brain's
  storage engine (recall fallback, learn->store_fact, consolidate), the
  WebUI /api/memory backend, agent_memory write path, no-brain fallback, and
  migrations source. Removal is Phase G roadmap work, not this stage.

Retired (each audit-proven zero production callers, removed ONE AT A TIME
with targeted + full-suite verification between):

1. `Brain.retrieve_memory_rows` flat compat adapter (brain.py) - superseded
   by MemoryRetrievalSource reading Brain.recall directly; ROADMAP-phaseG
   item 1.5 closed. Its two adapter tests removed with it.
2. `MemoryManager.store_project_context` dead method (manager.py).

New architecture guards in test_architecture.py:
test_no_retired_retrieve_memory_rows_adapter,
test_no_retired_store_project_context_method,
test_composition_roots_default_langgraph_engine.

Results: baseline 1778 passed -> after retirements+guards **1779 passed**
(1776 after removing the 2 retired-adapter tests, +3 guards), 0 failures;
parity suite 7/7; runtime workflow graph suite green; full parity matrix
rerun at stage end still 0 differences.


---

## 2026-08-22 — Phase 8 Post-Implementation Remediation

Read-only audit of the shipped Phase 8 (8A–8G) surfaced eight behavioral
hypotheses. Each was verified against source and reproduced before any fix;
only confirmed defects were changed. New regression coverage lives in
`tests/test_phase8_remediation.py` (40 tests).

Confirmed and fixed:
- **A — refinement lost entities**: `refine_query` only padded an anchor when a
  single gap existed, so two uncovered terms degraded "Tesla 2024 revenue" to
  "revenue 2024". Refinement is now entity-first, derives anchors from the
  ORIGINAL question, prefers timeframes when padding, hard-capped at
  MAX_REFINED_TERMS=3.
- **B — silent budget starvation**: 3 decomposed sub-questions with
  max_search_attempts=2 left the third unsearched with no trace. The graph now
  records `coverage_incomplete` + `unresearched_questions`, emits a
  `coverage_incomplete` stream event, injects a bounded COVERAGE WARNING into
  synthesis, and flags `validation_detail`.
- **C — narrow insufficiency detector**: "I don't have reliable information",
  "I am not certain", apostrophe variants etc. now match via normalized
  deterministic phrase families; ordinary hedging still never counts.
- **D — starved conflict pipeline**: collect_conflicts detected per-bundle, so
  cross-source contradictions were structurally impossible. Detection now runs
  once over combined extracted facts (≤40). ConflictDetector gained a
  conservative same-template numeric rule (years excluded; skeletons must match
  exactly).
- **E — fake green verification**: zero executed verification commands reported
  passed=True/verifications=0. Added structured statuses verified/failed/
  unavailable/skipped; zero commands → unavailable, honest error record,
  no repair.
- **F — timeout misclassification**: timed_out was classified as an
  implementation failure and triggered code repair. Timeout is now its own
  class, terminal, no repair (slow suite/deadlock/infra ≠ code defect).
- **G — tautological evaluation provenance**: drivers now disclose
  driver_mode scripted|live and staged_repair; CodingMetrics gained
  staged_repair_rate (CLI prints it); RegressionDetector compares research/
  coding families.

Not changed (verified acceptable / parallel work): decomposition gating (H),
ToolExecutor permission/risk pipeline, workspace confinement, model boundary,
persistence boundary. Incidental repairs: double-encoded box-drawing comments in
webui_server.py/services/context.py restored to UTF-8, DEVLOG normalized to
valid UTF-8, one merged line repaired in useCozmoChat.ts (+ honest labels for
the new verification_unavailable / coverage_incomplete phases).

Suite after remediation: pytest 1819 passed (was 1778), vitest 127 passed,
tsc+build clean, evaluation CLI research/coding/analyze run clean.
