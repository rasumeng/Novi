# Cozmo Devlog — Architecture Evolution

## Grounding Architecture Refactor

### Problem
Original grounding was ad-hoc: `TaskProfile.needs_grounding` boolean, no source tracking, no structured reasoning. The orchestrator had no dedicated grounding decision layer.

### Solution
Introduced `GroundingDecision` — a structured dataclass that captures the complete grounding decision:

```python
@dataclass
class GroundingDecision:
    needs_grounding: bool
    confidence: float
    reason: str
    source: str  # "keyword" | "heuristic" | "llm" | "none"
```

### Separation of Responsibilities

| Component | Responsibility |
|-----------|---------------|
| `IntentDetector` | Classifies task category (conversation, research, coding, planning, vision) |
| `EvidenceDetector` | Detects external information signals only (temporal, dynamic, comparative, project, memory) |
| `GroundingReasoner` | LLM-based reasoner for ambiguous cases (medium confidence, conflicting signals) |
| `Orchestrator` | Owns grounding decisions — four-tier: keyword → heuristic → LLM → none |

### Changes
- `TaskProfile.needs_grounding` removed
- `GroundingDecision` moved into `TaskAnalysis` as `.grounding`
- Grounding decisions are now structured with `source` (how decided), `confidence` (how sure), `reason` (why)
- Orchestrator's `_resolve_grounding()` implements the four-tier decision pipeline
- Evidence pipeline detects signals → feeds grounding → runtime executes retrieval

---

## Trace Architecture Rewrite

### Problem
Previous trace system leaked internal state directly to users. Raw confidence values, heuristic names, and routing details were exposed. The UI had to interpret internal concepts it shouldn't know about.

### Solution
Three-layer trace architecture:

```
Internal State
    ↓
Trace Event Layer    (action + category + summary)
    ↓
Trace Formatter      (maps to user-readable labels + icons)
    ↓
User UI
```

### TraceAction Enum
```python
class TraceAction(str, Enum):
    UNDERSTANDING = "understanding"   # Analyzing request
    RETRIEVING    = "retrieving"      # Finding information
    PLANNING      = "planning"        # Building execution plan
    EXECUTING     = "executing"       # Using tools
    RESPONDING    = "responding"      # Preparing answer
```

Each action maps to a user-visible label and icon via `TraceActionMetadata`.

### Dual Trace Streams

**TraceEvent** (user-facing):
- `action` — one of 5 user-understandable actions
- `category` — broad topic (reasoning, information_retrieval, knowledge, planning, tool_use)
- `summary` — one-line explanation

**DebugTraceEvent** (debug-only):
- `category` — internal phase name
- `data` — raw dict with implementation details

Stored separately: `trace.user_events` vs `trace.debug_events`.

### Design principles
- Trace events intentionally avoid leaking: confidence values, signal types, heuristic names, internal routing logic
- Debug traces only emitted when `debug_trace=True`
- The UI never sees `GroundingDecision`, `EvidenceAnalysis`, or `TaskProfile` internals

### ExecutionTrace
Single structured object emitted at end of every `run_stream()`:
- request_id, user_input, timing
- intent + confidence
- memory query results
- grounding quality + source count
- recovery attempts
- model selected + reason
- step-by-step tool calls
- final response metadata

---

## Retrieval Architecture

### Problem
Previously, retrieval was a single `_grounding_search()` call triggered by `needs_grounding` boolean. No separation between "should I retrieve?" and "where should I retrieve from?" No source selection, no fallback chains, no strategy awareness.

### Solution
Three-layer retrieval pipeline:

```
User Query
    ↓
Grounding Decision     (should we retrieve?)
    ↓
Retrieval Policy       (where should we retrieve?)
    ↓
Retrieval Coordinator  (execute retrieval with budget)
    ↓
Evidence Bundle        (structured search results)
    ↓
Runtime Reasoning      (LLM synthesizes answer)
```

### RetrievalPolicy
Pure decision logic. No runtime dependencies, no keyword matching.

```python
class RetrievalSource(str, Enum):
    KNOWLEDGE = "knowledge"  # Local knowledge base
    WEB = "web"              # Web search

class RetrievalStrategy(str, Enum):
    NONE              = "none"               # No retrieval needed
    KNOWLEDGE_ONLY    = "knowledge_only"     # Local KB only
    WEB_ONLY          = "web_only"           # Web search only
    KNOWLEDGE_THEN_WEB = "knowledge_then_web" # KB first, escalate to web

@dataclass
class RetrievalPlan:
    sources: list[RetrievalSource]
    strategy: RetrievalStrategy
    reason: str
```

The policy uses existing structured signals only:
- `GroundingDecision.needs_grounding`
- Evidence signal types and strengths (temporal, dynamic, comparative)
- Intent type

### RetrievalCoordinator
Execution control layer. Intercepts `web_search`/`web_fetch` tool calls during the ReAct loop to enforce rules:

