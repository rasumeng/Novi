# Sidebar + Context Manager — Beta Milestone Plan (Updated 2026-08-28)

> **For agentic workers:** Use superpowers:subagent-driven-development task-by-task.
> **Constraints:** Preserve Light Convo 6012a79, beta polish, READ workspace, no NAV_ORDER redesign, no second memory/orchestration, no vector indexing unless required, no shell WRITE/EXECUTE.

**Goal:** Novi accomplishes longer tasks via compact execution state + on-demand retrieval, not bigger windows; sidebar communicates Pinned→Projects(workspace containers)→Chats.

**Architecture:** Orchestrator → ExecutionCoordinator → ExecutionContext → ContextManager (single gatekeeper) → Retrieval/Compression → NoviRuntime → Model. ContextManager is agent-wide primitive, not workspace-specific.

**Tech:** NoviRuntime, RetrievalExecutor/UnifiedRetriever, ModelService/ModelRecord.context_length, WorkspaceService READ, novi/runtime/context_manager + budget.

---

## 1. Sidebar UX — Hierarchy exactly as approved

```
Pinned
Projects
▸ Project A
  Chat 1
  Chat 2
  [+] New chat (hover/focus on project header)
▸ Project B
Chats
  Chat 3
  Chat 4
```

* Projects expandable/collapsible, persisted `localStorage novi_sidebar_expanded_projects`
* `Projects` label + Projects expandable containers live **inside** `activeSection==='conversations'` block — `NAV_ORDER` unchanged (5 items), `Jobs/Timeline` untouched (OpenCode skill later)
* Single ownership: `Project.conversationIds` + `Conversation.projectId` dual write, `Sidebar` derives `projectId→Conversation[]` via both fields, `unassigned = !pinned && !inAnyProject`
* `+ New chat` small `Plus` affordance on project header hover/focus, creates conversation already associated (`activeProjectId` + auto-link, see 2026-08-27 fix)
* 264↔64 preserved (`motion.aside`), collapsed hides hierarchy, keyboard `aria-expanded`, touch `md:opacity-0 → opacity-100`
* Existing `ProjectsPanel` detail view kept (not removed), but sidebar becomes primary discoverability

**Files:** `Sidebar.tsx:110-144`, `SidebarItem.tsx` indent, `App.tsx:141` already passes `projects`, `workspaceModes.ts` **not touched**, `useNoviChat` already handles `activeProjectId` localStorage + backfill.

## 2. ContextManager — Single Gatekeeper, Agent-Wide

**Separation (authoritative):**
* Memory = durable facts/preferences/goals (LanceDB Brain)
* Project = user-authored sharedContext (first-class `ProjectContextRetrievalSource`, isolated by `project_id`, budget 2000 chars, not Brain)
* Workspace = filesystem state (sqlite `WorkspaceIndex`, READ)
* ExecutionState = compact task state (goal, current objective, plan, completed, discoveries, important files, workspace paths, decisions, errors, unresolved, next_action, memory refs)
* Conversation history = recent messages
* Retrieval = reconstruct on demand via `UnifiedRetriever` + `ResultMerger`
* **ContextManager = decides what reaches model** — consumes all sources above, single layer before `NoviRuntime._system_prompt` + `base_msgs` assembly.

**Flow:**

```
Orchestrator.analyze → TaskAnalysis
  → ExecutionCoordinator.prepare → Task/Job
    → ExecutionContext {goal, plan, history, project_id, workspace_root}
      → ContextManager(budget, executionState, retrievalPlan)
        → token estimate (model-specific) → available_budget
        → if exceeds 85% → compact (L1/L2) → preserve ExecutionState
        → retrieve minimum necessary (Memory/Project/Workspace/Knowledge/Web via existing RetrievalExecutor)
        → assemble prompt (system + stable state + retrieved + recent + tool results + reserve)
          → NoviRuntime → Model
```

**No second memory:** ContextManager coordinates existing `MemoryManager, ProjectContextRetrievalSource, WorkspaceRetrievalSource (FILE), KnowledgeRetrievalSource, WebRetrievalSource` via `UnifiedRetriever`.

