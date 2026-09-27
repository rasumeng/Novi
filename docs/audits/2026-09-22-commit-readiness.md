# Commit readiness — September 23, 2026

Status: **ready to review and commit**. No commit has been created.

## Cleanup completed

- Finished the migration from the deleted `NoviRuntime`, trace, and execution
  coordinator paths to `RunService`, `AgentLoop`, and `RunStore`.
- Removed obsolete tests, helpers, parity harnesses, and live diagnostic scripts
  that exclusively exercised the retired runtime.
- Preserved and migrated current graph, retrieval, compaction, recovery,
  evaluation, permission, background delivery, memory, and WebSocket coverage.
- Hid Skills from settings navigation and search, redirected saved Skills routes
  to General, and stopped fetching skills when settings opens. Backend skill
  support remains available.
- Fixed durable event ordering, stable assistant message IDs, background
  progress and cancellation, automatic permission expiry, one-turn memory
  ingestion, composition-root store identity, multimodal attachment delivery,
  deterministic conversation ordering, and non-blocking evaluation timeouts.
- Updated runtime documentation, package metadata, and pytest discovery. Added
  `/.live-home/` to `.gitignore`.
- Fixed Source Folder attachment response handling that could insert an
  undefined source and crash the React tree. Added response validation,
  draft-chat gating, project/conversation index cleanup, consistent source
  timestamps, and excluded-directory pruning during size checks.
- Landing-page source folders now remain temporary until the first message
  creates a conversation. Novi promotes the grants before starting that first
  run, restores persisted sources when chats reopen, and generates a concise
  model-authored title after the first completed response.

## Verification

- Backend full-suite baseline: `python -m pytest -q`: **2,179 passed, 3 skipped**.
  The 268 warnings are existing LanceDB `table_names()` deprecation warnings.
- Backend source/title/persistence checks after the final changes: **18 passed**.
- Frontend: `npm --prefix novi/webui test`: **134 passed**.
- Frontend production build: `npm --prefix novi/webui run build`: **passed**.
- Headless Chromium smoke test: Skills absent from navigation and search, no
  `/api/skills` request, Models usable, and no page errors.
- Source Folder browser smoke test: existing chats attach folders and render
  their chips with no page errors.
- Draft Source Folder browser smoke test: selection makes no source API call;
  first send persists the conversation, promotes its source, and keeps the UI
  free of page errors.
- `git diff --check`: **passed**.
- No live model generation or native Tauri behavior was tested.

## Docker follow-up

Automatic local SearXNG setup is feasible when Docker is installed and its
daemon is available. `novi/searxng_util.py` already has CLI detection, Windows
daemon startup, container startup, and health probes. A user-facing setup flow
still needs API configuration, local-only port binding, bounded image download
and startup handling, clear progress and errors, and reliable container reuse.
That feature is separate from this commit-ready cleanup and remains
unimplemented.
