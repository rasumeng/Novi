# Architecture Audit — v2 Stabilization

**Date**: 2026-07-22
**Scope**: Full codebase audit after v2 architecture migration (Engine activation + Cognitive Layer completion)
**Files scanned**: 71 Python files under `cozmo/`, 4 test files, full WebUI TypeScript frontend

---

## Prioritized Findings

### P0 — Critical (fix before v3 work)

#### 1. Engine Not Wired — Inline ReAct Loop Duplicated in Runtime

**File**: `cozmo/runtime/engine.py` (300 lines)
**File**: `cozmo/runtime/runtime.py` (lines 776-852)

`Engine.run_stream()` is a fully implemented stateless ReAct loop with checkpoint support, duplicate call detection, and resume capability. **Zero external callers.** The only reference is `Engine.run()` at line 297 which calls itself — a wrapper that just iterates the generator.

Meanwhile, `CozmoRuntime.run_stream()` implements a **second, independent ReAct loop** inline (lines 776-852) that duplicates: model invocation (`runnable.stream`), tool call extraction, duplicate detection, checkpoint logic, and event yielding.

**Impact**: Two ReAct loops to maintain. Checkpoint/resume only works if Engine is used — inline loop lacks that. Any fix to ReAct behavior must be applied in both places.

**Fix**: Replace the inline loop in `runtime.py:776-852` with `Engine.run_stream()`. Pass `model_fn`, `execute_tool`, and `on_event` callables. Effort: **4-6h** (refactoring, no new behavior).

---

#### 2. PolicyEngine Defined But Never Called

**File**: `cozmo/orchestrator/policy.py` (defines `PolicyEngine.decide()`)
**File**: `cozmo/orchestrator/orchestrator.py` (zero references to PolicyEngine)

`policy.py` documents that `Orchestrator.plan() → PolicyEngine.decide()`, but the actual `Orchestrator.plan()` method never imports or calls it. Permission checking happens ad-hoc inside `CozmoRuntime._check_permission()` (runtime.py) using a `PermissionResolver` + risk level system.

**Impact**: PolicyEngine serves zero purpose. The documented architecture is misleading. Permission enforcement is split between a completely dead class (`PolicyEngine`) and the live-but-different system in `runtime/permissions.py`.

**Fix**: Delete `policy.py`. Rename `runtime/permissions.py` to `runtime/policy.py` and export as `PolicyResolver` if the name is desired. Effort: **1h**.

---

#### 3. `tool_call` / `tool_result` Events Sent Twice Over WebSocket

**File**: `cozmo/webui_server.py:512-523` (Session.start_run emits)
**File**: `cozmo/webui_server.py:411-418` (Session._on_bus_event emits)
**File**: `cozmo/runtime/runtime.py:825-837` (_emit_bus calls)

Every tool execution step sends two identical WebSocket messages:
- **Path A**: `runtime.run_stream()` yields `("tool_call", ...)` / `("tool_result", ...)` → `Session.start_run()` matches by `kind` and calls `self._emit()` (lines 512-523)
- **Path B**: `runtime.run_stream()` calls `self._emit_bus("tool_called", ...)` / `self._emit_bus("tool_result", ...)` (lines 826, 837) → EventBus forwards to `Session._on_bus_event()` (lines 411-418) → calls `self._emit()` again

**Impact**: Frontend receives duplicate events for every tool call. Harmless for display (second overwrites first by ID), but doubles WebSocket bandwidth for tool-heavy sessions.

**Fix**: Remove either Path A or Path B. Path B (EventBus bridge) is cleaner for extensibility. Change `runtime.py` to not yield `tool_call`/`tool_result` events, letting the EventBus handle forwarding. Effort: **1h**.

---

#### 4. Duplicate CapabilityRegistry Instances

**File**: `cozmo/runtime/runtime.py:264-265` (creates per-runtime registry)
**File**: `cozmo/webui_server.py:302` (creates shared registry)
**File**: `cozmo/orchestrator/orchestrator.py:57` (creates own registry as fallback)

Three registries exist:
1. **Shared** in webui_server (line 302): `CapabilityRegistry()` + registered builtins
2. **Per-runtime** in CozmoRuntime (line 264): `CapabilityRegistry()` + registered builtins — **identical instance**
3. **Per-orchestrator** fallback (line 57): created when no registry injected