- **Budget tracking**: max 1 web search + 1 web fetch per execution
- **Duplicate prevention**: exact match + semantic term overlap (>= 50% overlap with >= 2 common terms)
- **Cache seeding**: pre-populated with pre-loop retrieval results
- **Strategy-aware limits**: KNOWLEDGE_ONLY gets 0 search/fetch budget
- **Phase guidance**: temporary system message when retrieval is active

No global tool removal — the coordinator returns guidance messages when budget is exhausted or duplicates are detected.

### Pre-loop Retrieval Execution
`_execute_retrieval_plan()` in runtime:
- WEB_ONLY: runs `_grounding_search()` (web via EvidenceCollector)
- KNOWLEDGE_ONLY: runs `_retrieve_knowledge()` (local KB)
- KNOWLEDGE_THEN_WEB: KB first, escalates to web if empty
- NONE: traces "no retrieval needed"

### Files
- `cozmo/runtime/retrieval_policy.py` — RetrievalSource, RetrievalStrategy, RetrievalPlan, RetrievalPolicy
- `cozmo/runtime/retrieval_coordinator.py` — RetrievalBudget, RetrievalCoordinator

---

## Knowledge Assessment / Runtime Recovery

### Problem
Previously, the system had no feedback on whether retrieved evidence was sufficient. The model decided whether it "knew enough" — leading to confident but wrong answers when retrieval failed silently.

### Solution
Introduced `RetrievalQuality` — structured quality assessment for every retrieval attempt:

```python
class RetrievalQuality(enum.Enum):
    SUFFICIENT = "sufficient"  # Good results, model can answer
    WEAK       = "weak"       # Partial results, low relevance
    EMPTY      = "empty"      # No results found
    FAILED     = "failed"     # Search API error
```

### Recovery System
Two-phase recovery when retrieval quality is insufficient:

**Phase 2 (pre-loop)**: Before the ReAct loop, if retrieval quality is not SUFFICIENT, upgrade capabilities to include web search tools. This ensures the model has the right tools before it starts reasoning.

**Phase 3 (mid-loop)**: During the ReAct loop, if:
1. Retrieval was attempted (quality recorded)
2. Quality is not SUFFICIENT
3. Model chose to answer without calling any tool
4. Below recovery attempt limit (max 1)

Then: add web search tools, rebind the runnable, inject a system message telling the model web search is available, continue the loop.

### Escalation Paths
- **KB empty, pre-loop**: KNOWLEDGE_THEN_WEB auto-escalates to web search before the ReAct loop starts
- **KB empty, in-loop**: Post-tool recovery detects `search_knowledge` returning empty → adds web tools, injects system message
- **Model answers without tools**: Phase 3 recovery upgrades capabilities and retries

### Quality Tracing
`RetrievalQuality` tracked on both `ExecutionContext` (runtime state) and `ExecutionTrace` (observability):
- `grounding_quality` — the quality grade
- `grounding_source_count` — number of sources returned
- `grounding_relevance_score` — term relevance evaluation
- `recovery_attempts` — count of recovery activations
- `recovery_action` — what recovery did

---

## Evidence / Search Improvements

### EvidenceCollector
Structured evidence acquisition pipeline replacing flat-string grounding:

```
query → search → rank/filter → fetch → merge → EvidenceBundle
                                     ↓
                                sufficient? → yes → return
                                     ↓ no
                                reformulate → retry
```

### EvidenceBundle
```python
@dataclass
class EvidenceBundle:
    query: str
    results: list
    merged_text: str
    source_count: int
    error: str | None
    quality: RetrievalQuality | None
```

### SearXNG Fixes
- Time range mapping: `d/w/m/y` → `day/week/month/year` (native SearXNG params)
- Search failure propagation: errors surface correctly through the pipeline
- Relevance evaluation: results filtered by term overlap ratio
- Reformulation: low-relevance results trigger query reformulation and retry

### Source Ranking
- Text results prioritized over video/image
- Relevance scoring via key term overlap
- Content fetching for top results
- Merged into single evidence string for model consumption

### File
- `cozmo/runtime/evidence.py` — EvidenceBundle, EvidenceCollector, RetrievalQuality
- `cozmo/tools/search_pipeline.py` — SearchConfig, SearchResult, search/fetch/rerank

---

## Retrieval Optimization

### Goal
Prevent wasteful search patterns:
```
search → search → search → fetch → timeout (17+ steps)
```

Promote efficient retrieval:
```
retrieve → understand → answer (5-8 steps)
```

### Implementation
`RetrievalCoordinator` enforces:
- **Max 1 web search** per execution (blocks duplicates and budget-exceeded calls)
- **Max 1 web fetch** per execution
- **Duplicate detection** via exact match + semantic term overlap
- **Cache seeding** with pre-loop results so first in-loop web search is caught as duplicate
- **Strategy-aware budgets**: KNOWLEDGE_ONLY gets 0 search/fetch; WEB_ONLY gets 1/1

### Trace Metrics (debug-only)
- `retrieval_search_count` — actual searches performed
- `retrieval_fetch_count` — actual fetches performed
- `retrieval_budget_exhausted` — whether budget was fully consumed

Excluded from user-facing `to_dict()`.

---

## Pre-Phase 9 — Memory & Knowledge Correctness Sprint

