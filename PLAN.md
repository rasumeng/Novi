# Cozmo — Architecture Evolution Plan

## 1. Project Vision

Cozmo transforms from a local AI chatbot with tools into a local AI agent operating system.

Every user request becomes a `Task`. The system determines intent, complexity, strategy, tools, and model — the user never chooses a mode. Intelligence is modular, measurable, and replaceable. The runtime orchestrates execution without owning domain logic. Subsystems communicate through stable interfaces and events. Concrete implementations of every major capability remain swappable.

The system serves as a local-first agent platform: private, extensible, and observable by design.

---

## 2. Current Architecture

### 2.1 Runtime

The runtime (`CozmoRuntime`) is a production-grade ReAct loop. It takes user input, executes a multi-phase pipeline (retrieval → planning → ReAct loop → memory), and streams results back to the consumer.

**Responsibilities:**
- Execute the full agent loop: retrieve, plan, invoke model, process tool calls, recover from failures
- Yield streaming output (tokens, tool calls, trace events) to consumers
- Manage per-execution state via `ExecutionContext`
- Delegate tool execution to `ToolExecutor`, retrieval to `RetrievalExecutor`
- Enforce retrieval budget and deduplication via `RetrievalCoordinator`
- Finalize and emit `ExecutionTrace` via `RuntimeTracer`

**Boundaries:**
- Does not own intent detection, evidence detection, complexity estimation, capability resolution, or retrieval policy decisions — those belong to the orchestrator
- Does not own evaluation, planning abstraction, or UI behavior
- Consumes `Orchestrator.analyze()` output as `TaskAnalysis`
- Consumer access formalized through `RuntimeInterface` Protocol only

**Key design decisions:**
- Heavy constructor (~30 fields) is a known code smell but stabilized — deliberate tradeoff to avoid service locator or DI framework
- Recovery logic (search escalation, model fallback) lives inside runtime because it is execution policy, not analysis policy
- All tracing goes through `RuntimeTracer` — runtime never constructs trace types directly

### 2.2 Orchestrator

The orchestrator is a thin analysis layer that transforms user input into an `ExecutionPlan`. It coordinates multiple detectors but executes nothing.

**Responsibilities:**
- Classify user intent via `IntentDetector`
- Detect evidence signals via `EvidenceDetector`
- Estimate complexity via `ComplexityEstimator`
- Resolve capabilities via `CapabilityRegistry` (additive, not selective)
- Produce `GroundingDecision` via three-tier analysis (keyword/heuristic/LLM)
- Determine `RetrievalPlan` via `RetrievalPolicy`
- Determine model requirements via `ModelRequirement` (capability, VRAM, temperature)
- Output `TaskAnalysis` containing all analysis results

**Boundaries:**
- Pure analysis — no I/O, no model invocation, no tool execution
- `EvidenceDetector` analyzes the user input for signal patterns; it does not retrieve evidence
- `RetrievalPolicy` decides WHERE/HOW to retrieve; it does not execute retrieval
- Orchestrator may be bypassed when runtime falls back to direct `classify_intent()`

### 2.3 Intent Detection

`IntentDetector` classifies user intent into `IntentType` enum: `CONVERSATION`, `RESEARCH`, `CODING`, `PLANNING`, `AUTONOMOUS`, `VISION`, `CONTINUATION`. Uses LLM classification with structured output.

**Boundary:** Pure classification. Does not determine strategy, capabilities, or tools.

### 2.4 Evidence Detection

`EvidenceDetector` analyzes user input for evidence signals: temporal, comparative, locality, project, memory, dynamic. Produces `EvidenceAnalysis` with signal types, strengths, and confidence.

**Boundary:** Signal detection only. Does not retrieve, rank, or validate evidence. `RetrievalPolicy` and runtime consume the analysis.

### 2.5 Grounding Decisions

`GroundingDecision` answers: does this request need external information? Uses three-tier approach:

1. **Keyword tier** — fast pattern matching for obvious grounding needs
2. **Heuristic tier** — rule-based signals (temporal, comparative, locality)
3. **LLM tier** — model-based judgment for ambiguous cases

**Boundary:** Decision only. Execution of grounding (search, fetch) belongs to `RetrievalExecutor`.

### 2.6 Retrieval Pipeline

Separated into four distinct subsystems:

| Subsystem | Role | Location |
|-----------|------|----------|
| `RetrievalPolicy` | Pure decision: WHERE/HOW to retrieve | `runtime/retrieval_policy.py` |
| `RetrievalCoordinator` | Execution governor: budget, dedup | `runtime/retrieval_coordinator.py` |
| `RetrievalExecutor` | Orchestrated execution of retrieval plans | `runtime/retrieval.py` |
| `EvidenceCollector` | Raw collection: search, rank, fetch, merge | `runtime/evidence.py` |

**Boundaries:**
- Policy has no I/O — consumes signals, produces `RetrievalPlan`
- Coordinator has no I/O — intercepts tool calls, enforces limits
- Executor owns the retrieval flow but delegates I/O to `EvidenceCollector` and memory
- `EvidenceCollector` is the only subsystem that touches search/memory

**Current limitations:**
- `RetrievalPolicy` knows only two sources: KNOWLEDGE (RAG KB) and WEB
- Memory is queried separately by runtime, not unified in the retrieval plan
- `EvidenceBundle.merged_text` is a flat string — no structured facts, no conflict detection, no confidence scoring
- Three call sites for `_grounding_search` in runtime (retrieval plan path, grounding needs path, fallback intent path)

### 2.7 Memory Architecture

`MemoryManager` stores and retrieves conversation history as OKF-classified (Observation-Knowledge-Feeling) memories with LanceDB backend. Importance scoring uses frequency × recency × distance formula.

**Boundaries:**
- Memory is queried during runtime execution phase, not during orchestration
- Memory is NOT a retrieval source in `RetrievalPolicy` — it is a separate path
- Embeddings use `EmbeddingService` (Sentence Transformers), decoupled from LLM providers
- `KnowledgeIndex` manages documentation RAG (separate from conversation memory)