The shared registry (webui_server) is injected into Orchestrator but **never** into CozmoRuntime. The runtime uses its own duplicate.

**Impact**: If capabilities differ between registries (e.g., runtime-added tools), behavior diverges between Orchestrator's capability resolution and Runtime's tool list. Unnecessary memory per session.

**Fix**: Pass the shared `CapabilityRegistry` from webui_server to both Orchestrator and CozmoRuntime. Remove the default-constructed instances. Effort: **1h**.

---

### P1 — High Priority (should fix before next release)

#### 5. Background Runs Bypass Orchestrator Entirely

**File**: `cozmo/webui_server.py:133-198` (`_start_background_run`)

`_start_background_run` calls `build_runtime(cfg)` to create a fresh runtime, then calls `rt.run_stream(goal)` with **no execution_plan**. The runtime falls through to its own intent classification (`classify_intent` at line 718) and capability resolution (lines 741-748) — bypassing Orchestrator, JobManager integration (except optional), and any plan-based context.

Additionally, a **new runtime + orchestrator is created on every trigger** (line 155), even though `build_runtime()` creates expensive components (ModelManager, ModelRouter, CapabilityRegistry, skills loader).

**Impact**: Scheduled/background tasks don't get proper Orchestrator routing. Fresh runtime creation per trigger is wasteful (skins loaded, models discovered each time).

**Fix**: Reuse shared runtime from webui_server's `get_backend()`. Pass through Orchestrator for planning. Effort: **2h**.

---

#### 6. CLI/Telegram/TaskQueue Paths Also Bypass Orchestrator

**Files**: `cozmo/cli.py`, `cozmo/task_queue.py`, `cozmo/telegram_bot.py`

All non-WebUI entry points construct `CozmoRuntime` directly and call `run_stream()` or `run()` without an `execution_plan`. Same bypass as #5: runtime handles its own intent classification, capability resolution, and model selection. No JobManager tracking.

**Impact**: Core architecture (Orchestrator → Plan → Runtime) only works for WebSocket WebUI. CLI is a second-class citizen on a completely different path.

**Fix**: Create a shared `_build_backend()` function in webui_server or a new `cozmo/app.py` that returns `(orchestrator, runtime, job_manager)`. All entry points use this instead of constructing components manually. Effort: **3-4h**.

---

#### 7. Duplicate LessonStore Implementations

**File**: `cozmo/runtime/lessons.py` (live, used by runtime.py)
**File**: `cozmo/runtime/reflection.py` (dead, only referenced by dead llm_planner.py)

Two incompatible implementations of the same concept:
- `lessons.py`: `Lesson` (tool, pattern, insight, success), `LessonStore` (record, get_context, persist to `~/.cozmo/lessons/lessons.json`)
- `reflection.py`: `Lesson` (id, error_type, tool, pattern, strategy), `LessonStore` (add, find_matches, format_for_prompt, persist to `~/.cozmo/lessons.json`)

**Impact**: Confusion about which LessonStore is canonical. `reflection.py` will rot as dead code.

**Fix**: Delete `reflection.py`. Move any unique logic (e.g., `classify_error()`) into `lessons.py`. Effort: **1h**.

---

#### 8. Dead Planner Package (3 files, 300+ lines)

**Files**: `cozmo/planner/__init__.py`, `cozmo/planner/plan.py`, `cozmo/planner/llm_planner.py`

Zero imports from any file in the codebase. `LLMPlanner`, `Plan`, `PlanStep` — all dead. The `__init__.py` is a docstring-only file.

**Impact**: 300+ lines of dead code. Wasteful to maintain and confuses new developers.

**Fix**: Delete `planner/` package. If plan generation is needed later, build it fresh in `orchestrator/`. Effort: **0.5h**.

---

### P2 — Medium Priority

#### 9. Dead SessionState / SessionStore (99 lines)

**File**: `cozmo/runtime/session.py`

`SessionState` dataclass + `SessionStore` persistence — full implementation of per-conversation state tracking with JSON persistence. **Zero imports from any file in the codebase.**

The actual session tracking happens in `cozmo/webui_server.py:Session` class (~150 lines) which is completely separate.

**Impact**: 99 lines of dead code. The dataclass and JSON save/load logic is unused — if needed, it would need to be rewritten anyway since it doesn't match webui_server's Session pattern.

