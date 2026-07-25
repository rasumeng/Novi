# Cozmo — Open-Source Local AI Assistant

Cozmo is a fully local, privacy-first AI assistant. Task-based intelligent system — determines intent, selects tools, routes to optimal model automatically. No mode selection, no cloud dependency.

```bash
cozmo webui     # → http://127.0.0.1:8765
```

---

## Core Philosophy

- **One intelligent assistant** — No chat/agent/code mode switching. Every request is a Task; system handles classification.
- **Local-first** — Everything runs via Ollama. No data leaves your machine.
- **Model agnostic** — Provider abstraction (Ollama/OpenAI). Model-per-role routing.
- **Extensible** — 20+ built-in tools, MCP server support, custom capabilities.
- **Event driven** — Streaming events, pub/sub event bus, tool call/results, planning.
- **Privacy focused** — Telemetry optional, no user tracking, local vector store.

---

## Architecture

```
User Input → IntentDetector → ComplexityEstimator → Orchestrator.plan()
                                                         ↓
                                               ExecutionPlan
                                                         ↓
                                              CozmoRuntime.run_stream()
                                                    ↓
                                        Engine (ReAct loop) → Tool calls
                                                    ↓
                                            Final response
```

### Runtime (`cozmo/runtime/`)
- `CozmoRuntime` — production execution loop
- `Engine` — stateless ReAct loop with checkpoint support
- `ModelRouter` — capability-based model selection with resource awareness
- `PermissionResolver` — pattern-based allow/ask/deny gating
- `EventBus` — typed event pub/sub (tool calls, results, plan steps)
- `LessonStore` — persistent tool success/failure patterns
- `MCPHost` — MCP stdio client sessions

### Orchestrator (`cozmo/orchestrator/`)
- `IntentDetector` — classifies user input (conversation/research/coding/planning/vision)
- `ComplexityEstimator` — scores task complexity for model routing
- `Orchestrator` — intent → plan → execution pipeline

### Job System (`cozmo/jobs/`)
- `JobManager` — submit/pause/resume/cancel/retry lifecycle
- `JobStore` — JSON persistence
- `Job` — dataclass with status, events, checkpoint support

### Capabilities (`cozmo/capabilities/`)
- `CapabilityRegistry` — declarable units of functionality with tool lists
- Builtin capabilities: conversation, research, coding, planning, vision

### Memory (`cozmo/memory/`)
- `LanceStore` — LanceDB vector store with hybrid search
- `MemoryManager` — short-term buffer, LLM summarization, importance scoring
- `KnowledgeIndex` — knowledge base indexing (OKF markdown with YAML frontmatter)

### Providers (`cozmo/providers/`)
- `LLMProvider`, `OllamaProvider`, `OpenAIProvider` — provider-agnostic model inference
- `MCPManager` — persistent MCP server connections with health checks

### Event Flow
```
Client (WebUI) ←WS→ WebUIServer ←→ CozmoRuntime ←→ Engine ←→ Tools/Memory/MCP
                        ↓
                  EventBus (pub/sub)
                        ↓
                 Jobs, Lessons, Logging
```

---

## Features

### Implemented
- Task-based single-assistant architecture (no mode selection)
- Intent detection (conversation, research, coding, planning, vision)
- Complexity-aware model routing
- 20+ tools: calculator, file I/O, code ops, web search, git, desktop, knowledge CRUD, subagent spawning
- MCP protocol support (stdio transport, multi-server, catalog)
- LanceDB memory with hybrid search and importance scoring
- Knowledge base indexing (OKF markdown with YAML frontmatter)
- Permission system (allow/ask/deny, pattern matching, session allowlists)
- Job system (lifecycle management, persistence, scheduler integration)
- Lesson store (tool success/failure reflection)
- WebUI (React/TypeScript) with streaming, permissions, projects, code/collab modes
- Model presets editor, tool permission mode selectors
- Conversation management (search, pin, rename, delete)
- File/image attachments with vision routing
- Project grouping with shared context injection
- Code mode: inline trace, terminal/diff/timeline, directory picker
- Collab mode: plan approval flow, project wizard
- Speech-to-text (Chrome native + MediaRecorder fallback)
- Provider abstraction (Ollama, OpenAI)
- CLI: `cozmo webui`, `cozmo run`, `cozmo code`, `cozmo config`, `cozmo telegram`

### In Progress
- End-to-end test coverage
- Context window management across long sessions
- Streaming MCP notifications
- Full desktop automation

### Planned
- Plugins/extensions system
- Codebase-aware context compaction
- Multi-modal inline rendering (charts, diagrams)
- Background agent scheduling with job monitoring UI

---

## Quick Start

```bash
git clone https://github.com/rasumeng/cozmo.git
cd cozmo
pip install -e .
pip install -e .[telegram]  # optional, for Telegram bot

cozmo init                   # creates ~/.cozmo/config.toml
cozmo webui                  # launch at http://127.0.0.1:8765
cozmo run "hello"            # or CLI quick query
```

