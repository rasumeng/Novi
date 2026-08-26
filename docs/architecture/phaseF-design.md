# Phase F — Cognitive Completion Design

**Status:** Design (implemented in Brain V1, 2026-08). Companion to `docs/architecture/brain-architecture.md` and `docs/archive/phaseC-blueprint.md`.

**Constraint:** This is a *cognitive completion* pass, not a storage redesign, not AGI research, not a new abstraction layer. Every proposal is anchored to the architecture that already exists (`novi/brain/`), and deferred work is listed explicitly.

---

## 1. Purpose and Scope

### 1.1 Purpose

Close the loop between "store and retrieve" and "understand what matters and how knowledge should evolve." Today Novi observes conversations, extracts atomic claims, groups them into scenarios, and retrieves them through a layered resolver. What is missing is the *cognitive layer* that turns those raw claims into a durable, self-maintaining model of the user: what remains true, what changed, what is important, and what should fade.

The end state is not generality. It is a **reliable personal assistant**: Novi knows the user's stable preferences, tracks active work, surfaces the right knowledge for a question, and quietly updates itself over months of use without manual curation.

### 1.2 Scope — IN

- Memory consolidation: repeated observations → durable knowledge (uses existing `KnowledgeStatus` lifecycle).
- A **bounded** reflection process (extends the existing `Brain.reflect()`).
- Knowledge evolution: confidence, contradiction handling, temporal awareness (extends existing statuses + `supersedes` edges).
- Personal context model: a derived, read-only user projection from existing Identity tags + Scenarios + Projects.
- Retrieval intelligence: reweight existing resolver scoring by importance × confidence × scenario relevance × recency (no new ranker).
- Forgetting / decay: priority reduction + archival, never physical deletion.

### 1.3 Scope — OUT (explicitly)

- No new storage systems. Reuse `VectorStore`, `ScenarioStore`, `ConversationStore`, `RelationshipStore`.
- No rewrite of the layered resolver, extractor, or scenario layer.
- No AGI: no self-improvement loops, no meta-learning, no autonomous goal-setting, no long-running background "thinking."
- No new production summarization pipeline (the `MemoryManager` brain=None fallback stays untouched — see §14 risk).

---

## 2. Cognitive Goals

| Goal | Meaning | Measured by |
|---|---|---|
| **G1 — Forget nothing, learn efficiently** | Claim confidence grows with corroboration, never by volume alone. | status distribution; promotion rate per scenario |
| **G2 — Track change honestly** | Contradiction produces a typed `supersedes` edge + historical preservation, never silent overwrite. | supersedes edge count; no untracked content mutation |
| **G3 — Stay current** | Retrieval prefers important, verified, scenario-appropriate knowledge over merely recent knowledge. | retrieval precision on importance probes |
| **G4 — Be usefully personal** | Stable preferences/goals are surfaced; transient facts decay so the profile stays clean. | profile projection accuracy on preference probes |
| **G5 — Bounded behavior** | Reflection is deterministic, budget-limited, and runs on defined triggers — not on a clock that surprises the user. | reflection cost (items examined) < constant |

The north star: **"A local AI assistant that understands the user, remembers important information, maintains continuity, and improves assistance over time."**

---

## 3. Cognitive Boundaries

Novi must distinguish **observed facts** from **strongly supported conclusions** from **speculative assumptions**, and must not silently manufacture memories from weak inference.

### 3.1 Evidence grading

Every candidate item is classified against an evidence grade at extraction/consolidation time:

| Grade | Definition | Confidence floor | Stored as |
|---|---|---|---|
| **Observed fact** | Stated directly by the user ("I prefer local AI systems", "I use TUI"). | ≥ threshold | `CANDIDATE` → promote on confirmation/repetition |
| **Strongly supported conclusion** | Derived from multiple independent observations pointing the same way. | high, requires ≥2 corroborations | promoted only via corroboration |
| **Speculative assumption** | Single mention, hedged, or inferred absent explicit statement. | low | kept as `CANDIDATE`, allowed to decay; never promoted |

