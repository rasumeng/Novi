# WebUI Redesign: Align with v2 Architecture

**Date**: 2026-07-22
**Philosophy**: Cozmo is one intelligent assistant. Hide implementation. Surface capability.

---

## Navigation Simplification

### Current Nav Items
```
Conversations | Projects | Memory | Knowledge | Settings
```

### Proposed Nav Items
```
✦ Cozmo

+ New Conversation

──────────────

Conversations

Projects

Jobs           ← NEW

Knowledge

Settings
```

Remove `Memory` (premature for v2). Add `Jobs` as first-class navigation matching `JobManager` architecture.

---

## Components to REMOVE (8 files)

| Component | File | Reason |
|-----------|------|--------|
| `PermissionModeSelector` | `components/chat/PermissionModeSelector.tsx` (86 lines) | Exposes 5 internal permission modes (manual/plan/accept-edits/auto/bypass). Users shouldn't see these. |
| `DirectoryPicker` | `components/chat/DirectoryPicker.tsx` (166 lines) | Directory is implicit from project context. No per-chat directory needed. |
| `AgentTaskPopup` | `components/chat/AgentTaskPopup.tsx` (141 lines) | "Create New Task" / "Import from Chat" — old agent task concept. Project creation is simpler. |
| `CreateProjectWizard` | `components/chat/CreateProjectWizard.tsx` (128 lines) | Over-engineered. ProjectForm is enough (name + description + location). |
| `ImportFromChatPopup` | `components/chat/ImportFromChatPopup.tsx` (142 lines) | Conversation-project linking handled in Projects panel. |
| `ModelBadge` | `components/common/ModelBadge.tsx` (21 lines) | Model role coloring (coder/chat/vision) — single model now, no roles. |
| `AgentSettings` | `components/settings/AgentSettings.tsx` (106 lines) | "Agent Model", "System Prompt", "Max Steps" — no separate agent mode. |
| `PermissionSelect` | `components/settings/PermissionSelect.tsx` | Tool-level permission selects. Simplified in settings. |

---

## Components to MODIFY (17 files)

| Component | Key Changes |
|-----------|-------------|
| **App.tsx** | Strip mode-exposing props from Conversation (`permissionMode`, `onSetPermissionMode`, `agentTask`, `onCreateTask`, `onImportChat`, `currentDirectory`, `onSetDirectory`). Replace `PermissionModal` overlay with inline card. |
| **Conversation.tsx** | Remove ~12 props. Keep only: `conversation`, `connection`, `generating`, `inlineSteps`, `plan`, `onSend`, `onStop`, `onApprovePlan`, `onRejectPlan`, `projects`, `onOpenProjectPanel`, `onOpenSettings`. |
| **PromptInput.tsx** (621 lines, biggest delete) | Strip `DirectoryPicker`, `PermissionModeSelector`, `AgentTaskPopup` imports + JSX + props. Keep: attach file, mic, skill trigger, connectors. |
| **useCozmoChat.ts** | Remove `permissionMode`, `agentTask`, `agentState` state. Add `executionPlan`, `pipelineStatus`, `jobs`. Strip 6 event handlers (`agent_config`, `agent_memory`, `agent_tasks`, `permission_request`, `directory_set`, `agent_state`). Add 6 handlers. |
| **services/cozmo.ts** | Remove 4 WS methods, 6 event types. Add 3-4 WS methods + event types. |
| **Sidebar.tsx** | Add Jobs nav item. |
| **workspaceModes.ts** | Add `'jobs'`. Remove `'memory'`. Order: conversations, projects, jobs, knowledge, settings. |
| **SettingsModal.tsx** | Remove `AgentSettings` section. Simplify models. |
| **ModelsSettings.tsx** | Single model selector instead of per-role. |
| **GeneralSettings.tsx** | Remove "Lightweight Mode" toggle. Remove "Developer Mode" toggle. |
| **PermissionModal.tsx** | Convert from overlay modal to inline card (rendered in conversation flow). |
| **InlinePlanApproval.tsx** | Rename → `ExecutionPlanApproval.tsx`. |
| **InlineTraceTimeline.tsx** | Rename → `ExecutionTimeline.tsx`. |
| **InlineTraceStep.tsx** | Rename → `ExecutionStep.tsx`. |
| **SearchModal.tsx** | Remove `mode` from `SearchResult` interface. |
| **constants.tsx** | Remove `BUILTIN_ROLES`, `PRESET_META`, `PERM_MODES`, `CAPABILITY_DEFS`, `PERMISSION_DEFS`. |
| **types/index.ts** | Remove `AgentTaskFile`, `AgentTaskCreate`, `AgentConfig`, `AgentStateInfo`, `PermissionMode`. Add `ExecutionPlan`, `ExecutionStep`, `PipelineState`, `JobInfo`. |

