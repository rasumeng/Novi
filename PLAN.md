# Cozmo Development Plan

## Architecture v3 — Task-Based Execution

Cozmo transitions from a **local AI chatbot with tools** to a **local AI agent operating system**.

Every user request becomes a `Task`. The system determines intent, complexity, strategy, tools, and model — the user never chooses a mode.

```
                         ┌──────────────────┐
                         │   Event Bus      │
                         │  (central pub/sub)│
                         └──────────────────┘
                                │
User Message → Orchestrator → JobManager → Engine (stateless)
                    │              │
               IntentDetector  Job lifecycle
               GoalExtractor   (pause/resume/
               Complexity       cancel/retry/
               CapabilityReg    checkpoint)
               EvidenceDetector
               RetrievalPolicy
               ModelRouter
```

### Core Concepts

| Concept | Role |
|---------|------|
| **Task** | Universal currency. Every request creates one. Has id, goal, status, execution history. |
| **Goal** | What to accomplish. Extracted from user message, resolved via memory for continuations. |
| **Intent** | Kind of work (CODING, RESEARCH, CONVERSATION, PLANNING, VISION). |
| **Job** | Execution instance of a Task. Stateless Engine receives a Job and streams events. |
| **Capability** | Declarative unit of functionality (tools, models, permissions, planner strategy). |
| **Evidence** | Structured information requirements + retrieved evidence for grounding. |
| **Trace** | Full observability per execution: decisions, latency, sources, quality. |

### Architecture Principles

1. **Task is universal currency** — not conversations, not messages, not modes.
2. **Orchestrator is thin** — coordinates, does NOT execute. Delegates everything.
3. **Engine is stateless** — pure ReAct loop. No knowledge of modes, tasks, or intents.
4. **Everything speaks through events** — components subscribe to EventBus.
5. **Policy gates all action** — no destructive operation bypasses policy check.
6. **Memory enriches early** — context resolution precedes planning.
7. **Capabilities are composable** — each has tools, permissions, model needs, risk.
8. **Model routing is resource-aware** — considers VRAM, loaded models, latency.
9. **Retrieval is trustworthy** — evidence has quality signals, not just raw text.
10. **Measurable before complex** — evaluation framework precedes new intelligence layers.

---

## Architecture v3 Current State

### End-to-End Pipeline

```
User Input
    │
    ▼
┌──────────────────────────────────────────────────┐
│  Orchestrator (analyze)                          │
│    IntentDetector     → IntentType                │
│    EvidenceDetector   → EvidenceAnalysis          │
│    ComplexityEstimator→ ComplexityScore           │
│    CapabilityRegistry → capability list           │
│    GroundingDecision  → needs_web_search?         │
│    RetrievalPolicy    → RetrievalPlan (strategy)  │
│  Output: TaskAnalysis                              │
└──────────────────────┬───────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────┐
│  Orchestrator (plan)                              │
│    TaskAnalysis → ExecutionPlan                    │
│    ModelRouter  → model_selection                  │
└──────────────────────┬───────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────┐
│  CozmoRuntime (run_stream)                       │
│    Phase 1: Execute RetrievalPlan                 │
│      - WEB_ONLY           → web search            │
│      - KNOWLEDGE_ONLY     → KB query              │
│      - KNOWLEDGE_THEN_WEB → KB → escalate to web │
│    Phase 2: Memory retrieval                       │
│    Phase 3: Build system prompt                   │
│    Phase 4: Plan generation (if plan_level ≥ 1)   │
│    Phase 5: ReAct loop (inline)                     │
│      - model.invoke → tool_calls?                 │
│      - permission gate → exec → feed back         │
│      - dedup + budget (RetrievalCoordinator)      │
│      - Recovery (upgrade search if needed)        │
│    Output: streaming (token/tool_call/trace)       │
└──────────────────────────────────────────────────┘
```

### Directory Structure (Current)