### 3.2 Inference policy

- **Explicit beats inferred.** A directly stated preference (an explicit-confirmation match, `verification.is_confirm`) outranks any inferred one.
- **One mention ≠ fact.** A single utterance ("I was up late last night") is a `CANDIDATE`, not a durable preference ("Robert prefers working late"). It only becomes durable through repetition or explicit restatement.
- **No silent synthesis.** The projection's rollups ("Novi — local AI assistant") are derived grouping of *stated* items, never fabricated attributes. The projection never invents a claim Novi was not told.
- **Uncertainty is visible.** Each item carries a `status` + `confidence`; consumers and the trust surface can tell fact from assumption at a glance.

### 3.3 What Novi should and should not infer

- **May infer:** sustained patterns from repeated, explicit evidence (preference, tool usage, project focus).
- **Must not infer:** moods, personality, health, finances, relationships, or "working late" style habits from isolated mentions.

---

## 4. User Trust Model

A personal assistant must remember correctly *and* responsibly. Trust rests on three pillars: **automatic-with-restraint**, **inspectable**, and **correctable**.

### 4.1 What is stored automatically

| Level | Stored | Mechanism |
|---|---|---|
| Always | Observed facts + conversation turns | existing `KnowledgeExtractor` → `CANDIDATE` |
| After confirmation | Preferences / goals / skills | `is_confirm` → `VERIFIED` instantly |
| After corroboration | Strongly supported conclusions | ≥2 corroborations → `CORROBORATED`/`VERIFIED` |
| Never | Speculative assumptions as durable fact | locked at `CANDIDATE`; decays |

### 4.2 Autonomous writes require stronger confidence

- Single weak mention → `CANDIDATE` only.
- Only repeated or explicit evidence advances to `VERIFIED`.
- Contradictions / supersessions are always recorded as edges (history preserved), never silent overwrite (§9.2).

### 4.3 Inspection

Provide a **memory inspection surface** (a tool/subcommand backed by the read path, no new store):

- List what Novi remembers, grouped by category (projection output).
- Show each item's `status`, `confidence`, `last_seen_at`, and source conversation.
- Show supersession/conflict history (edge endpoints).
- Reveal which items were **inferred** vs **explicit** vs **decayed**.

### 4.4 Correction

- A user can **demote/supersede/archive** any item explicitly (reuses `update_status` + `supersedes`/`conflicts_with` edges).
- A user correction is treated as strong evidence: it outranks corroboration history going forward.
- Corrections are append-only — the corrected item is `SUPERSEDED`, a new item records the correction; nothing is deleted.

### 4.5 Transparency of updates

- Memory writes are **on the write path only** — no invisible background persistence of new *claims* (reflection only re-grades existing items; it never invents claims).
- Promotion/demotion/supersession is auditable via the status field and the event vocabulary (`knowledge.promoted`, `identity.updated`), all "emit after persistence."

---

## 5. Retrieval Priority Refinement

Recency is **not** a proxy for importance. A rarely-mentioned, stable preference matters more than a recently discussed temporary task. The resolver therefore ranks by a fixed priority hierarchy and treats recency only as a tiebreaker within an equal tier — not as a multiplicative co-equal.

### 5.1 Priority order

1. **Importance** — intrinsic salience (`importance`, set by promotion; preference/goal/identity tags; scenario-anchored).
2. **Confidence** — `status` + `confidence` (verified > corroborated > candidate).
3. **Scenario relevance** — the resolver already scopes to the active scenario neighborhood (top-down walk).
4. **Recency** — `last_seen_at`, used **only to break ties** within an equal (importance, confidence) group.

### 5.2 Refined scoring

Replace the earlier multiplicative form with a **lexicographic tiering** inside `query_knowledge`:

```
tier_importance = bucket(importance)            # high / med / low
tier_confidence = bucket(status)                # verified / corroborated / candidate
tier_relevance  = in_active_scenario ? upper : lower
score = compose(tier_importance, tier_confidence, tier_relevance)
       then recency breat tight within equal tiers
```

This keeps the change cheap and deterministic (bucketing, no learned model), preserves the sufficiency gate, and guarantees a stable preference is not outranked by a fresh passing topic. `superseded` items are excluded unless explicitly requested.

---

## 6. Proposed Architecture Changes

All changes are additive to existing modules. No new module graph, no new storage.

### 6.1 Extend the knowledge model (existing types only)

`KnowledgeItem` already has `confidence`, `status`, `tags`, `scenario_id`, `created_at`. Two additive fields are proposed to support evolution and decay. They default to safe values so existing rows/consumers are unaffected:

- `last_seen_at: datetime` — last time this claim was corroborated. Defaults to `created_at` on insert; drives recency tiebreaking and decay.
- `importance: float` — normalized 0..1 salience for the *projection* ranks. Default `0.0` when unset (feature only for items the reflection pass promotes into the personal context).

No new enum values are required on the critical path: `KnowledgeStatus.CANDIDATE/CORROBORATED/VERIFIED/SUPERSEDED` already encode the lifecycle. `EdgeKind.CONFLICTS_WITH` and `EdgeKind.SUPERSEDES` already exist — Phase F wires them into decision logic.

### 6.2 Consolidation pass (extend `Brain._reflect_knowledge`)

The mechanism already exists (`promotion.decide` + `verification.corroboration`). Phase F adds:

1. **Merge-before-promote:** when corroboration finds near-duplicates, do not create siblings. The existing decide flow marks one winner `VERIFIED`; keep the corpus clean by *not* re-adding identical claims at extraction time (§7.2).
2. **Decay-aware demotion:** when `VERIFIED`/`CORROBORATED` items grow stale (`last_seen_at` older than a configurable horizon with no new corroboration), demote to `CANDIDATE` — lowering retrieval priority without deleting.
3. **Conflict detection:** on promote, compare against *verified* items marked `CONFLICTS_WITH` and write a typed edge instead of overwriting.

### 6.3 Bounded reflection coordinator (new, thin)

A small pure module, `novi/brain/reasoning/reflection.py`, guards `Brain.reflect()`:

- **Budget:** process at most `N` candidate items per pass (default ~200). Deterministic ordering (oldest `last_seen_at` first). Protects against unbounded LLM/scan cost and against runaway growth.
- **Trigger gating:** assistant decides *whether* a reflection pass is warranted from cheap signals (§8.2). It does not create a new background subsystem.
- **Pure:** reads Brain objects via injected callables, returns a `ReflectionReportExtension`; the Brain applies statuses/edges. Mirrors the existing `promotion`/`verification` purity contract.

### 6.4 Personal-context projection (derived read, no store)

A read-only view over existing data — not a new store. Built on demand from:

- **Identity-layer items** (already tagged `preference`/`goal`/`skill`/`identity`).
- **Active Scenarios** (already exist) and their project anchors.
- **Projects** (already exist).

Projection groups items by category and ranks by the §5 hierarchy (importance → confidence → scenario → recency). Because it is derived, it cannot fall out of sync with the store, and it costs nothing when unused. It **never invents** attributes — it groups stated items only.

### 6.5 Retrieval reweighting (extend resolver scoring)

`LayeredRetrievalResolver` currently scores by vector similarity with a sufficiency gate. Phase F applies the §5 **lexicographic tiering** inside `query_knowledge` — importance, then confidence, then scenario relevance, then recency-as-tiebreak (bucketed, deterministic, no learned model). See §5.2 for the exact form.

### 6.6 Decay scheduler (reuse existing `Scheduler`)

