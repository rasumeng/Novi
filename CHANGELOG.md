# Changelog

## v0.3.0 (unreleased)

### Unified Execution Architecture (Milestone 5, Phase E-3)

Every normal execution surface (WebUI, CLI, Telegram, TaskQueue, background runs,
scheduler triggers) now flows through a single `ExecutionCoordinator` seam — no
per-surface execution pipelines, no unplanned direct Runtime calls.

- CLI sessions get a stable `cli:<session_id>` conversation identity
- Telegram is coordinator-backed with a `telegram:<chat_id>` identity; execution
  runs on a worker thread so the async event loop never blocks
- TaskQueue workers, background runs, and scheduled triggers execute through the
  coordinator: each becomes a real Task → Plan → Job → ExecutionHistory chain
- Background/scheduled/queued attempts are tagged with their source
  (`source=background|schedule|taskqueue`, run/schedule ids)
- Duplicate scheduler instances consolidated into the shared `CozmoContext`
  singleton
- Model selection stays centralized (context model service + orchestrator model
  router); surface adapters never hardcode model/performance-profile behavior

### Agent Events (WebSocket — new backend events for activity panel)

- Tool category mapping (`_TOOL_CATEGORIES`) in `runtime.py` — tools tagged as `workspace`, `python`, `web`, `git`, `memory`, or `other`
- `tool_call` yields include `category` field for grouped display in activity panel
- `plan` event now includes structured `steps` array (`{id, description, tool, depends_on, status}`) when `AgentRuntime` provides a `Plan`
- New `progress` event `{current, total, label}` emitted each step during agent execution
- New `agent_state` event `{current_goal, status, tools_used, error?}` emitted at: plan approve, tool record, completion, error
- All new events forwarded through `webui_server.py` WebSocket handler
- Background run handler gracefully ignores `progress` / `agent_state` tuples

### Frontend Types & Hooks

- Added `AgentStateInfo`, `ProgressInfo`, `PlanStepInfo` types
- Updated `ServerEvent` union with `progress`, `agent_state`, `category` on `tool_call`, `steps` on `plan`
- `useCozmoChat` now exposes `agentState` and `progress` state objects
- `pushStep` accepts `toolCategory` field
- `handleEvent` clears `progress` on `done` / `error` / `newChat`

## v0.2.0 (unreleased)

### UI Architecture
- Inline trace components: InlineTraceTimeline, InlineTraceStep, InlinePlanApproval
- Removed right-side panels: ActivityPanel, RightPanel, ActivityCard, StatusIndicator, TerminalPanel, DiffPanel, FileChangeCard
- Trace now renders inline between user/assistant messages
- Thinking bubble with pulsing dots persists across mode switches
- Conversation component shared across Chat/Agent/Code modes

### Streaming Pipeline
- Removed `{` suppression in runtime.py
- Reasoning token capture via `reasoning=True` on ChatOllama
- Chat handler yields `(kind, text)` tuples for unified event processing
- Reasoning and agent_status handlers in frontend WebSocket client
- Plan events properly wired through webui_server.py

### Settings & Configuration
- ModelManager.reload_models() — hot-reload models from config without restart
- ModelManager.set_lightweight_mode() — toggle lightweight mode at runtime
- Fixed deep_merge in webui_server.py preserving models dict on config update
- SettingsModal.save() now persists agent, mcp, personality, memory sections
- Fixed AgentSettings duplicate model source (uses Models tab read-only)
- Fixed ModelSelect width (w-48 → min-w-[180px])
- Fixed CSS typo in ConnectorsSection (border-accept/40 → border-accent/40)
- Expanded SettingsData type with RuntimeConfig, AgentConfig, McpServerConfig

## v0.1.0 (unreleased)

- Initial public release
- CLI agent with specialist model routing (chat, coder, vision, research)
- ChromaDB-backed memory with auto-summarization
- Tool system: calculator, file I/O, web search, code ops, git, desktop, Telegram
- WebUI (React/TypeScript) with streaming, permissions, settings
- Search pipeline with query rewrite, multi-source, content extraction, synthesis
- MCP server protocol support
- Permission system with pattern-based gating (allow/ask/deny)
- Code project index for codebase-aware queries
