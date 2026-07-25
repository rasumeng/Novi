# Cozmo Devlog

_Chronological development notes — what changed, why, and impact._

---

### 2026-06-29 — Project inception

**Context**: Build fully local AI agent as alternative to paid agentic AI services (Claude Code, Cursor). Windows 11, Ollama backend, limited GPU — model efficiency critical.

**Existing codebase**: Basic ReAct agent (`main.py`) with calculator tool + RAG pipeline (`rag_local.py`). Both had several bugs.

**Decisions**:
- LangChain for quick wins, decouple later
- TOML config (`tomllib` stdlib)
- Telegram for messaging (simpler than Discord)
- Desktop control read-only initially

---

### 2026-06-29 — Phase 1: Package refactor + Tool system

**Changes**:
- `pyproject.toml` with `cozmo` CLI entry point
- Flat scripts → `cozmo/` package structure
- `config.py` — TOML loader/saver for `~/.cozmo/config.toml`
- `tools/__init__.py` — `TOOL_REGISTRY` + `@register_tool()` decorator
- `cli.py` — argparse: `cozmo init` + `cozmo run [query]`
- Calculator tool with `eval()` → safe AST parser

**Fixed**: 15+ bugs across config, CLI, tool registration, agent loop.

---

### 2026-06-29 — Phase 2: Orchestrator + model routing

**Changes**:
- `core/orchestrator.py` — Hybrid classifier (heuristic + LLM), model router
- Three tiers: fast (phi4-mini), balanced (qwen3:8b), heavy (qwen2.5-coder:14b)
- CLI uses Orchestrator instead of direct Agent

**Fixed**: Silent string concatenation bug in Python, typos in model names, missing config keys.

---

### 2026-06-29 — Phase 3: Memory, web search, desktop, Telegram

**Changes**:
- `memory/chroma_store.py` — ChromaDB with `nomic-embed-text`
- `memory/manager.py` — Short-term buffer (5 turns), auto-summarize, cross-session recall
- `tools/web_search.py` — DuckDuckGo via `ddgs`
- `tools/desktop.py` — Screenshot + clipboard (gated by config)
- `cozmo/telegram_bot.py` — Async Telegram bot

**Fixed**: 22 bugs across 8 files (typos, API mismatches, console encoding).

---

### 2026-06-29 — Specialist model routing refactor

**Changes**:
- `fast/balanced/heavy` tiers → task-specific specialists: `chat`, `coder`, `vision`, `research`
- `config.py` — New model keys; `core/orchestrator.py` — task→model mapping
- `core/agent.py` — `SPECIALIST_PROMPTS` per task type
- `tools/desktop.py` — Auto-analyze screenshots via vision model

**Impact**: Single agent can switch expertise per query without user mode selection.

---

### 2026-06-30 — Cozmo Code

**Changes**:
- `tools/code_ops.py` — 6 code tools: `write_file`, `edit_file`, `grep_search`, `run_command`, `git_diff`, `git_log`
- `code_indexer.py` — ChromaDB project index (respects `.gitignore`)
- `core/code_agent.py` — Code-aware agent with project context, command gating
- Tool format: `<tool>` JSON blocks (replaced regex `TOOL:` parsing)

**Fixed**: 12+ bugs in tool registration (decorator parens), encoding, API mismatches.

---

### 2026-06-30 — CLI UX improvements

**Changes**:
- `config_cli.py` — `cozmo config show|set|reset`
- `cli.py`: `!cmd` passthrough, `/commands`, `@file` autocomplete, status bar
- `prompt_toolkit` integration (FileHistory, fuzzy file completer)
- `core/code_agent.py` — `compact()` method for history summarization

---

### 2026-06-30 — Multi-agent system

**Changes**:
- `core/agent_registry.py` — Agent registry (TOML + `.cozmo/agents/*.md`)
- `core/plan_agent.py` — Read-only plan agent (blocks write/edit/run_command)
- F2 keybinding cycles agents in CLI; `/agent`, `/agents` commands
- Custom markdown agents with frontmatter auto-discovery

---

### 2026-06-30 — Permission system