**Current status:** The memory system architectural audit (2026-07-31) identified correctness defects in the existing foundations: duplicate knowledge indexing, broken WebUI memory endpoints, unregistered memory tools, dead reranking and consolidation paths, and configuration values that do not control behavior. These are restored in the Pre-Phase 9 correctness sprint before memory participates in unified retrieval (Phase 9).

### 2.8 Tool Execution

`ToolRegistry` owns registration. `ToolExecutor` handles execution with permission gating, validation, sanitization, and fallbacks. `PermissionResolver` gates destructive operations.

**Boundaries:**
- Runtime invokes tools through `ToolExecutor`, never directly
- `ToolRegistry` is populated at startup via `@register_tool` decorator and MCP providers
- `LessonStore` records tool success/failure patterns for future reflection

### 2.9 Trace and Event Architecture

Dual-layer observability:

| Layer | Mechanism | Consumers |
|-------|-----------|-----------|
| Events | `EventBus` pub/sub | WebSocket, logging, memory, reflection |
| Traces | `ExecutionTrace` + `RuntimeTracer` | Evaluation, debugging, analytics |

`EventBus` provides real-time typed events (30+ event types). `RuntimeTracer` provides structured per-execution traces with full metadata (intent, model, retrieval, tools, latency, steps).

**Boundary:** Tracing is write-only from runtime perspective. Consumers observe but do not control execution.

### 2.10 Model Abstraction

Orchestrator determines model *requirements* via `ModelRequirement` (capability, VRAM, temperature). `ModelRouter` resolves requirements to a concrete model based on available resources. `model_service` and `ModelManager` provide provider-agnostic invocation. Providers (Ollama, OpenAI) implement the same invocation contract.

**Boundary:** Orchestrator owns *what is needed*. `ModelRouter` owns *what to use*. Runtime owns *when to invoke*. These are three distinct concerns.

**Direction:** Model routing is resource-aware (VRAM, loaded models, concurrency). Selection is deterministic given capability requirements — not part of runtime decision logic.

---

## 3. Architectural Principles

### 3.1 Task is Universal Currency

Not conversations, not messages, not modes. Every user request creates a `Task` with `id`, `goal`, `status`, and execution history.

### 3.2 Orchestrator is Thin

Coordinates analysis. Does not execute. Delegates everything.

### 3.3 Runtime is Execution, Not Ownership

Runtime coordinates the agent loop. It does not own retrieval policy, evaluation, UI behavior, or planning logic.

### 3.4 Engine is Stateless

The ReAct loop (`Engine`) has no knowledge of modes, tasks, or intents. It receives a `Job` and streams events.

### 3.5 Events Are the Observation Layer

Events are for observation, lifecycle notifications, and streaming — never for hidden control flow. Direct interfaces (Protocols, function calls) remain preferred for execution flow and command dispatch. Events describe what happened; they do not prescribe what should happen next.

### 3.6 Policy Gates All Action

No destructive operation bypasses policy check. Tool execution, retrieval, and model selection all pass through policy layers before action.

### 3.7 Capabilities are Composable

Each capability declares tools, permissions, model needs, and risk profile independently. Composition is additive: resolve all applicable capabilities rather than choose one.

### 3.8 Retrieve Before Plan

Context resolution precedes planning. Memory enriches early. The system understands what it knows before deciding how to act.

### 3.9 Measure Before Improve

Evaluation frameworks precede new intelligence layers. No untestable changes.

### 3.10 Replaceability

Major subsystems remain replaceable: LLM providers, retrieval sources, memory systems, evaluators, UI clients. Depend on interfaces, not implementations.

### 3.11 Explicit Data Contracts

Subsystem boundaries are defined through stable typed contracts. Every cross-subsystem handoff uses a formal dataclass or protocol:
- `TaskAnalysis` — orchestrator to runtime
- `RetrievalPlan` — retrieval policy to executor
- `EvidenceContext` — evidence processor to runtime
- `ExecutionTrace` — runtime to evaluation and UI
- `MetricSet` — evaluation to comparison tooling
- `Plan` — planner to runtime

These contracts define the shared vocabulary of the system. Changing a contract requires updating all consumers — the cost is intentional and signals architectural coupling.

### 3.12 Composition Over Inheritance

Independent capabilities composed together. No large class hierarchies.

### 3.13 Storage Evolves Independently from Retrieval

Storage and representation evolve independently from retrieval. The retrieval layer depends on the `RetrievalSource` contract, not on concrete storage formats. LanceDB, OKF, Markdown, JSON, and future formats are implementation details of individual sources. This keeps the retrieval layer stable as memory representation changes.

---

## 4. Architecture Evolution Roadmap

### Phase Sequence

```
Phase 6.5 — Runtime Stabilization (COMPLETE)
    |
    |  Enables: formal consumer contract, centralized tracing, event-driven observation
    v
Phase 8 — Evaluation & Observability
    |
    |  Enables: measurable quality, regression detection, data-driven decisions
    v
Phase 7 — Evidence Processing
    |
    |  Enables: trustworthy evidence, structured facts, quality signals
    v
Phase 0 — WebUI Completion
    |
    |  Enables: full user-facing exposure of all capabilities
    v
Pre-Phase 9 — Memory & Knowledge Correctness Sprint
    |
    |  Enables: trustworthy memory/knowledge foundations for unified retrieval
    v
Phase 9 — Unified Retrieval Policy
    |
    |  Enables: unified retrieval from all sources, adaptive strategies
    v
Phase 10 — Planner & Long-running Tasks
    |
    |  Enables: autonomous multi-step tasks, session survival, background work
    v
Long-Term Target Architecture
```

### Why This Sequence

