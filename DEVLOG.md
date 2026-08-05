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