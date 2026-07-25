# Cozmo Development Plan

## Architecture v3 — Task-Based Execution

Cozmo transitions from a **mode-based multi-assistant** (Chat/Agent/Code with separate pipelines)
to a **task-based single intelligent system**. Every user request becomes a `Task`.
The system determines intent, complexity, strategy, tools, and model — the user never chooses a mode.

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

### Architecture Principles

1. **Task is universal currency** — not conversations, not messages, not modes.
2. **Orchestrator is thin** — coordinates, does NOT execute. Delegates everything.
3. **Engine is stateless** — pure ReAct loop. No knowledge of modes, tasks, or intents.
4. **Everything speaks through events** — components subscribe to EventBus.
5. **Policy gates all action** — no destructive operation bypasses policy check.
6. **Memory enriches early** — context resolution precedes planning.
7. **Capabilities are composable** — each has tools, permissions, model needs, risk.
8. **Model routing is resource-aware** — considers VRAM, loaded models, latency.

---

## Migration Plan (6 Phases)

### Phase 0: Foundation (COMPLETE)
**No behavior change.** Restructure directories, create dataclass types, set up aliases.

Create:
- `cozmo/runtime/` — core execution (engine.py, event_bus.py, model_router.py, resources.py, etc.)
- `cozmo/orchestrator/` — task_types.py (Task, Goal, TaskProfile, ExecutionPlan, ExecutionHistory)
- `cozmo/jobs/` — job.py (Job, JobStatus, Checkpoint)
- `cozmo/capabilities/` — registry.py, base.py, builtin.py
- `cozmo/planner/` — planning strategies (scaffolding)

Add:
- `force_capability` / `force_model` parameters (developer override, not user-facing)

### Phase 1: Unified Pipeline
**Core migration.** The ReAct loop no longer branches on mode.

- Replace `core/router.py` with `orchestrator/intent.py` (IntentDetector)
- Build `orchestrator/complexity.py` (ComplexityEstimator)
- Build `orchestrator/orchestrator.py` (lightweight coordinator, ~150 lines)
- Remove mode branching from `engine.py` (`_MODE_DISCIPLINE`, `_tool_gate`, per-mode temps)
- Remove `force_mode` from pipeline (keep as deprecated compat for 1 release)
- Build `runtime/model_router.py` (capability-based model selection)
- Build `capabilities/builtin.py` (Python-based capability definitions)
- Unify `webui_server.py` pipeline (no more force_mode passthrough)

### Phase 2: Job Manager + Pause/Resume
**Long-running tasks become proper Job objects.**

- `jobs/manager.py` — JobManager (submit, pause, resume, cancel, retry)
- `jobs/persistence.py` — save/load Jobs and Checkpoints
- Engine yields checkpoint events periodically
- Continuation ("keep going") loads Task + last Job checkpoint
- Background runs unified with foreground JobManager

### Phase 3: Safety + Awareness
**Policy gates all tasks. Resource tracking.**

- `orchestrator/policy.py` — PolicyEngine (permission mode, destructive patterns, workspace trust)
- `runtime/resources.py` — ResourceManager (VRAM, loaded models, concurrency)
- ModelRouter consults ResourceManager before selecting model

### Phase 4: Capability-Based Tool Selection
**Tools driven by capability resolution, not mode gating.**

- `capabilities/registry.py` — resolves task profile → capability list → tool set
- Remove `_tool_gate` completely
- Any tool available to any task if capability supports it

### Phase 5: Frontend Redesign (COMPLETE)
**No mode tabs. Workspace navigation.**

- Removed `WorkspaceMode`, `WorkspaceTabs`, `workspaceModes.ts`
- Built `WorkspaceNav`: Conversations | Projects | Memory | Knowledge | Settings
- Unified LandingPage (no per-mode colors/logos)
- Simplified PromptInput (no mode-conditioned UI)
- Removed mode from WebSocket protocol, conversation persistence

### Phase 6: Polish + Data Migration (COMPLETE)
**Clean up. Migrate. Release.**

- `cozmo migrate v1-to-v2` — strips `mode` from persisted conversations
- Deleted `cozmo/core/`, `cozmo/core/agent/`, `cozmo/core/chat/`
- Removed old sidebar components (WorkspaceTabs.tsx)
- Updated docs, bumped version to 0.2.0

---

## Directory Structure (Current)