**Phase 8 before Phase 7:** Evidence processing introduces fact extraction, conflict detection, and confidence scoring — all LLM-dependent operations. Without evaluation, you cannot measure whether these improvements actually help or harm answers. Build the yardstick before the race.

**Phase 7 before Phase 0:** Evidence processing produces structured data (facts, conflicts, confidence scores) that the UI should expose. Completing the UI before evidence processing means restructuring the frontend twice. Let the backend capability settle first.

**Phase 0 before Phase 9:** Unified retrieval policy introduces memory, files, and projects as retrieval sources. The UI must be capable of displaying multi-source retrieval results, source selection, and retrieval metadata. Complete the UI foundation before expanding the retrieval surface.

**Correctness Sprint before Phase 9:** The memory system architectural audit found correctness defects in the existing memory and knowledge foundations (duplicate knowledge indexing, broken WebUI memory endpoints, unregistered memory tools, dead reranking and consolidation paths, configuration values that do not control behavior). Unified retrieval cannot be built on unstable foundations. Stabilization is a small prerequisite phase, not a feature expansion.

**Phase 9 before Phase 10:** Long-running tasks depend on unified retrieval. A planner that needs information during multi-step execution should use the same retrieval abstraction as everything else. Without unified retrieval, the planner would either duplicate retrieval logic or bypass the policy layer.

---

## 5. Phase Specifications

### 5.1 Phase 8 — Evaluation & Observability

#### Purpose

Create the measurement infrastructure needed to make data-driven decisions about all future changes. Without this phase, every improvement to evidence, retrieval, planning, or memory is guesswork.

#### Goals

- Runtime execution can be consumed by evaluation tooling
- Evaluation framework exists with benchmark dataset
- Metrics exist for retrieval quality, answer quality, tool efficiency, and latency
- Regression detection runs automatically
- Observability is lightweight — events are consumed, not stored at scale

#### Primary Deliverables

- `EvaluationRunner` — automated eval harness
- Benchmark dataset (extension of `regression_corpus.json`)
- Metric collectors for retrieval, answer quality, tool usage, latency
- Comparison tooling (before/after change analysis)
- TraceCollector for evaluation consumption (in-memory, bounded)

#### Components Introduced

| Component | Role |
|-----------|------|
| `EvaluationRunner` | Orchestrates benchmark execution, metric collection, comparison |
| `MetricCollector` | Per-category metrics (retrieval, answer, tools, latency) |
| `BenchmarkDataset` | Typed benchmark cases with ground truth |
| `RegressionDetector` | Compares metric sets, flags regressions |
| `TraceCollector` | In-memory trace collection for evaluation consumption |

#### Public Interfaces

```python
@dataclass
class MetricSet:
    retrieval: RetrievalMetrics
    answer: AnswerMetrics
    tools: ToolMetrics
    latency: float

@dataclass
class RetrievalMetrics:
    precision: float
    recall: float
    source_quality_distribution: dict
    grounding_accuracy: float

@dataclass
class AnswerMetrics:
    correctness: float
    completeness: float
    relevance: float
    hallucination_rate: float

@dataclass
class BenchmarkCase:
    input: str
    expected_intent: IntentType
    expected_grounding: bool
    expected_sources: list[str]
    expected_answer_contains: list[str]
    tags: list[str]

class EvaluationRunner:
    def run(dataset: BenchmarkDataset) -> EvaluationResult
    def compare(baseline: MetricSet, candidate: MetricSet) -> RegressionReport
```

#### Dependencies

**Must exist:** RuntimeInterface Protocol, EventBus, ExecutionTrace, existing regression corpus.

**Depends on this phase:** Phase 7 (needs measurement), Phase 9 (needs measurement), Phase 10 (needs measurement).

#### Architectural Constraints

- Evaluation must consume runtime outputs, not control runtime execution
- No changes to runtime internals for evaluation purposes
- Observability must remain intentionally lightweight — no dashboards, analytics platforms, or large-scale storage
- Metric definitions must be stable across future phases (do not redesign metrics each phase)
- Benchmark dataset must be extensible without framework changes

#### Risks

- Overbuilding the evaluation framework (dashboards, visualization, complex reporting). Scope control is critical.
- Tight coupling between metric definitions and specific trace schema fields. Version the trace schema.
- Creating evaluation that only works for simple cases, missing the complex multi-step behavior that matters most.
- Benchmark dataset becoming stale or reflecting only what is easy to measure.

#### Success Criteria

- 50+ benchmark cases covering all intent types and retrieval strategies
- Retrieval precision/recall measurable and tracked
- Answer quality (correctness, completeness) measurable
- Before/after comparison produces actionable regression signals
- Evaluation completes in under 5 minutes for full dataset
- No changes required in runtime or orchestrator to support evaluation

#### Explicit Non-Goals

- Dashboards or visualization platforms
- Real-time monitoring
- Persistent trace storage (explicitly deferred)
- Analytics platforms (explicitly deferred)
- Automated CI integration (future concern)
- A/B testing infrastructure
- User-facing analytics

---

### 5.2 Phase 7 — Evidence Processing

#### Purpose

Transform raw retrieval into trusted, structured evidence with quality signals. Current `EvidenceBundle.merged_text` is a flat string — the system cannot distinguish between conflicting sources, cannot assess confidence per fact, and wastes tokens on irrelevant content.

#### Goals

- Raw search results are processed into structured facts with confidence scores
- Source conflicts are detected and surfaced
- Context compression reduces token waste
- Evidence quality is measurable and comparable
- `EvidenceContext` replaces `EvidenceBundle.merged_text` as the primary evidence contract
- Grounding decisions incorporate evidence quality signals

#### Primary Deliverables

- `EvidenceProcessor` — post-collection evidence refinement pipeline
- `EvidenceContext` — structured evidence contract with facts, sources, conflicts, confidence
- `SourceRanking` — configurable, pluggable ranking strategies
- `FactExtractor` — LLM-based structured fact extraction
- `ConflictDetector` — source disagreement detection and resolution
- `ContextCompressor` — relevance-based compression