Decay is a *read-time* reweight plus an *occasional* consolidation demotion — it does **not** require a new daemon. A user-visible action (scenario completion, explicit invocation, or the existing scheduler tick) runs `reflect()`; a read query always applies recency reweighting inline. This keeps forgetting correct on the read path and avoids surprising background writes.

---

## 7. Memory Lifecycle

### 7.1 States

```
                    confirm / N corroborations
  CANDIDATE ───────────────────────────────▶ CORROBORATED ──▶ VERIFIED
       ▲                                        │                  │
       └──────────(decay: stale, no new)────────┘       (contradiction)
                                                             │
                                                         SUPERSEDED  (preserved,
    raw Turn ─▶ extraction ─▶ CANDIDATE                        │      keep history)
    (no claim)      │                                   conflicts_with edge to new
               dedup ─ drop if near-dup exists
```

### 7.2 Consolidation rules

1. **Repeated observation** → corroboration. The *same content* does not create a second item; it advances `last_seen_at` and corroboration count on the existing nearest item.
2. **Duplicate at extraction** → drop. `_dedup` already removes exact/near duplicates within a batch and across `MemoryManager`; extend the same rule across the *verified* corpus (a new claim matching a `VERIFIED` item is a corroboration, not a new item).
3. **When does a claim become long-term knowledge?** When it crosses `CANDIDATE → VERIFIED` via either explicit confirmation (`verification.is_confirm`) or the corroboration threshold. That is the formal "durable" boundary.
4. **What is preserved?** Claims that are `VERIFIED`, or that carry a `preference`/`goal`/`skill`/`identity` tag. Everything else is allowed to decay.
5. **Short-term → durable:** the 5-turn buffered batch (legacy cadence, see `brain.py`) produces `CANDIDATE` items; consolidation over subsequent turns walks them to `VERIFIED`. No new buffer is introduced.

### 7.3 Example (from the prompt)

- "Robert is working on Novi."
- "Robert redesigned the memory system."
- "Robert wants Novi to become a personal assistant."

These are three separate extraction batches producing `CANDIDATE` items tagged `project`/`goal`. Corroboration (shared terms: *robert*, *novi*) advances them toward `VERIFIED`. The projection groups the goal-tagged item into the **Identity → Goal** bucket for "personal assistant," and the project-tagged items into the **Project: Novi** bucket. Storage still holds three atomic history rows; the *projection* synthesizes "Novi — local AI assistant, cognitive architecture focus, active development." No merge destroys history.

---

## 8. Reflection Lifecycle

### 8.1 What reflection asks

Each pass answers, for a budgeted set of candidate items:

- Did anything important happen? (new verified / promoted count)
- Did the user reveal a preference? (identity-tagged candidate → verified)
- Did a project change? (scenario status / summary drift)
- Did a goal change? (supersedes edge on a goal/identity item)
- Should existing knowledge be updated? (demotion from staleness, conflict resolution)

### 8.2 Triggers (cheap, deterministic)

Reflection is **not** on a surprise clock. Candidate triggers, in priority order:

1. **Scenario completion** — the user archives/completes a scenario (already a lifecycle event, `ScenarioStatus.COMPLETED/ARCHIVED`).
2. **Idle after activity** — reuse the existing `Scheduler` tick to run a *bounded* pass when no conversation is active, only if pending work exists (new candidate items since last pass).
3. **On demand** — explicit "reflect"/"consolidate" invocation (manual maintenance).
4. **Confirmation burst** — a run of explicit `is_confirm` claims triggers an immediate confirm-pass.

### 8.3 What it produces

A `ReflectionReport` (extends the existing `ReflectionReport`) with counts and, critically, a pure decision list:

- promotions, corroborations, superseded, decays, conflicts-detected
- which items were touched (ids) so the Brain can apply them and the event bus can emit a `knowledge.promoted` / `identity.updated` event.

### 8.4 Interaction with memory