```
cozmo/
├── orchestrator/       # Coordination — thin, delegates everything
│   ├── orchestrator.py # Event stitcher (~330 lines)
│   ├── intent.py       # IntentDetector + GoalExtractor
│   ├── complexity.py   # ComplexityEstimator
│   ├── evidence.py     # EvidenceDetector (signal detection)
│   └── task_types.py   # Task, Goal, TaskProfile, ExecutionPlan, etc.
│
├── capabilities/       # Declarative capability system
│   ├── registry.py     # CapabilityRegistry (resolve, dependencies)
│   ├── base.py         # Capability dataclass
│   └── builtin.py      # Built-in capabilities (9 types)
│
├── planner/            # Planning strategies — EMPTY (scaffolding only)
│
├── jobs/               # Job lifecycle (COMPLETE)
│   ├── manager.py      # JobManager (thread-safe lifecycle)
│   ├── job.py          # Job, JobStatus, Checkpoint dataclasses
│   └── persistence.py  # JobStore (JSON file persistence)
│
├── runtime/            # Execution fundamentals
│   ├── runtime.py      # CozmoRuntime — production ReAct loop (~1000 lines)
│   ├── engine.py       # Stateless ReAct engine — DEPRECATED (use runtime.py)
│   ├── retrieval.py    # RetrievalExecutor — retrieval plan execution
│   ├── tool_executor.py# ToolExecutor — permission → validate → exec → sanitize
│   ├── tracer.py       # RuntimeTracer — trace creation, events, finalization
│   ├── interface.py    # RuntimeInterface Protocol — typed consumer contract
│   ├── event_bus.py    # Central pub/sub (~40 event types)
│   ├── model_router.py # VRAM-aware model selection
│   ├── resources.py    # VRAM/model/concurrency tracking
│   ├── prompts.py      # System prompt builder
│   ├── context.py      # Token estimation + history trimming
│   ├── trace.py        # ExecutionTrace — full observability
│   ├── evidence.py     # EvidenceCollector (search→rank→fetch→merge)
│   ├── retrieval_policy.py     # RetrievalPolicy (WHERE/HOW to retrieve)
│   ├── retrieval_coordinator.py# Budget + dedup in ReAct loop
│   ├── tool_registry.py
│   ├── tool_risk.py
│   ├── permissions.py
│   ├── lessons.py      # Tool success/failure store
│   ├── execution_context.py # Unified state for one run
│   ├── mcp_host.py     # MCP client sessions
│   └── providers/      # MCP provider (persistent connections)
│
├── providers/          # LLM providers (Ollama, OpenAI)
├── models/             # Model service, registry
├── services/           # Composition root, embedding
├── memory/             # Memory system
│   ├── manager.py      # MemoryManager (OKF classification, importance)
│   ├── knowledge_index.py # OKF → LanceStore indexing + RAG search
│   └── lancedb_store.py   # LanceDB store wrapper
├── tools/              # Tool implementations
│   ├── __init__.py     # TOOL_REGISTRY, @register_tool decorator
│   ├── search_pipeline.py # Multi-search + fetch + rerank pipeline
│   ├── web_search.py   # Web search tool
│   └── ... (calculator, code_ops, desktop, file_ops, etc.)
├── webui/              # React frontend (compiled dist/)
├── webui_server.py     # FastAPI WebSocket + REST server (~1552 lines)
├── webui.py            # WebUI backend builder
├── config.py           # YAML config loader/saver
├── cli.py              # CLI entry point
├── scheduler.py        # Background scheduler
├── task_queue.py       # Async task queue
└── telegram_bot.py     # Telegram bot

tests/
├── regression_corpus.json  # 60+ regression test cases
├── test_evidence.py
├── test_retrieval.py
├── test_grounding.py
├── test_retrieval_coordinator.py
├── test_execution.py
├── test_execution_context.py
├── test_architecture.py
├── test_cognitive.py
├── test_trace_boundary.py
├── test_v2_pipeline.py
├── test_search_pipeline.py
├── benchmark_orchestrator.py
└── evaluate_orchestrator.py
```

---

## Completed