```
cozmo/
├── orchestrator/       # Coordination — thin, delegates everything
│   ├── orchestrator.py # Event stitcher
│   ├── intent.py       # IntentDetector + GoalExtractor
│   ├── complexity.py   # ComplexityEstimator
│   └── task_types.py   # Task, Goal, TaskProfile, ExecutionPlan, ExecutionHistory
│
├── capabilities/       # Declarative capability system
│   ├── registry.py     # CapabilityRegistry
│   ├── base.py         # Capability dataclass
│   └── builtin.py      # Built-in capabilities
│
├── planner/            # Planning strategies (scaffolding)
│
├── jobs/               # Job lifecycle
│   ├── manager.py      # JobManager
│   ├── job.py          # Job, JobStatus, Checkpoint
│   └── persistence.py  # Checkpoint persistence
│
├── runtime/            # Execution fundamentals
│   ├── engine.py       # Stateless ReAct loop
│   ├── event_bus.py    # Central pub/sub
│   ├── model_router.py # Cost-aware model selection
│   ├── resources.py    # VRAM/model/concurrency tracking
│   ├── prompts.py      # System prompt builder
│   ├── context.py      # Token estimation
│   ├── tool_registry.py
│   ├── tool_risk.py
│   ├── permissions.py
│   ├── lessons.py      # Tool success/failure store
│   ├── mcp_host.py     # MCP client sessions
│   ├── runtime.py      # CozmoRuntime — production loop
│   └── providers/      # MCP provider (persistent connections)
│
├── providers/          # LLM providers (Ollama, OpenAI)
├── models/             # Model service, registry
├── services/           # Composition root, embedding
├── memory/             # Memory (knowledge + retrieval)
├── tools/              # Tool implementations
├── webui/              # React frontend
├── webui_server.py     # FastAPI WebSocket + REST server
├── webui.py            # WebUI backend builder
├── config.py           # Config loader/saver
├── config_cli.py       # Config CLI
├── cli.py              # CLI entry point
├── migrate.py          # Data migration
├── ollama.py           # Ollama process management
├── scheduler.py        # Background scheduler
├── task_queue.py       # Async task queue
├── telegram_bot.py     # Telegram bot
├── code_indexer.py     # Project indexer
└── searxng_util.py     # Search utility
```

---

## Completed

- Phase 0 architecture restructuring
- Task/Goal/Job dataclass types
- Runtime/Engine/ModelRouter/ResourceManager skeletons
- force_capability / force_model config params
- **Phase 1: Unified pipeline**
  - `orchestrator/intent.py` — IntentDetector + GoalExtractor
  - `orchestrator/complexity.py` — ComplexityEstimator (heuristic-based)
  - `orchestrator/orchestrator.py` — lightweight coordinator (~150 lines)
  - `runtime/runtime.py` — unified ReAct loop (no mode branching)
  - `runtime/runtime.py` — `force_mode` deprecated

- **Phase 2: Job Manager + Pause/Resume**
  - `jobs/manager.py` — JobManager (submit, pause, resume, cancel, retry, start, complete)
  - `jobs/persistence.py` — JobStore (JSON file persistence for jobs and checkpoints)
  - `runtime/engine.py` — checkpoint_interval + Checkpoint events + resume support

- **Phase 3: Safety + Awareness**
  - `runtime/resources.py` — ResourceManager with concurrency gating
  - ResourceManager: VRAM tracking, model load/unload with OOM prevention
  - ResourceManager: LRU eviction, `best_available()` ranking
  - `runtime/model_router.py` — consults ResourceManager for VRAM/loaded status

- **Phase 4: Capability-Based Tool Selection**
  - `runtime/runtime.py` — `_tools_for_mode` accepts `allowed_tools` list from capability resolution
  - `runtime/runtime.py` — resolves capabilities via `CapabilityRegistry.get_tool_names()`
  - `runtime/runtime.py` — selects model via `ModelRouter.resolve()` using capability

- **Phase 5: Frontend Redesign**
  - Removed `WorkspaceMode`, `WorkspaceTabs`, `workspaceModes.ts`
  - Built `WorkspaceNav`: Conversations | Projects | Memory | Knowledge | Settings
  - Unified LandingPage, simplified PromptInput
  - Removed mode from WebSocket protocol, conversation persistence
  - TypeScript compiles clean with zero mode references

- **Phase 6: Polish + Data Migration**
  - Deleted `cozmo/core/` (old agent/chat/providers code)
  - WebUI server migrated to new architecture
  - `cozmo migrate v1-to-v2` — strips `mode` from persisted conversations
  - Bumped version to 0.2.0