Reflection writes only through the existing status/edge mutations (`update_status`, `add_many` on `RelationshipStore`). It never rewrites content, never deletes, and never bypasses the store. Extraction and reflection are strictly serialized through `Brain` so a reflection pass never races an in-flight extraction.

---

## 9. Knowledge Evolution Model

### 9.1 Confidence

- **Increase:** corroboration count and explicit confirmation (`verification.corroboration`, `promotion.decide`). One mention → `CANDIDATE`; repeated, confirmed, or multi-source evidence → `VERIFIED`.
- **Decrease:** staleness demotion (see §6.2) and the read-time recency reweight §6.5. Confidence is a *function of evidence + time*, not a stored opinion that only grows.

### 9.2 Contradictions

- **Handler:** On a newly `VERIFIED` claim that conflicts with an existing `VERIFIED` item, mark the old item `SUPERSEDED` and write `ConflictRelationship(source=new, target=old, kind=CONFLICTS_WITH)` (plus `SUPERSEDES`). The old row keeps its token history; retrieval excludes it.
- **No deletion.** "Robert uses a TUI" → "Robert migrated Novi to WebUI" = old `TUI` claim `SUPERSEDED` with a `supersedes` edge. Both facts remain inspectable; current state is derived from the newest non-superseded claim. The same rule makes user corrections append-only (§4.4).

### 9.3 Temporal knowledge

- **Current vs historical:** derived from `status != SUPERSEDED` AND recency tiebreak.
- **What changed:** the `supersedes`/`conflicts_with` edge endpoints give an explicit change record (old → new).
- **No longer relevant:** decay to `CANDIDATE`/low importance → excluded from the personal projection and deprioritized in retrieval, but still searchable on demand.

---

## 10. Data Flow Diagrams

### 10.1 Write / consolidate

```
Turn ─▶ Brain.observe
         └─▶ ConversationStore (persist, always)
         └─▶ buffer ─(5 turns)─▶ KnowledgeExtractor ─▶ CANDIDATE items
              (dedup against corpus) ─▶ KnowledgeLayer.store_extracted
                                        (provenance edges, scenario_id)

Reflect trigger ─▶ Brain.reflect
         └─▶ ReflectionPlan (budget N, oldest last_seen first)
         └─▶ corroboration → decide (promote/demote/supersede)
         └─▶ apply status + relationship edges (RelationshipStore)
         └─▶ emit knowledge.promoted / identity.updated
```

### 10.2 Read / retrieve

```
Query ─▶ Brain.recall ─▶ LayeredRetrievalResolver
         resolve(project→scenario) ─▶ load scenario
         query_knowledge(scenario-scoped, then global)
             └─▶ tier(importance) → tier(confidence) → tier(scenario) → recency-tiebreak
                 (superseded excluded)
         sufficiency gate ─▶ conversation/memory fallback
         └─▶ RecallResult → runtime formatter
```

### 10.3 Personal projection (derived, on demand)

```
Identify layer items + active scenarios + project anchors
   └─▶ group by category (preference/goal/skill/project/event/relationship)
   └─▶ rank: importance → confidence → scenario → recency-tiebreak
   └─▶ projection dict (read-only, never stored; never invents attributes)
```

---

## 11. What Belongs in Novi V1

| # | Feature | Anchored to existing code | Complexity |
|---|---|---|---|
| 1 | Consolidation: corroborate & dedup across corpus | `verification.py`, `promotion.py`, `_dedup` | Low |
| 2 | Bounded reflection coordinator | extends `Brain.reflect` | Low–Med |
| 3 | Conflict → `supersedes`/`conflicts_with` edge | `types.EdgeKind`, `RelationshipStore` | Low |
| 4 | Temporal awareness: current vs historical | status + recency tiebreak in resolver | Low |
| 5 | Retrieval priority tiering (imp → conf → scenario → recency) | `resolver.query_knowledge` | Low |
| 6 | Personal-context projection (derived read) | identity tags + scenarios + projects | Med |
| 7 | Forgetting = decay + archive (never delete) | decay pass + retrieval exclusion | Med |
| 8 | Events: `knowledge.promoted`, `identity.updated` | `events.py` vocabulary | Low |
| 9 | Memory inspection + correction surface | read path + `update_status` + edges | Med |