## 3. Context Budgeting — Model-Aware, Instrumented

```
available = context_window
  - system_prompt (~800)
  - stable_execution_state (~1200)
  - recent_conversation (history[-6] estimated)
  - retrieved_context (memory+project+workspace via allocation)
  - tool_output (truncated via L1)
  - output_reserve (1024)
  - safety_margin (512)
= budget for next step
```

* `context_window` from `ModelRecord.context_length` via `ModelService.registry` + `runtime_inventory._context_length_from_show`; if `None` → conservative `4096` (small model) or `8192` default, never fabricate, fail safe.
* `ContextBudgetManager` class `novi/runtime/context_budget.py` — `estimate_tokens(text) = len*4` (existing dead code now wired) + `model_window` → `available`, `utilization%`, `thresholds 75% warning / 85% compact / 90% emergency`.
* Instrumentation: `ExecutionContext.metadata.budget_breakdown = {system, stable, recent, retrieved, tool, reserve, safety, available, utilization}` logged to `trace.debug_events` and `budget_breakdown` test helper.
* Tests: `test_context_budget.py` — budget calc for small (4096) vs large (8192), consumption breakdown sum, fallback when unknown.

## 4. Compression Layered — Preserve Hierarchy

**L1 Tool-result compression:** Large tool result (> `max_tool_output 8000`) → keep `paths, snippets, errors, counts, summary`. Implemented in `novi/runtime/tool_result_compressor.py` reused by `ContextCompressor` — do not blind summarize filenames/errors. Extensible: workspace hits keep `score` + `path`.

**L2 Rolling conversation compaction:** When `utilization >=85%`, produce summary preserving at minimum: goal, current objective, decisions, discoveries, important files, completed work, unresolved issues, current plan, next_action. Uses `simple_llm` `SUMMARIZE_PROMPT` if available else extractive fallback (deterministic, no LLM). Stores as `ExecutionContext.summary` + `ExecutionState` fields, `history` truncated to last 6 turns.

**L3 StableState/checkpoint:** `novi/runtime/execution_state.py` `StableState` dataclass (goal, current_objective, plan, completed, current_step, discoveries, important_files, workspace_paths, decisions, errors, unresolved, next_action, memory_refs, budget_breakdown). Persisted in `jobs/job.py:42 Checkpoint.stable: dict` alongside `step, tool_states, messages` (bounded 500/1000). Checkpoint is task state, not message dump.

## 5. Continue — No Babysitting

* `max_steps` (10) or `context 90%` → `Job.status = NEEDS_CONTINUATION` (add to `jobs/job.py` enum), `Checkpoint` saved with `StableState`, `ExecutionTrace.stop_reason = "max_steps"|"context_overflow"`.
* `ContextManager.compact` before marking.
* If policy `continuation_count <3` and task not terminal → **auto-resume internally**: `ExecutionCoordinator` re-queues `resume_from = checkpoint.step` without user input, `Runtime` restores `StableState` via `ExecutionContext`, retrieval re-fetches via `memory_refs`.
* Otherwise surface resumable state: UI shows `Novi paused — 3 steps completed, next: investigate runtime/model routing [Continue]` (existing `ActivityPanel` + `inlineSteps` + `ThinkingTrace`).
* User explicit "continue" → `IntentDetector CONTINUATION` → `ContinuationService.recommended(conversation_id)` returns `ResumeTarget(task_id,job_id,checkpoint,next_step)` even if text is just "continue" — not a fresh plan. `ExecutionCoordinator._resolve_continuation` handles keyword **or** `NEEDS_CONTINUATION` job for that conversation. No fake replay of "continue" word.
* Loop prevention: `continuation_count` in `Job.metadata` max 3 per task → abort to `error`.

## 6. Workspace Boundary — READ Only