**Changes**:
- `core/permissions.py` — `PermissionResolver` with `fnmatch` pattern matching
- Unified allow/ask/deny gating at agent level (not in tool functions)
- Session allowlist; `--auto` flag for non-interactive mode
- `code.allow_commands` removed; `[permissions]` config section

---

### 2026-07-01 — Textual TUI (Phase 8)

**Changes**:
- `tui/app.py` — `CozmoApp(Textual.App)` minimal shell
- `tui/sprite.py` — ANSI half-block art from PNG
- `tui/widgets/header.py` — Sprite + title badge

**Decision**: Step-by-step TUI build caught Windows rendering issues early.

---

### 2026-07-01 — CozmoTUI standalone → merged

**Built standalone** `rasumeng/CozmoTUI` repo (no Cozmo deps):
- Three-panel design (Chat/Collab/Code), sidebar, settings modal
- Full UI shell with sprite, themes, keyboard navigation

**Merged back** into `cozmo/tui/` as main TUI implementation, replacing partial Phase 8.

---

### 2026-07-03 — Model selector + Agent harness

**Changes**:
- `tui/screens/model_selector.py` — Queries Ollama `/api/tags`
- `core/chat_agent.py` — ChatAgent (minimal tools, 3-turn loop)
- `core/collab_agent.py` — CollabAgent (Observe-Plan-Act-Reflect, 7-turn)
- `tui/screens/permission.py` — Permission modal with threading bridge
- Streaming format: `(kind, text)` tuples across all agents
- All three panels wired with specialized agents

---

### 2026-07-03 — Comprehensive audit + fixes

**Critical fixes**:
- `q` → `Ctrl+Q` exit (both `app.py` and `main.py` had conflicting bindings)
- Permission modal Escape → signals `False` immediately
- Sidebar typo "Sessoions" → "Sessions"

**Security fixes**:
- `eval(expression)` → safe AST parser (no code execution)
- `subprocess.run(shell=True)` → `shlex.split()` + blocked command list
- File path traversal via `realpath()` check

**Architecture cleanup**:
- `core/base_agent.py` — Shared utilities (parse_tool_call, build_tool_schema)
- All agents extend BaseAgent; chat_mixin.py eliminates panel duplication
- agent.py kept as legacy for Orchestrator path

---

### 2026-07-03 — Phase 2: Orchestrator integration + Markdown

**Changes**:
- Classification display in TUI thinking area
- Memory context injected into all agent prompts
- Context compaction (triggers at 6+ turns)
- Markdown rendering via `textual.widgets.Markdown`
- File picker (`tui/screens/file_picker.py`)
- `Ctrl+L` clear, `Ctrl+Q` exit
- Token count in footer

---

### 2026-07-03 — Phase 3: Toggle mode + CLI deprecation

**Changes**:
- CodeInput Build/Plan toggle actually wired to agent swap
- Context window % display in footer
- CLI `run`/`code` commands marked `[DEPRECATED: use 'tui']`

---

### 2026-07-03 — Web search fix

**Changes**:
- ChatAgent system prompt forces `web_search` for time-sensitive info
- `web_fetch` consolidated into `web_search.py`

---

### 2026-07-08 — CozmoBrain integration

**Merged** standalone CozmoBrain repo:
- `core/mcp_host.py` — MCP stdio client sessions
- `core/router.py` — ToolRouter (keyword + domain priority + LLM fallback)
- `core/context.py` — History trimming, token estimation, compaction
- `core/prompts.py` — Date-aware system prompt builder
- `docker/sandbox.Dockerfile` — Sandboxed Python execution
- `execute_python` tool with Docker → subprocess fallback
- `fetch_url`, date-stamped web search, knowledge CRUD (OKF frontmatter)

**Design**: Brain's `pydantic_ai` → Cozmo's `OllamaModel` via `StatelessLLM` wrapper.

---

### 2026-07-09 — WebUI: Color scheme, sidebar, search, settings

**Progressive WebUI development**:
- Purple theme matching TUI; Cozmo pixel-art sprite
- Conversation pin/rename/delete in sidebar
- Full-text search across conversations
- Settings modal (6 sections: Models, Tools, Memory, Skills, Connectors, General)
- Model presets editor (add/delete custom presets)
- Tool permission mode selectors (Allow/Ask/Deny)
- Microphone STT (Chrome native + MediaRecorder fallback)