- **Phase 0 (Foundation):** Directory restructuring, dataclass types, aliases.
- **Phase 1 (Unified Pipeline):** Orchestrator (intent/evidence/complexity detection), unified ReAct loop, no mode branching.
- **Phase 2 (Job Manager + Pause/Resume):** JobManager, JobStore, engine checkpointing.
- **Phase 3 (Safety + Awareness):** ResourceManager (VRAM), ModelRouter (VRAM-aware), concurrency gating.
- **Phase 4 (Capability-Based Tool Selection):** CapabilityRegistry.get_tool_names(), ModelRouter.resolve() per capability.
- **Phase 5 (Frontend Redesign):** WorkspaceNav, no mode tabs, unified LandingPage.
- **Phase 6 (Polish + Data Migration):** Deleted old core/, migrate command.
- **Phase 6.5 (Runtime Stabilization):** runtime.py 1814→1000 lines. Extracted ToolExecutor, RetrievalExecutor, ModelRouter, RuntimeTracer, RuntimeInterface. Removed 16+ duplicated inline methods. Cleaned dead imports, stale Phase comments, trailing section headers. Removed `_permission_callback`/`_plan_callback` dead API. Consolidated duplicate analysis/recovery branches. WebUI now exclusively accesses runtime through RuntimeInterface Protocol. 296/296 tests passing.

### Evidence/Retrieval System (Complete)

- EvidenceDetector: 6 signal types (temporal/comparative/locality/project/memory/dynamic)
- GroundingDecision: 3-tier (keyword/heuristic/LLM)
- RetrievalPolicy: NONE / KNOWLEDGE_ONLY / WEB_ONLY / KNOWLEDGE_THEN_WEB
- RetrievalCoordinator: budget enforcement + duplicate query detection
- EvidenceCollector: search → rank → fetch → merge pipeline
- ExecutionTrace: full observability (intent, model, retrieval, tools, latency)

### Memory System (Complete)

- MemoryManager: OKF classification, importance scoring (frequency × recency × distance)
- KnowledgeIndex: OKF markdown → LanceStore → RAG search with reranking
- Hybrid retrieval (vector + keyword)

---

## Roadmap — Future Phases

```
Current → Phase 0 → Phase 7 → Phase 8 → Phase 9 → Phase 10
```

### Phase 0: Finish WebUI + Product Integration

**Goal:** Ensure WebUI is fully integrated with the runtime architecture before adding more intelligence layers.

**Audit current flow:**

```
User → WebUI → WebSocket/API → Runtime → Orchestrator → Tools/Retrieval/Memory → Response
```

Verify all major flows correctly pass through this unified pipeline. No duplicate execution paths.

#### Tasks

**1. Trace System Completion**

TraceEvents are created (`trace.py`) and emitted via WebSocket. User-facing metadata exists (TraceAction: Understanding/Finding/Planning/Using tools/Preparing answer). Debug data (DebugTraceEvent) includes analysis, retrieval, and model details.

What needs improvement:
- UI: Display trace action labels + icons during streaming
- UI: Debug mode toggle to expose/expand debug data
- UI: Surface retrieval quality, source count, confidence to user (non-internal format)
- Ensure internal objects NOT exposed: GroundingDecision, EvidenceAnalysis, RetrievalPolicy internals, Debug state

**2. Conversation Experience**

Current: WebSocket streams (token/tool_call/trace) → UI renders.

Improve:
- Streaming response rendering (real-time token display)
- Trace visualization (show current action with metadata)
- Error states (network, model, tool failures displayed gracefully)
- Retry behavior (user-facing retry for failed operations)
- Conversation persistence (reliability across sessions)
- Loading states (spinner/progress during analysis/retrieval/execution)
- Interrupted generations (detect and allow continuation)
- Long response handling (scroll-to-bottom, expandable)

The conversation tab is the backbone. Must support future features: Projects, Jobs, Memory, Scheduled tasks, Agent workflows.

**3. Verify Runtime Ownership**

Audit for duplicate execution paths:
- Old agent loops? **Complete** — old `core/` deleted.
- Legacy tool execution? **Complete** — unified ToolRegistry.
- Duplicate retrieval systems? **Check** — there are 3 paths in `run_stream` that call `_grounding_search`: retrieval plan path, grounding needs path, fallback intent path. Unify.
- Unused abstractions? **Check** — `planner/` is empty scaffolding. Remove or implement.

