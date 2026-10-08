# Changelog

## v0.3.0-beta.1

First downloadable beta. Windows x64 only.

### Installing

Download `Novi_*-x64-setup.exe` from the
[releases page](https://github.com/rasumeng/Novi/releases) and run it.

> **Windows will warn on first run.** This beta is unsigned, so SmartScreen
> shows "Windows protected your PC". Choose **More info → Run anyway**.
> Nothing is wrong with the download.

The installer is per-user — no administrator rights needed.

### Distribution

- NSIS installer only; the MSI target was dropped so there is one installer and
  one update path
- Releases are published by pushing a version tag. Merging to `main` never
  publishes anything
- Published as a normal GitHub release, not a prerelease, because the updater
  reads `releases/latest/download/latest.json` and that endpoint excludes
  prereleases — marking these as prereleases would break updates silently

### Automatic updates

- In-app update check on launch, throttled to at most once per 24 hours
- Offers to download, install, and relaunch
- Every update is verified against a committed signing key before anything is
  written to disk
- Fails silently and harmlessly when offline — an update check never blocks or
  breaks launch

### Packaging correctness

These were defects that would have shipped broken installers:

- The version was duplicated across four manifests with no single writer, and
  `package-lock.json` had already drifted to `0.1.0` while everything else read
  `0.2.0`. `scripts/release.py` is now the only writer, and `check` fails on skew
- `tests/test_beta_hardening.py` pinned the literal string `"0.2.0"`, which would
  have failed the suite on the first release while checking nothing about the
  other manifests
- The release workflow refused to publish when the PyInstaller sidecar was
  missing. `bundle.resources` silently bundles whatever is present, so a skipped
  backend build produced an installer that only failed at runtime
- The release workflow now asserts the published release contains `latest.json`
  and fails loudly if it does not, instead of shipping an installer whose
  updates would 404 with no visible error

### Known limitations in this build

- **Ollama must be installed separately.** It is not bundled and this build does
  not provision it. First-run provisioning lands in a later release.
- **No chat model is provisioned automatically.** On first launch Novi silently
  pulls the embedding model (~274 MB) with no prompt or progress indicator, and
  `llm.primary_model` starts empty — choose a model in Settings → Models. An
  onboarding flow that detects hardware, requests consent with a real download
  size, and verifies with a live inference is the next piece of work.
- The backend still crashes without supervision if it dies mid-session; you must
  quit and relaunch. A supervisor and a persistent log file come later.
- Backend logs are kept in memory only and discarded, since a release build has
  no console.
- Long-term memory and the Brain remain gated off (`memory.enabled = false`,
  `brain.enabled = false`), per `docs/BETA_SCOPE.md`.
- PyInstaller `--onefile` can trip antivirus heuristics on managed machines.

### Verification

- 2250 Python tests, 155 frontend tests, 2 Rust tests
- The upgraded Tauri runtime (`tauri` 2.12.1, `wry` 0.57.0) was smoke-tested
  against a live build: window, webview load, global shortcut, single-instance
  handling, and stale-backend reaping all confirmed working
- Known gaps: the installer has never been built by CI end to end, and the
  updater path (`check → download → verify → install → relaunch`) is unproven
  until a second tagged release exists

---

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