---

### 2026-07-10 — File/image attachments, vision routing, projects

**Changes**:
- Attachment upload/serve/delete endpoints (`~/.cozmo/attachments/`)
- Vision model routing (images force `qwen2.5vl:7b` bypassing classifier)
- Project CRUD with shared context injection
- Project wizard UI (create, import from chats, select)

---

### 2026-07-12 — Code Mode UI + Collab mode

**Changes**:
- Code Mode: tool events, terminal/diff/trace panels, inline diffs
- Directory picker, permission mode selector (5 modes)
- Collab mode: plan approval flow (plan → approve/reject)
- Collab Project Management: create, import, select with wizard UI

---

### 2026-07-21 — v2 Architecture migration (mode-based → task-based)

**Complete rewrite** of internal architecture. Replaced mode-based multi-assistant (Chat/Agent/Code/Collab) with task-based single intelligent system.

**New architecture**:
- `runtime/` — Unified `CozmoRuntime.run_stream()` production loop
- `orchestrator/` — Intent detection, complexity estimation, plan creation
- `jobs/` — Long-running job lifecycle (submit/pause/resume/cancel)
- `capabilities/` — Resolvable capability definitions with tool lists
- `planner/` — Step-by-step execution plan generation

**Changes**:
- IntentDetector replaces `core/router.py` mode routing
- `run_stream()` unified across all intent types
- `jobs/manager.py` — Thread-safe job lifecycle
- `orchestrator/policy.py` — Relaxed/normal/strict modes
- `runtime/resources.py` — VRAM tracking, LRU eviction
- `runtime/model_router.py` — Capability-based model selection
- Frontend: removed `WorkspaceMode`, deleted mode-specific components
- `core/` entirely removed (webui_server.py imports migrated)
- `cozmo migrate v1-to-v2` CLI command strips `mode` from persisted conversations

**Test results**: 27 integration tests passing.

---

### 2026-07-22 — Engine activation + Cognitive Layer

**Execution layer**:
- `runtime/engine.py` — Real ReAct loop with checkpoint support
- Duplicate call detection, checkpoint emission/resume
- `CozmoRuntime.run_stream()` accepts `execution_plan` parameter

**Cognitive layer**:
- Intent-based memory type filtering + recency/frequency/distance ranking
- Complexity-aware ModelRouter (upgrades capability when score >= 4)
- `runtime/lessons.py` — LessonStore: tool success/failure patterns
- Scheduler wired via Job system

**Test results**: 64 tests, 0 failures (42 v2 pipeline + 15 execution + 22 cognitive).

---

### Current Status

| Layer | Status | Notes |
|-------|--------|-------|
| **Runtime** | Beta | Unified ReAct loop, execution plans, checkpoint/resume |
| **Orchestrator** | Beta | Intent detection, complexity estimation, plan creation |
| **Cognition** | Alpha | Memory ranking, complexity-aware routing, lesson store |
| **WebUI** | Beta | Full-featured: streaming, permissions, projects, code/collab modes |
| **CLI** | Deprecated | `webui` is primary; `run`/`code` maintained for backward compat |
| **TUI** | Removed | Was split to standalone, then removed in v2 migration |
| **Memory** | Beta | LanceDB + Sentence Transformers (ChromaDB legacy removed) |
| **MCP** | Beta | Stdio transport, catalog, multi-server support |
| **Job System** | Beta | Lifecycle management, persistence, scheduler integration |
| **Capabilities** | Alpha | Declarable capability definitions, builtin registry |

**Remaining gaps**:
- `planner/` module is scaffolded but inactive (plan generation happens in orchestrator)
- `runtime/session.py` removed — session state handled by webui_server.py directly
- `runtime/workspace.py` removed — workspace tracking deferred
- `orchestrator/policy.py`/`continuation.py` removed — policy/continuation deferred
- `runtime/reflection.py` removed — reflection handled by LessonStore
- End-to-end test coverage needs expansion