#### Components Introduced

| Component | Role |
|-----------|------|
| `EvidenceProcessor` | Orchestrates fact extraction → conflict detection → compression → confidence assessment |
| `EvidenceContext` | Structured evidence dataclass replacing flat `merged_text` |
| `SourceRanking` | Pluggable ranking functions (authority, relevance, freshness, source type, consistency) |
| `FactExtractor` | LLM-based fact extraction with confidence per fact |
| `ConflictDetector` | Source disagreement detection with resolution strategy |
| `ContextCompressor` | N search results → verified summary |
| `ConfidenceAssessor` | Aggregate evidence confidence scoring |

#### Public Interfaces

```python
@dataclass
class Fact:
    statement: str
    confidence: float
    sources: list[str]
    category: str

@dataclass
class Source:
    url: str
    title: str
    authority: float
    relevance: float
    freshness: datetime | None
    source_type: str

@dataclass
class Conflict:
    statements: list[str]
    sources: list[str]
    severity: str  # MAJOR, MINOR
    resolution: str | None

@dataclass
class EvidenceContext:
    facts: list[Fact]
    sources: list[Source]
    conflicts: list[Conflict]
    confidence: float
    summary: str

class EvidenceProcessor:
    def process(bundle: EvidenceBundle) -> EvidenceContext

class SourceRanking:
    def rank(sources: list[dict], config: RankingConfig) -> list[dict]
    def register_scorer(name: str, fn: Callable)
```

#### Dependencies

**Must exist:** `EvidenceCollector`, `EvidenceBundle`, `RetrievalQuality` (all from Phase 6.5). Evaluation framework from Phase 8 to measure impact.

**Depends on this phase:** Phase 0 (UI exposes evidence quality), Phase 9 (retrieval policy uses evidence quality signals).

#### Architectural Constraints

- `EvidenceProcessor` must wrap/complement `EvidenceCollector`, not replace it
- `EvidenceProcessor` must not own retrieval logic — it processes what `EvidenceCollector` provides
- `EvidenceContext` must coexist with `EvidenceBundle.merged_text` during migration — consumers migrate one by one
- Fact extraction should prefer classification tasks over generation tasks for reliability with small models
- Must provide fallback to raw text when extraction confidence is low
- No coupling to specific LLM providers for extraction

#### Risks

- Small local models (3B-8B) producing unreliable fact extractions. Mitigation: use smaller extraction tasks (classify not generate), fallback to raw text when confidence is low.
- `EvidenceContext` migration breaking existing consumers. Mitigation: dual-delivery during transition (both `merged_text` and `EvidenceContext`), migrate consumers one by one.
- Conflict detection producing too many false positives. Mitigation: severity tiers, configurable thresholds.
- Context compression losing critical information. Mitigation: compression ratio limits, relevance scoring transparency.

#### Success Criteria

- `EvidenceContext` fully replaces `merged_text` in all consumers
- Fact extraction produces structured facts with per-fact confidence
- Conflict detection identifies real contradictions with <10% false positive rate
- Context compression reduces token usage by 40%+ without degrading answer quality
- Source ranking is configurable (authority, relevance, freshness, source type, consistency)
- Evaluation shows measurable improvement in answer quality after evidence processing

#### Explicit Non-Goals

- Retrieval policy decisions (that is Phase 9)
- Memory as a retrieval source (Phase 9)
- Long-term evidence storage
- User-facing evidence editing
- Multi-modal evidence (text only)

#### Baseline (FROZEN)

Status: **FROZEN baseline** — the Phase 7 implementation is complete and stable.
Do not modify evidence processing without a gated, incremental change.

- **Measurement command:** `python -m cozmo.evaluation evidence [--dataset tests\evidence_corpus.json] [--save PATH]`
  (also `evidence-compare BASELINE.json CANDIDATE.json`). Corpus: `tests/evidence_corpus.json`
  (9 cases, EV-01..EV-09). Frozen reference report: `tests/evidence_baseline.json`.
- **Baseline numbers** (qwen3:8b, temp 0.0, 9-case corpus, 2026-07-31):

  | Mode | Recall | Grounded |
  |------|--------|----------|
  | A_merged (status quo) | 0.938 | 0.889 |
  | B_context (EvidenceContext.summary) | 0.938 | 0.889 |
  | N_none (control) | 0.062 | 0.111 |

  Compression: **25.3%** (4322 → 3228 chars). Behaviorally equivalent, not yet migration-ready.

- **Gate rule (binding):** any future optimization — budget tuning, conflict/confidence
  rendering, improved extraction — is proposed as an incremental change and accepted only
  if `evidence-compare BASELINE.json CANDIDATE.json` reports **PASS** (no regression beyond
  tolerance) *and* a measurable improvement (higher recall/grounded or compression). No
  speculative optimization. No chase of the 40% compression success criterion without a
  passing A/B first.
- **Migration rule unchanged:** consumers of `merged_text` migrate to `EvidenceContext`
  one-by-one, each after its own measured improvement.

---

### 5.3 Phase 0 — WebUI Completion

#### Purpose

Complete the frontend to fully expose all backend capabilities. The WebUI must consume backend services without owning intelligence logic. This phase ensures the UI is a presentation layer, not a second execution path.

#### Goals

- All runtime capabilities are accessible through the WebUI
- Streaming response rendering is smooth and reliable
- Trace visualization shows current action with metadata
- Debug mode is available but does not expose internal types
- Conversation persistence works across sessions
- Error states, retry, and loading states are handled gracefully
- Long responses, interrupted generations, and scroll management work
- UI architecture can support future features (Projects, Jobs, Memory, Scheduled tasks, Agent workflows)

#### Primary Deliverables