### Context
Phase 7 (Evidence Processing) and Phase 8 (Evaluation & Observability) are complete. The 2026-07-31 memory architectural audit found correctness defects in the existing foundations — duplicate knowledge indexing, broken WebUI memory endpoints, unregistered memory tools, dead reranking paths, and config values that did not control behavior. Unified retrieval (Phase 9) must not be built on unstable foundations.

### Sprint Scope (reliability, not architecture)
- **Knowledge index reliability**: deterministic chunk identifiers, idempotent re-indexing, stale-chunk removal, vector index support
- **Memory system reliability**: config now actually controls behavior (`max_turns_before_summary`, `max_short_term_pairs`), active `MemoryManager` registered via `get/set_memory_manager` for tool access, `embed_model` stamped on stored records
- **Embedding lifecycle**: `EmbeddingService.model_name` / `RerankerService.model_name` exposed; config values (memory, embedding, reranker) drive observed behavior
- **Reranking**: `reranker` service wired into knowledge index initialization
- **Memory tools**: `memory_ops` registered in the tool registry

### Regression Coverage
New `tests/test_memory_correctness.py` locks down: re-index idempotency, stale/legacy-row removal on file change, deterministic UUID replacement, and embedding-model change handling.

---

## Test Suite Consolidation

### Problem
Suite was correct but slow and partly environment-dependent. ~73% of runtime was live-network/backend work, not assertions: 5 tests hit a real SearXNG server, and the `TestSession` fixture built the full production backend (11s one-time).

### Changes
- **Deterministic tests**: `test_search_pipeline.py` mocks `urllib.request.urlopen` (preserves the HTTP-400 contract); `test_evidence.py` mocks `_search_multi`; `test_v2_pipeline.py` `TestSession` mocks `build_runtime` (real EventBus + mocks); `test_execution_context.py` and `test_trace_boundary.py` stub the live search
- **Dead/duplicate removal**: `test_next_character_grounding_true` (empty `pass` body), `test_memory_types_per_intent` and `test_research_python_history` (tautologies), duplicate run_stream backward-compat test
- **Parameterization**: `TestGroundingDecision` 5 near-identical source tests → 1 parametrized; `TestEvidencePatterns` 9 near-identical detection tests → 2 parametrized; shared `orch_factory` fixture removed 6× orchestration boilerplate
- **Bug found**: mocking the live search exposed an un-gated `DebugTraceEvent` append in `RetrievalExecutor.execute_search` (empty/failed branches leaked debug events when `debug_trace=False`). Fixed to gate on `self.debug_trace`, matching the pattern everywhere else.

### Result
- **403 tests, all passing** (was 408 — net -5 dead/duplicate; parametrize preserved all assertions)
- **Suite: ~25s → ~5.4s** (~78% faster); only remaining slow test is the intentional 2s `test_timeout_guard`
- No network dependency: suite runs without SearXNG or a live backend

---

## Current Architecture State

```
User Input
    ↓
Orchestrator
├── IntentDetector          (classifies task type)
├── EvidenceDetector        (detects info signals)
├── ComplexityEstimator     (scores task complexity)
├── Grounding Decision      (should we retrieve?)
└── RetrievalPolicy         (where should we retrieve?)
    ↓
Runtime
├── RetrievalCoordinator    (executes with budget/dedup)
├── EvidenceCollector       (search → rank → fetch → merge)
├── Recovery System         (Phase 2 pre-loop + Phase 3 mid-loop)
├── Trace System            (user events + debug traces)
└── Agent Execution Loop    (ReAct with tool calling)
    ↓
Response
```

### Test Suite
- 403 total tests
- All passing in ~5.4s (no network/backend dependencies)
- Test files: `test_trace_boundary.py`, `test_evidence.py`, `test_grounding.py`, `test_retrieval_coordinator.py`, `test_search_pipeline.py`, `test_execution_context.py`, `test_regression.py`, `test_evidence_processing.py`, `test_evidence_ab.py`, `test_evaluation.py`, `test_memory_correctness.py`

### Key Files

```
cozmo/
├── runtime/
│   ├── retrieval_policy.py      # RetrievalSource, RetrievalStrategy, RetrievalPolicy
│   ├── retrieval_coordinator.py # RetrievalBudget, RetrievalCoordinator
│   ├── evidence.py              # EvidenceBundle, EvidenceCollector, RetrievalQuality
│   ├── trace.py                 # ExecutionTrace, TraceEvent, DebugTraceEvent
│   ├── execution_context.py     # ExecutionContext — unified run state
│   ├── runtime.py               # CozmoRuntime — unified execution loop
├── orchestrator/
│   ├── evidence.py              # EvidenceDetector, EvidenceSignal
│   ├── task_types.py            # TaskAnalysis, GroundingDecision, EvidenceAnalysis
│   ├── orchestrator.py          # Orchestrator — analysis pipeline
│   ├── intent.py                # IntentDetector, classify_intent
```

### Current Focus
**Pre-Phase 9 Memory & Knowledge Correctness Sprint** — in progress. Phase 9 (Unified Retrieval Policy) is next.