Goal: One unified runtime with no mode branching.

---

### Phase 7: Evidence Processing Layer

**Goal:** Turn raw retrieval into trusted evidence with quality signals.

#### Current

```
Search → EvidenceBundle → LLM → Answer
```

EvidenceBundle has: merged_text (flat string), source_count, quality (SUFFICIENT/WEAK/EMPTY/FAILED). No fact extraction, no conflict detection, no confidence scoring.

#### Future

```
Search → EvidenceCollector → EvidenceProcessor
                                  ├── Source Ranking (configurable)
                                  ├── Fact Extraction (LLM)
                                  ├── Conflict Detection
                                  ├── Context Compression
                                  └── Confidence Assessment
                                  ↓
                    Trusted EvidenceContext
                                  ↓
                    LLM → Answer
```

#### Required Components

**1. Source Ranking**
Currently hardcoded (video penalized, text boosted). Make configurable by:
- authority
- relevance
- freshness
- source type
- consistency

Refactor `_rank_sources` into a ranker object with pluggable scoring functions.

**2. Fact Extraction**
Current: raw text → LLM. Future: extract structured facts.

```
Input:  "Version 2.4 releases August 15 according to developers"
Output: Fact{statement: "Version 2.4 releases August 15", confidence: 0.92, sources: [...]}
```

LLM consumes facts instead of raw documents. Reduces token usage, improves reasoning quality.

**3. Conflict Detection**
Detect when sources disagree:

```
Source A: "Update releases August 15"
Source B: "Update delayed until August 20"
```

System detects conflicting claims, confidence differences, preferred sources.

**4. Context Compression**
Prevent context overflow. Compress N search results (5000 tokens) → verified summary (800 tokens).

**5. EvidenceContext Contract**
Stable interface replacing flat `merged_text`.

```python
@dataclass
class EvidenceContext:
    facts: list[Fact]
    sources: list[Source]
    conflicts: list[Conflict]
    confidence: float
    summary: str
```

**Relationship with existing code:**
- `EvidenceCollector` handles search→rank→fetch→merge (raw collection)
- `EvidenceProcessor` handles fact extraction→conflict detection→compression (post-processing)
- `EvidenceProcessor` wraps/complements `EvidenceCollector`, does NOT replace it

---

### Phase 8: Agent Evaluation Framework

**Goal:** Measurable, repeatable evaluation before adding new intelligence layers.

**Why now:** Without evaluation, Phase 7 and Phase 9 will be untestable improvements. Build the yardstick before the race.

**Parallel with existing tests:**
- 60+ regression cases in `regression_corpus.json`
- `evaluate_orchestrator.py` exists but is bespoke
- Need formal framework, not just test files

#### Required Components

**1. Retrieval Success Metrics**
- Was retrieval triggered when needed? (precision/recall of grounding decision)
- Was the correct source chosen? (KB vs web vs none)
- Was evidence sufficient? (quality grade distribution)

**2. Answer Quality Metrics**
- Correctness (ground-truth matching)
- Completeness (coverage score)
- Relevance (signal-to-noise ratio)
- Hallucination rate (factual accuracy)

**3. Tool Efficiency Metrics**
- Steps per task
- Tool calls per task
- Retrieval attempts per task
- End-to-end latency

**4. Regression Test Dataset**

Categories:
- **Temporal:** "What is the latest Minecraft update?"
- **Knowledge:** "Explain recursion"
- **Coding:** "Write a Python decorator"
- **Memory:** "What project was I working on?"
- **Tool use:** "Find files containing 'TODO'"
- **Multi-step:** "Build a portfolio website"

The evaluation framework should:
- Run automatically (CI or scheduled)
- Compare before/after changes
- Surface regressions, not just pass/fail

---

### Phase 9: Memory Retrieval Policy

**Goal:** Make memory a first-class retrieval source in RetrievalPolicy.