- Streaming response rendering (real-time token display)
- Trace visualization (action labels, icons, metadata during streaming)
- Debug mode toggle (exposes debug data without exposing internal objects)
- Error state UI (network, model, tool failures displayed gracefully)
- Retry behavior (user-facing retry for failed operations)
- Conversation persistence (session survival)
- Loading states (analysis, retrieval, execution progress indicators)
- Handling for interrupted generations and long responses

#### Components Introduced

| Component | Role |
|-----------|------|
| `StreamRenderer` | Real-time token display component |
| `TraceVisualizer` | Current action display with metadata |
| `DebugPanel` | Debug mode expansion for internal metadata |
| `ErrorBoundary` | Graceful error display with retry |
| `ConversationStore` | Client-side conversation persistence |
| `StatusIndicator` | Progress display for analysis/retrieval/execution phases |

#### Public Interfaces

- WebSocket event types (already exist — extend with phase-appropriate metadata)
- `RuntimeInterface` Protocol (already exists — no changes expected)
- Trace schema for UI consumption (versioned, stable subset of `ExecutionTrace`)

#### Dependencies

**Must exist:** `RuntimeInterface` Protocol (Phase 6.5), `EventBus` event types, `ExecutionTrace` schema, streaming infrastructure.

**Depends on this phase:** Phase 9 (UI must display multi-source retrieval), Phase 10 (UI must display task progress, job status, planner state).

#### Architectural Constraints

- UI must never own intelligence logic — no mode selection, no execution branching, no retrieval decisions
- UI must consume backend capabilities through `RuntimeInterface` only
- Internal types must not be exposed: `GroundingDecision`, `EvidenceAnalysis`, `RetrievalPolicy` internals, debug state internals
- Trace schema must be versioned for backward compatibility
- UI must remain a standalone compiled frontend (React dist/) served by backend

#### Risks

- UI growing into intelligence logic (chatbot-like branching, client-side decisions). Architectural review boundary.
- WebSocket protocol becoming tightly coupled to UI needs. Keep protocol generic; UI adapts to protocol, not vice versa.
- Conversation persistence creating expectations of full memory management. Scope: session-level only.
- Over-investing in UI polish before backend capabilities stabilize. Prioritize functionality over aesthetics.

#### Success Criteria

- All streaming response types render correctly (tokens, tool calls, trace events)
- Trace visualization shows current action with phase-appropriate icons/labels
- Debug mode toggles without exposing internal domain types
- Error states display gracefully with retry option
- Conversation persists across page refreshes
- Loading states shown for all phases (analysis, retrieval, execution)
- Interrupted generations detected and continuable

#### Explicit Non-Goals

- Mobile app or responsive design for all screen sizes
- Real-time notifications
- Multi-user support
- UI-side analytics or telemetry
- Theme system or extensive customization
- Project management interface (future)
- Job management interface (future — Phase 10)

---

### 5.4 Pre-Phase 9 — Memory & Knowledge Correctness Sprint

#### Purpose

Stabilize the existing memory and knowledge systems so they can serve as trustworthy foundations for unified retrieval. This is a correctness checkpoint, not a feature phase: it restores reliability to what already exists without expanding scope or building advanced memory features. Building unified retrieval on the current foundations would compound existing defects.

#### Goal

"Restore correctness and reliability of existing memory and knowledge systems before unifying retrieval."

#### Implementation Objectives

**Knowledge Index Reliability**

- Fix duplicate knowledge indexing
- Replace unstable UUID chunk IDs with deterministic chunk identifiers
- Ensure re-indexing removes stale chunks correctly
- Validate chunking behavior (boundaries, overlap, oversized chunks)
- Add vector index support where appropriate

**Memory System Reliability**

- Remove broken/dead memory paths
- Fix WebUI memory management integration
- Register or remove unused memory tools
- Ensure configuration values actually control behavior
- Remove duplicated abstractions

**Retrieval Quality Foundation**

- Properly connect reranking
- Validate embedding lifecycle (model changes, vector-space compatibility)
- Document current retrieval scoring signals:
  - relevance
  - recency
  - frequency
  - importance (future-ready concept)

#### Architecture Goal (Observability)

Memory and knowledge health checks are documented as a future architectural capability. Do not implement in this sprint. Examples:

- number of memories
- number of knowledge chunks
- duplicate detection
- embedding model information
- retrieval latency
- index health

#### Scope Boundaries

This sprint changes reliability, not architecture. No new memory features. No changes to the retrieval layer or retrieval strategy. Phase 9 remains responsible for unified retrieval architecture.

#### Success Criteria

- Knowledge re-indexing is idempotent — re-indexing leaves no stale or duplicate chunks
- WebUI memory management works against the live memory system
- Every configured memory tool is either registered or removed
- Configuration values (memory, embedding, reranker) control observed behavior
- Reranking is wired into the production retrieval path through a single abstraction
- Health-check metrics are documented as a future capability

#### Explicit Non-Goals

- New memory features
- Retrieval architecture changes (Phase 9)
- Advanced memory intelligence (reflection, consolidation, importance learning — future)
- Observability implementation (documented only)

---

### 5.5 Phase 9 — Unified Retrieval Policy

#### Purpose

Create a unified retrieval layer that allows all knowledge sources to participate through a common interface. This is retrieval architecture, not a memory redesign — the underlying sources remain independent:

- Conversation Memory
- Knowledge Base
- Project Index
- Files
- Web sources
- Future plugins

Each source exposes a common retrieval contract:

```
RetrievalSource
        |
        ↓
retrieve(query, budget)
        |
        ↓
RetrievalResult
```

`RetrievalPolicy` becomes responsible for source selection, retrieval strategy, retrieval budgets, ranking, merging results, and context allocation. The runtime does not directly query individual memory systems.

#### Goals

- Every knowledge source exposes the `RetrievalSource` contract
- `RetrievalPolicy` selects, ranks, and merges results across all sources
- Retrieval budgets and context allocation are enforced across sources
- Runtime memory query path is removed — all retrieval goes through `RetrievalExecutor`
- Sources join and leave independently (future plugins) without retrieval-layer changes
- The retrieval layer is stable regardless of underlying storage and representation formats