**Fix**: Delete `session.py`. If session state persistence is desired, port the concepts to `webui_server.py:Session`. Effort: **0.5h**.

---

#### 10. Dead WorkspaceContext (105 lines)

**File**: `cozmo/runtime/workspace.py`

`WorkspaceContext` — project directory tracking, git probing, file listing. Well-documented. **Zero imports.**

The `to_prompt_context()` method that formats workspace state as a system prompt block is exactly what the existing `_project_context` mechanism does in `webui_server.py:Session`.

**Impact**: Duplicated concept of "current project state." WorkspaceContext is clearly designed to replace the ad-hoc `_project_context` string, but was never wired.

**Fix**: Either delete and port useful methods to runtime.py, or wire WorkspaceContext into CozmoRuntime and deprecate `_project_context`. Effort: **0.5h** delete, **2h** wire.

---

#### 11. Circular Import Anti-Pattern in Orchestrator

**Files**: 
- `cozmo/orchestrator/complexity.py:15`
- `cozmo/orchestrator/continuation.py:16`
- `cozmo/orchestrator/intent.py:19`
- `cozmo/orchestrator/orchestrator.py:17-21`

All four files use `from ..orchestrator.task_types` instead of `from .task_types`. Python resolves this correctly (the package is the same), but it's fragile — future Python versions may reject `..orchestrator` when resolved from within the `orchestrator` package itself.

**Fix**: Change `..orchestrator.` to `.` in all four files. Effort: **0.5h**.

---

#### 12. WebUI/Backend Split-Brain for Projects

**File**: `cozmo/webui_server.py` — projects CRUD via both HTTP (lines 928-978) and WS (lines 1302-1574)
**File**: `cozmo/webui/src/hooks/useCozmoChat.ts` — `projects` state updated from both paths without reconciliation

Projects have two parallel APIs:
- HTTP: `GET/POST/PUT/DELETE /api/projects` — full CRUD
- WS: `list_projects`, `create_project`, `select_project` → events `projects_list`, `project_created`, `project_selected`

Frontend calls both simultaneously on mount (`fetchProjects()` HTTP at line 48, WS `list_projects` at line 50). The `projects` state array is overwritten from two sources.

Same issue for conversations: `fetchConversations()` HTTP + `recent_conversations` WS populate separate state variables.

**Impact**: Race conditions on project/conversation state. Two code paths to maintain for the same data.

**Fix**: Pick HTTP for CRUD (standard request/response), remove WS project endpoints (or vice versa). Effort: **2-3h**.

---

#### 13. `runtime/__init__.py` Ornamental Re-Exports

**File**: `cozmo/runtime/__init__.py` — re-exports 13+ symbols

Zero consumers use `from cozmo.runtime import X`. All callers import directly from submodules (`from .runtime.engine import Engine`).

**Impact**: Maintenance burden — must keep re-exports in sync with actual class/function names. If a class is renamed in the submodule but the re-export isn't updated, the package-level import breaks silently.

**Fix**: Either remove all re-exports (preferred) or audit consumers and switch them to `from cozmo.runtime import X`. Effort: **0.5h**.

---

#### 14. Unused WebSocket Events on Frontend

**File**: `cozmo/webui/src/services/cozmo.ts:13-19` — events defined but never handled
**File**: `cozmo/webui/src/hooks/useCozmoChat.ts:33-34, 192-323` — branches for events that never fire

**Never sent by backend** (3 types):
- `agent_status` — no runtime yields this
- `progress` — no runtime yields this  
- `agent_state` — no runtime yields this

**Defined but no `case` in handleEvent** (5 types):
- `agent_config`, `agent_memory`, `agent_tasks`, `background_run_log`, `background_run_logs`

8 dead event types in the `ServerEvent` union. 3 dead state variables (`agentState`, `progress`, `agentTask`).

**Impact**: Code clutter, confusing for new developers. ~60 lines of dead switch branches.

**Fix**: Remove the dead event types and their state variables. Effort: **0.5h**.

---

### P3 — Nice-to-Have

#### 15. Empty/Stub Files

| File | Size | Action |
|------|------|--------|
| `cozmo/tools/weather.py` | 0 bytes | Delete or implement |
| `cozmo/memory/__init__.py` | 0 bytes | Delete or add exports |
| `cozmo/tools/diagnostics.py` | 74 lines, self-described as "stub" | Delete or implement |