All nine extend what exists. None introduce a new storage system or a new background "cognition" service.

---

## 12. Deferred to Future Research

- **Self-improvement loops / meta-learning** — Novi does not rewrite its own extraction prompt based on outcomes.
- **Autonomous long-horizon goals** — scenario *regrouping* and *project anchoring from content* stays manual (already listed OUT in `phaseC-blueprint.md` §2).
- **Learned relevance ranking** — the reweight/tiering function is static; a learned model is future work, out of V1 scope.
- **Emotional / hedonic memory** — beyond "useful personalization," out of scope.
- **Federated or cloud-shared memory** — Novi is local-first.
- **Graph reasoning over the full relationship graph** — V1 uses edges for supersede/conflict provenance only; general path traversal is future work.
- **Adaptive forgetting heuristics** — decay horizons are constant in V1; personalization of decay is future work.

---

## 13. Implementation Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Scope creep to AGI-style reflection** | High | Fixed budget (`N` items/pass), pure coordinator, deterministic triggers, no background daemon. |
| **Forgetting reads as data loss** | Med | Forgetting is decay/archive only; never `DELETE`. Superseded + archived rows remain queryable. |
| **Regency misread as importance** | Med | Lexicographic tiering (§5) makes importance first; recency only breaks ties. Explicit test with a rare stable preference vs a fresh topic. |
| **Corroboration false-positives (conflicting claims merged)** | Med | Corroboration requires high token overlap (`verification.corroboration` ≥0.5 ratio); conflicts route to edges, not merges. Add conflict tests. |
| **Retrieval reweighting regresses existing recall tests** | Med | Tiering is monotone-separated from similarity; keep superseded-exclusion toggle; back-compat flag for current behavior; existing 740 suite is the safety net. |
| **Scheduler-triggered reflection surprises the user** | Low | Reflection only runs on defined triggers (scenario completion / idle with pending work / on demand / confirm burst); no spontaneous mid-conversation writes. |
| **Loss of memory inspection/correction trust** | Med | Backed by read path + `update_status` + edges; user correction outranks corroboration history; audit via events. |
| **`MemoryManager` brain=None fallback divergence** | Low | The no-brain legacy pipeline is untouched this phase (deliberately deferred). Consolidation lives in Brain layers only; flat `query()` behavior is unchanged. |
| **History growth / archive bloat over months** | Med | Archived rows are pointer-light (status + edge); projection is derived, D0 cost when unused. Flag an optional compaction *much later*, not in V1. |
| **Event vocabulary drift** | Low | New events follow the existing `events.py` "emit after persistence, canonical ids only" pattern. |

---

## 14. Acceptance / Definition of Done (design target)

1. Repeated consistent observations verify a claim without manual curation; duplicates do not accumulate.
2. A contradiction produces a `supersedes`/`conflicts_with` edge and preserves both histories.
3. Old, un-corroborated claims decay in retrieval priority and drop out of the personal projection.
4. A rarely-mentioned stable preference outranks a recently discussed temporary topic.
5. Personal projection answers "what does Novi know about me" from Identity + Scenarios + Projects, with no new storage and no invented attributes.
6. Memory is inspectable and correctable; user corrections are append-only.
7. `Brain.recall` returns important, verified, scenario-appropriate knowledge by default.
8. Every write is append-only (supersede/archive), never an in-place mutation.
9. Full existing test suite stays green; new pure-reasoning tests added (`reflection`, `decay`, `conflict`, `projection`, `tiering`).