#### Primary Deliverables

- `RetrievalSource` protocol — common retrieval contract
- `RetrievalResult` — common result contract
- Source adapters: Memory, Knowledge, Project, File, Web
- `RetrievalPolicy` extension for multi-source decision making (selection, budgets, ranking, merging, context allocation)
- Elimination of runtime's separate memory query path
- Unified retrieval plan execution

#### Components Introduced

| Component | Role |
|-----------|------|
| `RetrievalSource` | Protocol: `retrieve(query, budget) -> RetrievalResult` |
| `RetrievalResult` | Common result contract: source, items, score, metadata |
| `MemoryRetrievalSource` | Conversation memory adapter implementing the contract |
| `KnowledgeRetrievalSource` | Knowledge base adapter implementing the contract |
| `ProjectRetrievalSource` | Project index adapter implementing the contract |
| `FileRetrievalSource` | File adapter implementing the contract |
| `WebRetrievalSource` | Web adapter implementing the contract |
| `SourceSelector` | Adaptive source selection based on query and context |
| `ResultMerger` | Merges and ranks results across sources |

#### Public Interfaces

```python
class RetrievalSource(Protocol):
    def retrieve(self, query: str, budget: RetrievalBudget) -> RetrievalResult

@dataclass
class RetrievalResult:
    source: str
    items: list[RetrievedItem]
    score: float
    metadata: dict

@dataclass
class RetrievalBudget:
    max_sources: int
    max_results: int
    max_context_chars: int

class RetrievalPolicy:
    def plan(self, query: str, context: RetrievalContext) -> RetrievalPlan
    # owns source selection, strategy, budgets, ranking, merging, context allocation
```

#### Dependencies

**Must exist:** Stable, correct memory and knowledge foundations (Pre-Phase 9), `RetrievalPolicy`, `RetrievalExecutor`, `EvidenceContext` from Phase 7, WebUI from Phase 0 capable of displaying multi-source retrieval.

**Depends on this phase:** Phase 10 (planner benefits from unified retrieval, but can use retrieval abstractions without requiring the full implementation).

#### Architectural Constraints

- `RetrievalPolicy` must remain a pure decision layer — no I/O
- Storage and representation evolve independently from retrieval: LanceDB, OKF, Markdown, JSON, and future formats are implementation details of individual sources, not of the retrieval layer
- Runtime must not directly query individual memory systems — all retrieval goes through `RetrievalExecutor`
- Policy decisions must not be coupled to specific source implementations
- Retrofit: existing memory query in runtime must be removed, not duplicated
- `SourceSelector` must support pluggable strategies (different policies for different query types)
- Result ranking and merging must be deterministic and evaluable

#### Risks

- Heterogeneous scoring across sources. Mitigation: common `RetrievalResult` contract with normalized scores.
- Ranking and merging complexity across many sources. Start with simple heuristics, iterate with Phase 8 evaluation.
- Removing the separate memory query path from runtime may break existing behavior. Test with Phase 8 evaluation before/after.
- Context allocation across many sources. Mitigation: `RetrievalBudget` with explicit caps enforced by the coordinator.

#### Success Criteria

- All sources expose the `RetrievalSource` contract
- `RetrievalPolicy` selects, ranks, and merges across sources
- Retrieval budgets and context allocation are enforced across sources
- Runtime memory query path is removed — all retrieval goes through `RetrievalExecutor`
- New sources can be added without retrieval-layer changes
- Phase 8 evaluation shows no regression in retrieval quality after unification

#### Explicit Non-Goals

Future intelligence capabilities. Phase 9 provides the retrieval foundation that allows these to exist later, but does not build them:

- Reflection
- Advanced consolidation
- Semantic memory evolution
- Importance learning
- Episodic memory redesign
- Knowledge graph features
- OKF redesign

Other deferred items:

- Real-time memory synchronization across sessions
- Multi-user memory isolation
- External memory backends (current LanceDB only)
- Full-text file indexing (future)
- Project-aware retrieval (future — needs a project system)

---

### 5.6 Phase 10 — Planner & Long-running Tasks

#### Purpose

Enable Cozmo to perform tasks that span multiple steps, extended time periods, and session boundaries. Current architecture handles single-turn execution well but has no planning abstraction beyond inline plan generation in runtime. The empty `planner/` module must be built.

#### Goals

- Planning abstraction exists with step generation, dependency resolution, and execution strategies (sequential, parallel, conditional)
- Long-running tasks survive server restarts via checkpoint/restore
- Background task execution with progress tracking
- Job lifecycle is fully wired through planner (not just runtime)
- Task ↔ conversation linking is transparent to user
- Planner can be interrupted, paused, and resumed

#### Primary Deliverables

- `PlannerEngine` — step generation and dependency resolution
- `TaskStore` — task persistence beyond job state
- `PlanPersistence` — plan state storage with incremental checkpoints
- `BackgroundExecutor` — background task execution
- `ProgressTracker` — task progress reporting
- `TaskStore` / `JobStore` integration for cross-session survival

#### Components Introduced

| Component | Role |
|-----------|------|
| `PlannerEngine` | Step generation, dependency resolution, execution strategy |
| `TaskStore` | Persistent task state management |
| `PlanPersistence` | Plan checkpoint/restore (diff-based, incremental) |
| `BackgroundExecutor` | Out-of-band task execution |
| `ProgressTracker` | User-facing task progress |
| `Job-Planner Bridge` | Wiring Job lifecycle to planning execution |
| `ResumeHandler` | Detect interrupted tasks on startup, restore context, continue |

#### Public Interfaces