Requires Python >= 3.10 and [Ollama](https://ollama.ai) running locally with models pulled.

---

## Configuration

Config lives at `~/.cozmo/config.toml`. Managed via WebUI Settings modal, `cozmo config` CLI, or direct editing.

### Models (`[llm.roles]`)
Per-role model dispatch. Each role specifies a model name — empty falls back to `default_model`.

```toml
[llm]
default_model = "qwen3:8b"

[llm.roles]
chat = { model = "" }
coder = { model = "" }
vision = { model = "" }
planner = { model = "" }
classifier = { model = "" }
router = { model = "" }
orchestrator = { model = "" }
```

### Providers (`[providers]`)
```toml
[providers]
default = "ollama"

[providers.ollama]
url = "http://localhost:11434"

[providers.openai]
api_key_env = "OPENAI_API_KEY"
```

### Memory (`[memory]`)
```toml
[memory]
max_turns_before_summary = 5
max_short_term_pairs = 10
```

### Runtime (`[runtime]`)
```toml
[runtime]
lightweight_mode = false
max_history = 10
max_steps = 8
max_tool_output_chars = 8000

[runtime.temperatures]
chat = 0.6
work = 0.0
research = 0.2
```

### Permissions (`[permissions]`)
```toml
[permissions]
write_file = "ask"
edit_file = "ask"

[permissions.run_command]
"*" = "ask"
"git *" = "allow"
"dir *" = "allow"
```

### Environment Variables
| Variable | Override |
|----------|----------|
| `COZMO_DEFAULT_MODEL` | Default LLM model |
| `COZMO_MODEL_CHAT` | Chat role model |
| `COZMO_MODEL_CODER` | Coder role model |
| `COZMO_MODEL_VISION` | Vision role model |
| `COZMO_MODEL_PLANNER` | Planner role model |
| `COZMO_OLLAMA_URL` | Ollama server URL |
| `COZMO_EMBED_MODEL` | Embedding model |
| `COZMO_TELEGRAM_BOT_TOKEN` | Telegram bot token |

---

## Tool System

Registered tools auto-discovered by the runtime. Tools not listed default to MEDIUM risk.

| Category | Tools |
|----------|-------|
| **File** | `read_file`, `write_file`, `edit_file`, `list_directory`, `glob_search`, `read` |
| **Code** | `grep_search`, `run_command`, `execute_python`, `git_diff`, `git_log` |
| **Web** | `web_search`, `web_search_pipeline`, `web_fetch`, `fetch_url` |
| **Knowledge** | `read_knowledge`, `write_knowledge`, `search_knowledge` |
| **Desktop** | `screenshot`, `clipboard_read`, `analyze_image` |
| **Math** | `calculator` (safe AST parser) |
| **Comm** | `telegram_send` |
| **Agent** | `task` (subagent spawner) |
| **Scheduling** | `schedule_task`, `list_schedules`, `remove_schedule` |
| **Diagnostics** | `diagnostics`, `sourcegraph` |

---

## Package Structure

```
cozmo/
├── runtime/            # Production execution loop, engine, model routing, permissions
│   ├── providers/      # MCP provider (persistent connections)
│   ├── engine.py       # Stateless ReAct loop with checkpoint support
│   ├── runtime.py      # CozmoRuntime — unified pipeline
│   ├── model_router.py # Capability-based model selection
│   ├── event_bus.py    # Typed pub/sub event system
│   ├── permissions.py  # Pattern-based allow/ask/deny
│   ├── tool_registry.py# Tool registration and LangChain wrapping
│   ├── tool_risk.py    # Risk classification for permission defaults
│   ├── mcp_host.py     # MCP stdio client sessions
│   ├── resources.py    # VRAM tracking, model ranking
│   ├── lessons.py      # Tool success/failure store
│   ├── context.py      # History trimming, token estimation
│   └── prompts.py      # System prompt builder
│
├── orchestrator/       # Intent detection, complexity, plan creation
├── jobs/               # Job lifecycle management
├── capabilities/       # Declarable capability definitions
├── planner/            # Planning strategies (scaffolding)
├── providers/          # Provider abstraction (Ollama, OpenAI)
├── models/             # Model service, registry
├── services/           # CozmoContext composition root, embedding
├── memory/             # LanceDB store, memory manager, knowledge index
├── tools/              # 20+ registered tools
├── webui/              # React/TypeScript frontend (Vite + Tailwind)
├── webui_server.py     # FastAPI WebSocket + REST server
├── webui.py            # WebUI backend builder
├── cli.py              # CLI entry point
├── config.py           # TOML config loader/saver with migration
├── config_cli.py       # cozmo config show|set|reset
├── migrate.py          # v1-to-v2 data migration
├── ollama.py           # Ollama process management
├── scheduler.py        # Background agent scheduler
├── task_queue.py       # Async task queue
├── telegram_bot.py     # Telegram bot integration
├── code_indexer.py     # ChromaDB project indexer
├── searxng_util.py     # SearXNG search utility
├── default_skills/     # Bundled skills
└── docker/             # Sandbox Dockerfile for execute_python
```

---

## Status

| Layer | Status | Notes |
|-------|--------|-------|
| **Runtime** | Beta | Unified ReAct loop, execution plans, checkpoint/resume |
| **Orchestrator** | Beta | Intent detection, complexity estimation, plan creation |
| **Memory** | Beta | LanceDB hybrid search, importance scoring, knowledge index |
| **WebUI** | Beta | Streaming, permissions, projects, code/collab, STT |
| **MCP** | Beta | Stdio transport, catalog, multi-server, health checks |
| **Cognition** | Alpha | Memory ranking, complexity-aware routing, lessons |
| **Job System** | Beta | Lifecycle, persistence, scheduler integration |
| **CLI** | Deprecated | `webui` is primary; `run`/`code` maintained |

---

## License

MIT