Effort: **0.25h**.

#### 16. Duplicate SearXNG Search Logic

**Files**: `cozmo/tools/web_search.py:13` and `cozmo/tools/search_pipeline.py:106`

Both implement `_search_searxng()` with identical HTTP call patterns but different return types. The `search_pipeline.py` version is richer but duplicates connection logic.

**Fix**: Extract shared SearXNG client into a utility. Effort: **1h**.

#### 17. Deprecated Code Kept

| File | Line | Detail |
|------|------|--------|
| `cozmo/tools/search_pipeline.py` | 329-331 | `synthesize_answer()` self-documented as DEPRECATED |
| `cozmo/runtime/runtime.py` | 714-715 | `force_mode` compat path logged as deprecated |
| `cozmo/tools/code_ops.py` | 27 | Regex search comment: "Python fallback" |

**Fix**: Remove deprecated code after verifying no callers remain. Effort: **0.5h**.

#### 18. `test_phase2-6.py` at Project Root

Standalone integration test (71 lines) that hits `http://127.0.0.1:8080/api/*` endpoints via urllib. Not integrated with pytest. Dead test.

**Fix**: Delete or move to `tests/` and convert to pytest. Effort: **0.5h**.

---

## Architecture Diagram (Current vs. Intended)

### Current (WebSocket path only):
```
User Input → Session.start_run()
  → Orchestrator.plan()          ✓ intent, complexity, capabilities, router
  → JobManager.submit()          ✓
  → CozmoRuntime.run_stream()
      → [!] inline ReAct loop    ✗ bypasses Engine
      → MemoryManager.query()    ✓
      → LessonStore.get_context() ✓
      → [!] _emit_bus + yield    ✗ double emission
      → LessonStore.record()     ✓
      → MemoryManager.remember() ✓
  → JobManager.complete()        ✓
```

### All other paths (CLI, Telegram, Background, TaskQueue):
```
[entry point] → CozmoRuntime.run_stream()  ← no execution_plan
  → classify_intent()                     ✗ self-resolve
  → _capability_registry.resolve()        ✗ self-resolve
  → _model_router.resolve()               ✗ self-resolve
  → inline ReAct loop                     ✗ bypasses Engine
```

### Intended:
```
User Input → Session/CLI/Background
  → Orchestrator.plan()
      → IntentDetector
      → ComplexityEstimator
      → PolicyEngine.decide()          ← currently dead
      → CapabilityRegistry.resolve()
      → ModelRouter.resolve()
  → JobManager.submit/start
  → CozmoRuntime.run_stream(execution_plan=plan)
      → Engine.run_stream()            ← currently bypassed
          → model_fn → execute_tool → checkpoint
      → MemoryManager.query/remember
      → LessonStore.record/get_context
  → JobManager.complete
```

---

## Summary Statistics

| Metric | Value |
|--------|-------|
| Python files scanned | 71 |
| Dead files (zero imports) | 6 (`workspace.py`, `session.py`, `planner/*.py` + `weather.py`) |
| Dead function/class implementations | ~4 (`Engine.run_stream` unwired, `PolicyEngine` unwired, `synthesize_answer` deprecated, `classify_error` in reflection) |
| Duplicate implementations | 3 (`LessonStore` ×2, SearXNG search ×2, CapabilityRegistry ×3) |
| WebSocket events double-emitted | 2 (`tool_call`, `tool_result`) |
| WebSocket events defined but unused | 8 |
| Circular import anti-patterns | 4 files in orchestrator/ |
| Empty files | 2 (`weather.py`, `memory/__init__.py`) |
| Ornamental re-exports | 13+ in `runtime/__init__.py` |
| Dead test at root | 1 (`test_phase2-6.py`) |

---

## Recommended Implementation Order

1. **P0 items first** (#1-#4): Engine wiring, PolicyEngine removal, double emit fix, CapabilityRegistry dedup → **~7-8h**
2. **P1 consolidation** (#5-#8): Centralize runtime construction, dedup LessonStore, delete planner → **~6-7h**
3. **P1 cleanup** (#9-#14): Delete dead files, fix circular imports, prune WebSocket events → **~3-4h**
4. **P2 polish** (#15-#18): Stub cleanup, SearXNG dedup, deprecated removal → **~2h**

**Total estimated effort**: ~18-21h to fully stabilize v2 architecture.