```python
@dataclass
class PlanStep:
    id: str
    description: str
    dependencies: list[str]
    strategy: str  # SEQUENTIAL, PARALLEL, CONDITIONAL
    status: str
    result: Any | None

@dataclass
class Plan:
    id: str
    task_id: str
    steps: list[PlanStep]
    current_step: int
    status: str
    created_at: datetime
    updated_at: datetime

class PlannerEngine:
    def create_plan(goal: Goal, context: RetrievalContext) -> Plan
    def next_step(plan: Plan) -> PlanStep
    def resolve_dependencies(plan: Plan) -> list[PlanStep]  # ready steps

class TaskStore:
    def save(task: Task)
    def load(task_id: str) -> Task
    def list_active() -> list[Task]
    def list_by_conversation(conversation_id: str) -> list[Task]
```

#### Dependencies

**Must exist:** `JobManager`, `JobStore`, `Checkpoint` (all stable from Phase 6.5). WebUI from Phase 0 capable of displaying task progress. Retrieval abstractions (interfaces, not Phase 9 implementation) for plan-time information needs. Unified retrieval from Phase 9 is a dependency preference — if not yet available, planner uses explicit context passed at invocation time.

**Depends on this phase:** Long-Term Target Architecture.

#### Architectural Constraints

- Planner must not assume implementation details of execution strategy too early — start with SEQUENTIAL, add PARALLEL and CONDITIONAL later
- Planner must not own tool execution, retrieval, or model invocation — those belong to runtime
- Planner must produce plans that runtime can execute; runtime must not need planner awareness
- Checkpointing must be incremental (diff-based) — full-state checkpointing does not scale
- Task ↔ conversation linking must be transparent — user says "continue my portfolio" and the system finds the right task
- Planner must work offline — no cloud dependency for planning

#### Risks

- State explosion for long-running tasks. Mitigation: incremental checkpoints, summary-only persistence for conversational context.
- Planning LLM quality (small models may produce poor multi-step plans). Mitigation: start with step-by-step decomposition, validate each step before execution.
- Over-engineering the planner before understanding real usage patterns. Mitigation: start minimal (sequential steps only), iterate with Phase 8 evaluation.
- Job lifecycle and planner lifecycle having overlapping responsibilities. Mitigation: Jobs own execution lifecycle, Planner owns step logic — distinct concerns.
- Resume across server restarts losing context. Mitigation: incremental checkpointing, restore validation before continuation.

#### Success Criteria

- Planner generates multi-step plans with dependency resolution
- Plans survive server restarts (checkpoint → restore → continue)
- Background task execution reports progress via events
- Task ↔ conversation linking works (resume by conversation reference)
- Phase 8 evaluation shows planning improves task completion for multi-step requests
- Planner works with local models (no cloud requirement)

#### Explicit Non-Goals

- Fully autonomous agent (human-in-the-loop for destructive operations remains)
- Calendar/schedule-based task triggering (future)
- Multi-agent coordination (future)
- Full programming language for task definition
- Visual plan editor (UI concern, future)
- Plan sharing or templates

---

## 6. Cross-Phase Design Constraints

These rules must remain true across all phases. Violations should be treated as design debt.

### 6.1 Runtime Boundary

Runtime coordinates execution. It does not own:

- Retrieval policy decisions
- Evaluation logic or metric collection
- UI behavior or presentation logic
- Planning logic or step generation
- Memory retention policy

**Enforcement:** If a feature can be implemented outside runtime without changing runtime internals, it should be.

### 6.2 Stable Interfaces

Depend on interfaces and protocols, not concrete implementations.

```
Interface / Protocol → Implementation
```

**Applies to:**
- LLM providers
- Retrieval sources
- Memory systems
- Evaluators
- UI clients
- Tool implementations
- Evidence processing

**Enforcement:** All cross-subsystem communication must pass through a Protocol, abstract base class, or event type. Direct imports across subsystem boundaries require architectural review.

### 6.3 Composition Over Inheritance

Independent capabilities compose together. No large class hierarchies.

**Applies to:** Capability registry, tool selection, model routing, evidence processing, retrieval policy.

**Enforcement:** Favor Protocol composition and dependency injection over base class inheritance. Reuse through delegation, not extension.

### 6.4 Events Are the Observation Layer

Events are for:
- Observation
- Lifecycle notifications (started, completed, failed)
- Streaming (tokens, tool calls, trace events)

Events are NOT for:
- Hidden control flow (decisions made in event handlers that affect execution)
- Command dispatch (execute this action)
- Cross-subsystem coupling

**Enforcement:** Event handlers must not mutate execution state. Events describe what happened; they do not prescribe what should happen next.

### 6.5 Replaceability

Every major subsystem must be replaceable without changes to its consumers.

| Subsystem | Replaceable With |
|-----------|-----------------|
| LLM providers | Different provider, different model |
| Retrieval sources | Different search engine, different KB backend |
| Memory systems | Different vector store, different ranking |
| Evaluators | Different framework, different metrics |
| UI clients | Different frontend framework, CLI, API client |
| Tools | Different implementations, different MCP providers |
| Evidence processor | Different extraction strategy, different LLM |

**Enforcement:** Each subsystem has a Protocol or abstract interface. Integration tests verify that a mock implementation can be substituted without breaking consumers.

### 6.6 Measurable Intelligence

Every major capability must have:
- Observable behavior (what does it do?)
- Evaluation criteria (how do we know it works?)
- Regression protection (how do we know it still works?)

**Applies to:** Intent detection, evidence detection, grounding decisions, retrieval policy, evidence processing, memory, planning, tool execution.

**Enforcement:** Each phase must produce or extend evaluation criteria for the capabilities it touches. No phase completes without corresponding benchmark cases.

### 6.7 Subsystem Ownership Boundaries