`WorkspaceService` already `READ` only (`capability.py enabled_for_beta`), `WorkspaceIndex` `path traversal block`, exclusions `.git/node_modules/.venv...`, `search` isolated by `project_id` only `~/.novi/workspaces/{id}/index.sqlite`, `read` checks `target.relative_to(root)`. Project A never retrieves B — negative tests `test_workspace_isolation.py` + `test_project_context_isolation.py` after compaction/resume already PASS for index, now also after ContextManager preserves `project_id` in `StableState`.

## 7. Small Model Optimization

Test matrix: small context 4096 + 200-file repo + large tool output (200 files) + 3 compaction cycles + interrupted run + max-steps exhaustion. Strategy: concise execution prompts, structured `StableState`, aggressive L1 truncation, relevance-ranked retrieval (`UnifiedRetriever` already), workspace chunk budget `3/6000`, rolling summaries, early compaction at 85%, model-specific budgets, dedup `workspace_context` not duplicated in `prompt` (existing `runtime.py:354` guard).

## 8. Preserve Architecture — Reuse, Not Replace

* Reuse `Orchestrator → ExecutionCoordinator → ExecutionContext → RetrievalExecutor → UnifiedRetriever → NoviRuntime → ModelService` seam.
* `ContextManager` fits between `ExecutionContext` and `RetrievalExecutor`/`NoviRuntime._system_prompt` — does not replace `RetrievalExecutor` or `MemoryManager`.
* No second memory, no second retrieval framework, no second loop, no new nav, no vector indexing unless `WorkspaceRetrievalSource` needs it (interface already extensible).
* Frontend polish frozen except `Sidebar.tsx` hierarchy.

## 9. Canonical Acceptance Test

Project "Novi Development" workspace `~/Projects/novi` (or `D:\Projects\Novi`), user: "Find where model routing is implemented."

Chain: `Orchestrator/analyze` → `workload research/code` → `ModelSelector.resolve(workload)` → `ModelService.resolve` → `runtime bind` should be found via workspace evidence, not hallucinated. Novi must: search efficiently (path+content → 3 chunks), read only necessary (budget 6000), cite `orchestrator.py:266 plan, runtime.py:523, models/service.py:60, services/context.py:418` etc., preserve discoveries in `StableState`, survive compaction/max_steps, resume without restatement, never load whole repo, never leak other project.

Existing `MessageContent.test.tsx` 378 already covers latex/math; new `test_workspace_routing_e2e.py` will seed workspace with known files and assert retrieval + budget + citation.

## 10. Verification

* `npm run build` + `vitest 7/171` + `pytest` (existing 60+37)
* New: `test_sidebar_hierarchy.py`, `test_project_isolation.py` (already), `test_context_budget.py` (instrumentation), `test_compaction_l1_l2_l3.py`, `test_tool_output_compression.py`, `test_checkpoint_serialization.py`, `test_max_step_continuation.py`, `test_context_overflow_continuation.py`, `test_interrupted_resume.py`, `test_no_duplicate_execution.py`, `test_small_context_model.py`, `test_workspace_200file_budget.py`, `test_continue_resume_vs_fresh.py`
* Manual UI: Attach `D:\Projects\Novi`, ask routing question, observe `Searching workspace… → Reading 3 files… → Files used:` + `Continue` resume after `max_steps`.

## Implementation Order (updated)

1. **ContextBudgetManager + ContextManager skeleton** — model-aware budget, instrumentation, single gatekeeper wired before `NoviRuntime._system_prompt`.
2. **L1 tool-result compression** — integrate `ContextCompressor` into `ToolExecutor` path.
3. **Sidebar hierarchy** — `Sidebar.tsx` expandable Projects (Pinned > Projects > Chats), persisted, `+ New chat`.
4. **L2/L3 compaction + StableState checkpoint** — rolling summary at 85%, `Checkpoint.stable`.
5. **Continuation auto-resume** — `NEEDS_CONTINUATION`, `max_steps/context_overflow` → checkpoint → compact → auto-resume (3) or resumable UI, `continue` keyword resumes checkpoint.
6. **Integration + 13 verification suites + manual workspace investigation.**

Scope: P0 required above; P1 vector workspace, file watcher; P2 autonomous WRITE/EXECUTE.