#### Current
- `RetrievalPolicy` has 2 sources: KNOWLEDGE (RAG KB), WEB
- Memory is queried separately in runtime (`_query_memory`)
- No unified decision about WHEN to query memory vs KB vs web

#### Future

Unified `RetrievalPolicy` sources:
- Knowledge Base (RAG index of documentation)
- Web (live search)
- Memory (conversation history, learned facts)
- Files (project-specific context)
- Projects (active project state)
- Jobs (ongoing work context)

#### Memory Confidence System

Memory should understand:
- **Importance:** How significant is this memory? (already scored in MemoryManager)
- **Recency:** When was it last accessed/updated?
- **Relevance:** Does it match the current query?
- **Permanence:** Is this a persistent fact or ephemeral context?

Not every conversation becomes permanent memory. System should decide retention policy.

#### Integration Points
- `RetrievalPolicy.resolve()` gains memory signal awareness
- `RetrievalPlan.sources` can include memory
- Runtime executes memory retrieval as part of plan, not as separate path

---

### Phase 10: Long-running Tasks

**Goal:** Allow Cozmo to perform tasks over time, surviving session boundaries.

#### Current
- Job system exists (lifecycle, persistence, checkpointing)
- Planner is EMPTY (placeholder only)
- Task types exist but are not wired into a complete workflow

#### Future

```
User request → Task → Job → Planner → Execution → Checkpoint → Resume
```

**Components to build:**

**1. Task System (use existing types)**
- Task creation, state management, persistence
- Task ↔ Conversation linking

**2. Job System (mostly complete)**
- JobManager: submit, pause, resume, cancel, retry (done)
- JobStore: JSON persistence (done)
- Engine checkpoint events (done)
- Need: better resume across server restarts