| Subsystem | Owns | Does Not Own |
|-----------|------|-------------|
| Runtime | Execution loop, streaming, recovery | Policy decisions, evaluation, UI |
| Orchestrator | Analysis, intent/evidence/complexity detection | Execution, I/O, model invocation |
| Retrieval Policy | Source selection, strategy | Execution, I/O |
| Retrieval Coordinator | Budget, dedup, execution gating | Policy decisions, collection |
| Evidence Collector | Search, rank, fetch, merge | Post-processing, confidence |
| Evidence Processor | Facts, conflicts, compression, confidence | Collection, policy |
| Job System | Lifecycle, persistence, checkpoint | Planning logic, execution |
| Memory | Storage, retrieval, importance scoring | Source selection, retention policy |
| Planner | Step generation, dependency resolution | Execution, tool invocation |
| WebUI | Presentation, streaming rendering | Intelligence logic, execution decisions |
| Evaluation | Measurement, metrics, comparison | Runtime control, execution |

---

## 7. Long-Term Target Architecture

### Major Subsystems

```
┌──────────────────────────────────────────────────────────┐
│                     User Interface                        │
│    WebUI (React) │ CLI │ Telegram │ API │ Future Clients  │
└────────────────────────┬─────────────────────────────────┘
                         │ RuntimeInterface Protocol
                         ▼
┌──────────────────────────────────────────────────────────┐
│                    Runtime Layer                          │
│    CozmoRuntime     RetrievalExecutor     ToolExecutor    │
│    Engine           RuntimeTracer        ExecutionContext │
│    RetrievalCoordinator                  LessonStore      │
└──────┬──────────┬──────────┬──────────┬──────────────────┘
       │          │          │          │
       ▼          ▼          ▼          ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│ Analysis │ │Retrieval │ │  Tools   │ │  Memory  │
│ Layer    │ │Pipeline  │ │  Layer   │ │  Layer   │
│          │ │          │ │          │ │          │
│Intent    │ │Policy    │ │Registry  │ │Manager   │
│Evidence  │ │Select    │ │Executor  │ │Index     │
│Complexity│ │Collect   │ │PermGate  │ │Store     │
│Capability│ │Process   │ │MCP Host  │ │          │
│ModelRoute│ │Source    │ │          │ │          │
└──────────┘ └──────────┘ └──────────┘ └──────────┘
       │          │          │          │
       └──────────┴──────────┴──────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│                    Cross-Cutting                          │
│  EventBus     │  ExecutionTrace     │  EvaluationRunner   │
│  JobManager   │  TaskStore          │  PlannerEngine      │
│  Checkpoint   │  Config             │  ModelRouter        │
└──────────────────────────────────────────────────────────┘
```

### Subsystem Responsibilities

**Runtime Layer:** Executes the agent loop. Owns streaming, recovery, and execution policy. Delegates analysis, retrieval, tool execution, and memory to subordinate subsystems. Communicates results via events and traces.

**Analysis Layer:** Transforms user input into structured understanding. Owns intent detection, evidence signal detection, complexity estimation, capability resolution, model selection, and retrieval policy decisions. Produces `TaskAnalysis` consumed by runtime.

**Retrieval Pipeline:** Owns everything between "we need information" and "here is trusted evidence". Policy decides sources. Selector adapts to context. Coordinator enforces budget. Collectors perform I/O. Processor extracts facts, detects conflicts, computes confidence, compresses context. All sources participate through the common `RetrievalSource` contract.

**Tools Layer:** Owns tool registration, execution, permission gating, risk assessment, and MCP provider management. Tools are declarative capabilities with metadata for routing and safety.

**Memory Layer:** Owns persistent storage of conversation history, learned facts, and knowledge base indexes. Provides retrieval and importance scoring. Separate from but consumed by retrieval pipeline. Storage format (LanceDB, OKF, Markdown, etc.) is internal to this layer and evolves independently of the retrieval layer.

**Cross-Cutting Subsystems:**
- **EventBus:** Central typed dispatch. All subsystems emit events. Observers subscribe independently.
- **ExecutionTrace:** Complete observability per execution. Consumed by evaluation, debugging, and UI.
- **JobManager:** Lifecycle management for executions. Submit, pause, resume, cancel, retry.
- **TaskStore:** Persistent task state across sessions.
- **PlannerEngine:** Multi-step plan generation and dependency resolution.
- **ModelRouter:** Resource-aware model selection based on capability requirements.
- **EvaluationRunner:** Automated measurement, comparison, and regression detection.

### Information Flow

```
User Input
    │
    ▼
Analysis Layer ──→ TaskAnalysis ──→ Retrieval Pipeline (if needed) ──→ EvidenceContext
    │                                                                       │
    └───────────────────────────────────────────────────────────────────────┘
                                                                             │
                                                                             ▼
                                                                      Runtime Layer
                                                                             │
                                                    ┌────────────────────────┤
                                                    │                        │
                                                    ▼                        ▼
                                              Model Invocation         Tool Execution
                                                    │                        │
                                                    └────────────────────────┘
                                                                             │
                                                                      (ReAct loop)
                                                                             │
                                                                             ▼
                                                                     Output + Trace
                                                                             │
                                                                             ▼
                                                                       Memory Layer
                                                                             │
                                                                             ▼
                                                                      EventBus (all)
```

### Design Philosophy

**Orchestration over ownership.** The system coordinates capabilities that belong to distinct subsystems. No subsystem holds both the question and the answer.

**Policy before action.** Every decision point has a policy layer: retrieval policy, permission policy, model selection policy, retention policy. Policies are observable and replaceable.

**Events as truth.** What happened is recorded as events. Consumers (UI, eval, memory, logging) observe independently. Execution never depends on a specific consumer being present.

**Measurable by construction.** Every capability produces observable output that feeds evaluation. If you cannot measure it, it does not exist in the architecture.

**Replaceable from day one.** Major subsystem interfaces are defined as Protocols. The system works with stub implementations. Concrete choices (Ollama, LanceDB, etc.) are deployment details.

**Local-first.** All capabilities work offline. Cloud services are optional extensions, never architecture requirements.

**Progressive complexity.** The system starts simple (sequential, single-model, deterministic policy) and layers complexity only when measurement proves it improves outcomes.