---

## Components to CREATE (4 files)

| Component | File | Purpose |
|-----------|------|---------|
| **JobsPanel** | `components/jobs/JobsPanel.tsx` | Job list: running/paused/completed/failed. Stop, retry, logs. Accessible from sidebar nav "Jobs". Polls REST or listens WS `job_*` events. |
| **AssistantActivityPanel** | `components/chat/AssistantActivityPanel.tsx` | Live execution view: current step, tools running, progress bar, reasoning text. Inline in conversation. |
| **InlinePermissionCard** | `components/chat/InlinePermissionCard.tsx` | Inline approve/deny card (replaces modal). Shows tool name + args only. Non-blocking. |
| **ProjectContextBar** | `components/projects/ProjectContextBar.tsx` | Active project indicator: name, path, branch, files modified. Shown in conversation header. |

---

## Backend API Changes

### WebSocket events to REMOVE (6):
| Event | Reason |
|-------|--------|
| `agent_config` | No separate agent config |
| `agent_memory` | Agent memory concept gone |
| `agent_tasks` | Task concept replaced by projects |
| `permission_request` | Replaced by simpler inline card |
| `directory_set` | Directory implicit from project |
| `agent_state` | Replaced by engine_status |

### WebSocket events to ADD (4):
| Event | Payload | Purpose |
|-------|---------|---------|
| `execution_step` | `{step_id, type, tool, args, status, result}` | Step-by-step from Engine |
| `engine_status` | `{status, current_step, progress}` | Pipeline heartbeat |
| `job_created` | `{job: JobInfo}` | Job lifecycle event |
| `job_updated` | `{job_id, status, ...}` | Job status change |

### REST endpoints to ADD (4):
- `GET /api/jobs` — list jobs
- `POST /api/jobs/:id/stop` — stop job
- `POST /api/jobs/:id/retry` — retry failed job
- `GET /api/jobs/:id/logs` — get logs

---

## State Management Changes

### Remove from useCozmoChat:
```typescript
permissionMode          // line 27 — old permission concept
agentTask               // line 29 — old task concept
agentState              // line 33 — replaced by pipelineStatus
backgroundRuns          // line 30 — replaced by jobs[]
```

### Add to useCozmoChat:
```typescript
executionPlan: ExecutionPlan | null  // current plan from Orchestrator
pipelineStatus: 'idle' | 'planning' | 'executing' | 'done' | 'error'
jobs: JobInfo[]                      // replaces backgroundRuns[]
```

### Props flow before/after:
```
Before (App → Conversation → PromptInput):
  12 mode-exposing props passed through two levels

After (App → Conversation → PromptInput):
  0 mode-exposing props. All removed.
  Pipeline state goes to AssistantActivityPanel.
  Permission state goes to InlinePermissionCard.
  Project state shown in ProjectContextBar.
```

---

## Implementation Order

1. **Types & constants**: Remove old types in `types/index.ts`, add new ones. Clean `constants.tsx`. (1h)
2. **Backend API**: Add job REST endpoints, add execution_step/engine_status WS events. (2h)
3. **State management**: Strip/rename in `useCozmoChat.ts`. Remove 6 event handlers. (2h)
4. **Component cleanup**: Delete 8 removed files. Clean PromptInput, Conversation, App.tsx. (2h)
5. **New components**: JobsPanel, AssistantActivityPanel, InlinePermissionCard, ProjectContextBar. (3h)
6. **Settings**: Simplify ModelsSettings, remove AgentSettings, clean GeneralSettings. (1h)
7. **Navigation**: Update workspaceModes, Sidebar, WorkspaceNav. (0.5h)
8. **E2E verification**: TypeScript compile, Vite build, manual testing. (1h)

**Total**: ~12.5h