**3. Checkpoint System (needs work)**
- Store: progress, state, decisions, artifacts
- Current checkpoint saves model state. Need task-level checkpoint (what was done, what's next).

**4. Planner (build from scratch — currently empty)**
- Step generation
- Dependency resolution
- Execution strategy (sequential, parallel, conditional)
- Plan persistence

**5. Resume Capability**
- Detect interrupted tasks on startup
- Load checkpoint → restore context → continue execution
- User-facing: "Continue my portfolio" picks up where it left off

---

## Architectural Questions — Answered

### 1. Does the current architecture support this roadmap?

**Mostly yes.** The architecture has clean separation between:
- Orchestration (analysis/planning)
- Retrieval (policy/coordination/collection)
- Execution (engine/runtime)
- Jobs (lifecycle/persistence)
- Observability (trace/event bus)

**Gaps:**
- No EvidenceProcessor abstraction (needed for Phase 7)
- RetrievalPolicy only knows KB/WEB (needs Memory for Phase 9)
- Planner is empty (needed for Phase 10)
- No evaluation framework (needed for Phase 8)

### 2. Are RetrievalPolicy, RetrievalCoordinator, EvidenceProcessor, MemoryPolicy, and JobSystem correctly separated?

**Current separation is sound:**
- `RetrievalPolicy` — pure decision (WHERE/HOW to retrieve)
- `RetrievalCoordinator` — execution governor (budget/dedup)
- `EvidenceCollector` — raw collection pipeline (search→rank→fetch→merge)
- `EvidenceProcessor` — NOT YET BUILT (post-collection: facts/conflicts/confidence)
- `MemoryPolicy` — NOT YET BUILT (will be part of RetrievalPolicy extension)
- `JobSystem` — lifecycle management (correctly separated from runtime)

### 3. Are there abstractions missing?

- **EvidenceProcessor** — post-collection evidence refinement (Phase 7)
- **EvaluationRunner** — automated eval harness (Phase 8)
- **MemoryRetrievalSource** — memory as a retrieval source (Phase 9)
- **PlannerEngine** — step generation + dependency resolution (Phase 10, currently empty)
- **TaskStore** — task persistence beyond job state (Phase 10)

### 4. Should any phases be reordered?

**Yes. Phase 8 (Evaluation) should come before Phase 7 (Evidence Processing).**

Rationale:
- Evidence processing introduces fact extraction, conflict detection, and confidence scoring — all LLM-dependent operations that can silently degrade quality
- Without an evaluation framework, you cannot measure whether the evidence processor improves or harms answers
- Build the measurement tool before the feature it measures

**Suggested order: Phase 0 → Phase 8 → Phase 7 → Phase 9 → Phase 10**

### 5. Where will the current design create technical debt?

1. **Three model routing paths in `runtime.py`:** ExecutionPlan path, TaskAnalysis path, fallback intent path. These should be unified. They duplicate logic and create branching complexity.

2. **Retrieval execution has 3 call sites for `_grounding_search`:** Plan execution path, grounding needs path, fallback intent path. Unify into a single entry point.

3. **Runtime.py is ~1814 lines.** Growing. Should be split: retrieval execution, plan generation, tool routing, and the main loop should be separate modules.

4. **EvidenceCollector uses flat `merged_text`.** Moving to structured `EvidenceContext` (Phase 7) will be a breaking change for all consumers. Plan migration carefully.

5. **Planner module is empty.** This creates the risk that Phase 10 needs to build planner from scratch while also integrating with jobs, tasks, and checkpointing — high coupling risk.

6. **Memory and KB are separate systems.** They should share a retrieval interface so RetrievalPolicy can treat them uniformly.

### 6. What should be simplified before adding complexity?

1. **Refactor runtime.py** — Split into focused modules:
   - `runtime/loop.py` — main run_stream orchestration
   - `runtime/retrieval.py` — all retrieval execution (extract from runtime.py)
   - `runtime/model_routing.py` — model selection logic
   - `runtime/planning.py` — plan generation (currently inline)

2. **Unify model routing paths** — Single entry point, remove the 3-branch model selection in `run_stream`.

3. **Remove dead scaffolding** — Either implement `planner/` or remove it.

4. **Standardize tracing pattern** — `_trace_event()` is called with different argument patterns across the codebase. Create a single `emit_trace()` method.

5. **Retrieval execution cleanup** — Merge the 3 call sites for `_grounding_search` into one method that the runtime calls uniformly.

---

## Implementation Order (Recommended)

```
Phase 0: Finish WebUI + Product Integration
  ↓ (foundation for all debugging)
Phase 8: Agent Evaluation Framework
  ↓ (measure before improving)
Phase 7: Evidence Processing Layer
  ↓ (trustworthy retrieval)
Phase 9: Memory Retrieval Policy
  ↓ (unified retrieval)
Phase 10: Long-running Tasks
  ↓ (autonomous agents)
```

### Within Each Phase

1. **Design first** — Stable interfaces before implementation
2. **Test concurrently** — Write evaluation tests alongside features
3. **Refactor before build** — Clean existing debt before adding new layers
4. **Measure after** — Run evaluation before/after to verify improvement

---

## Technical Risks

1. **LLM quality gate for evidence processing (Phase 7):** Fact extraction, conflict detection, and confidence assessment all depend on LLM quality. Small local models (3B-8B) may produce unreliable extractions. Plan: use smaller extraction tasks (classify, not generate), fallback to raw text when confidence is low.

2. **Memory confidence system complexity (Phase 9):** Determining importance/relevance/permanence requires either heuristics (brittle) or LLM calls (expensive). Start with heuristic scoring (already exists in MemoryManager), iterate with eval feedback.

3. **Long-running task state explosion (Phase 10):** Checkpointing complex multi-step tasks generates large state. Plan: incremental checkpoints (diff-based), summary-only persistence for conversational context.

4. **WebUI ↔ Runtime coupling:** The WebSocket protocol currently passes trace events as dicts. As trace schema grows (Phase 7 EvidenceContext, Phase 9 memory details), maintain backward compatibility. Version the trace schema.

5. **EvidenceContext migration (Phase 7):** Changing EvidenceBundle.merged_text → EvidenceContext affects the system prompt builder, LLM consumption, and tracing. Migration plan: add EvidenceContext alongside merged_text, migrate consumers one by one, then remove merged_text.
