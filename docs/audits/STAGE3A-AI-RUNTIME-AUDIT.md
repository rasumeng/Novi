# Stage 3A — LangChain / LangGraph Runtime Integration Audit

Phase 7 Stage 3A. Read-only audit. No production changes made by this stage.

- Date: 2026-08-20
- Scope: `cozmo/` Python runtime, integration seams for LangChain / LangGraph.
- Dependencies (installed, `pyproject.toml`):
  - `langgraph 1.2.6`
  - `langchain-core 1.4.9`
  - `langchain-ollama 1.1.0`
  - `langchain-openai 1.3.5`
  - `lancedb 0.34.0`
- Baseline: Stage 2 verified — Python 1425 passed, 31 architecture guards, Vitest 125 passed, tsc clean, config CLI smoke-tested. Working tree carries uncommitted Stage 2 changes. Do not undo them.

---

## 1. Current Architecture

### 1.1 Single execution seam

All six entry surfaces (WebUI WebSocket, background scheduler, CLI, Telegram, scheduler, task queue) converge on one coordinator:

```
entry surface
  → ExecutionCoordinator.run_stream            (cozmo/services/execution.py:62)
    → Orchestrator.plan                        (cozmo/orchestrator/orchestrator.py)
      → ExecutionPlan
      → CozmoRuntime.run_stream(ctx)           (cozmo/runtime/runtime.py:430)
        → [analysis]  IntentDetector / ComplexityEstimator / EvidenceDetector
        → [plan]      PlannerEngine (deterministic templates, no LLM)
        → [retrieve]  RetrievalExecutor.execute (pre-loop, research intent)
        → [resolve]   ModelSelector.resolve → ModelService → ModelRuntime → providers
        → [bind]      mm.bind_model(name, lc_tools, temperature)  (:605/:901/:982)
        → [loop]      _run_agent_loop  ReAct                       (:822)
        → [memory]    Brain.observe (or flat history + SimpleLLM compact)
```

`CozmoRuntime` is a **deterministic per-step executor** (runtime.py:639-642, 822-842). It consumes an ordered `ExecutionPlan.plan.steps`, runs each step through a bounded ReAct loop, and emits plan/step lifecycle events. No planning logic lives in the runtime. Resume is index-addressed: `ctx.resume_from == Checkpoint.step`, passed through unchanged.

### 1.2 Model resolution & construction (ownership)

- `ModelSelector.resolve(workload)` (runtime/model_selector.py) returns the verbatim configured `llm.workloads.<workload>.model` or raises `ModelUnavailableError`. `model_capabilities()` is descriptive only.
- `_CAPABILITY_TO_WORKLOAD`: coding→code, planning→research, research→research, conversation→general (runtime.py:44-47).
- `ModelService.client_for_model` / `bind_model` (cozmo/models/service.py) delegate construction to the new `ModelRuntime` boundary.
- **Only one LangChain model-construction site in the repo**: `cozmo/providers/base.py` (`ChatOllama` :95/:101/:108, `ChatOpenAI` :139/:150). Verified by repo-wide grep: no other `ChatOllama`/`ChatOpenAI`/`langchain_ollama`/`langchain_openai` import exists.
- LangGraph: **zero usage**. Not imported anywhere. Pure placeholder dependency (confirmed by `cozmo-memory-rag-langgraph-audit.md` and grep).

### 1.3 LLM call surfaces

| Caller | File | Model | Notes |
|---|---|---|---|
| ReAct loop | runtime.py:858 | runnable.stream | bound via ModelService.bind_model |
| Intent/grounding | orchestrator/intent.py, evidence.py | SimpleLLM.invoke | heuristic pre-pass + LLM fallback |
| Knowledge summarize | memory/manager.py | SimpleLLM | SUMMARIZE_PROMPT |
| History compaction | runtime.py:1047 | SimpleLLM.invoke | _COMPACT_PROMPT |
| Desktop vision | tools/desktop.py | direct Ollama HTTP (requests) | verbatim `llm.workloads.general.model` + `supports_vision` gate |

`SimpleLLM` (services/simple_llm.py) re-resolves the workload model on every call and propagates `ModelUnavailableError` verbatim.

### 1.4 Tools

- `cozmo/tools/__init__.py`: global `TOOL_REGISTRY` dict + `@register_tool()` decorator.
- `ToolRegistry` (runtime/tool_registry.py): `register/unregister/get/list`, plus `as_lc_tools()` wrapping via `langchain_core.tools.StructuredTool.from_function` (:31-40).
- `ToolExecutor` (runtime/tool_executor.py): permission gating → risk (`get_tool_risk` in tool_risk.py) → validation → execution → sanitization → normalization → fallback → record. Uses `langchain_core.messages.AIMessage`.
- Capabilities (capabilities/builtin.py) bind tool sets + planner strategy + risk + template patterns:
  - research → `[web_search, web_search_pipeline, web_fetch, calculator, search_knowledge]` + optional `[fetch_url, read_knowledge]`, strategy `research`.
  - coding → `[read_file, write_file, edit_file, glob, grep, bash, run_command, list_directory]` + optional `[diagnostics, execute_python, git_diff, git_log]`, strategy `coding`.

### 1.5 Research pipeline

```
RetrievalPolicy.resolve (pure) ──delegates──> SourceSelector.select
   → RetrievalPlan (sources, strategy, ContextAllocation: max_sources=3, max_results=8, max_context_chars=6000)
   → RetrievalCoordinator (budget: max_web_searches=1, max_web_fetches=1; blocks duplicate search/fetch; dedup)
   → RetrievalExecutor.execute (pre-loop; sources Memory/Knowledge/Project/Web via RetrievalSource protocol)
   → EvidenceCollector (search→rank→fetch→merge→EvidenceBundle; RetrievalQuality SUFFICIENT/WEAK/EMPTY/FAILED)
   → grounding_text injected into SystemMessage; "[Retrieval guidance]" budget hint (:617-623)
```

Search backend is SearXNG-only (`tools/search_pipeline.py` `_search_multi`/`fetch_pages`/`clean_content`/`rerank_results`; `tools/web_search.py`). Recovery/escalation logic (knowledge-empty → web) is **scattered across the ReAct loop** in three hooks:

- `recommend_pre_loop` (runtime.py:575) — upgrade to search tools before the loop.
- `recommend_when_model_answered` (:894) — model answered without tools → grant search, **rebind runnable** (:901).
- `recommend_after_tool` (:975) — empty KB result → escalate to web, **rebind runnable** (:982).

Each rebind reconstructs the LangChain runnable inline. Synthesis is inline in the ReAct loop (no dedicated synthesize node). There is **no validate/gap-analysis node**.

### 1.6 Coding pipeline

- Planning: `PlannerEngine` (planner/planner.py) deterministic 3-step template (understand → implement → verify).
- Execution: ReAct loop with coding tools. `execute_python` (tools/code_ops.py) supports Docker-sandbox-with-subprocess-fallback. `diagnostics` tool is a syntax/TODO stub — no real LSP/test runner. ProjectIndex (code_indexer.py) = LanceDB semantic code index.
- **No test-execution tool, no edit→test→fix retry loop** — verify step today is an LLM self-report, not a real run.

### 1.7 Memory / persistence ownership (locked contracts)

- Brain (brain/brain.py) owns conversation canonical identity; storage internals confined to `cozmo/brain/` + `cozmo/services/context.py` (Guard `test_runtime_does_not_touch_storage_internals`).
- Flat `MemoryManager` (memory/manager.py, LanceDB) = legacy read path; `MemoryRetrievalSource` routes through `Brain.recall` when a Brain is wired (sources/memory.py:98-107).
- Runtime in-memory history + `SimpleLLM` compaction (runtime.py:1045-1056). LangGraph must not take over persistence; no LangGraph checkpointer wiring.
- Jobs/Checkpoints: `services/job_lifecycle.py`, `services/continuation.py` (resume candidates), `Checkpoint.step` contract. Runtime never reads checkpoints — callers resolve and pass `resume_from`.

---

## 2. LangChain Opportunities

Current LangChain usage is minimal and confined: messages (`HumanMessage`/`SystemMessage`/`AIMessage`/`ToolMessage`), `StructuredTool`, and the two providers. That boundary is the correct one — the architecture guards already enforce it. No whole-ecosystem expansion needed.

| # | Opportunity | Current impl | Proposed | Benefit | Complexity | Risk | Rec |
|---|---|---|---|---|---|---|---|
| L1 | Single model-construction seam | `providers/base.py` + `runtime/models/factory.py` coexist | Keep both; `ModelRuntime.create_chat_model` is the only path; providers stay thin | One gate for guards 1/3/6/7 | S | L | **Do in 3B** |
| L2 | LangChain runnable rebinding | 3 inline rebinds in loop (:901/:982) | Move rebind behind a single `runtime.bind()` helper so recovery no longer reconstructs inline | Readability, testable | S | L | **Do in 3C** |
| L3 | `Runnable` abstraction for plan-step executor | `client_for_model` vs `bind_model` branches | Accept any `Runnable`/`BaseChatModel` in `_run_agent_loop` | Decouples loop from ModelService | S | L | **Do in 3C** |
| L4 | `SimpleLLM` stays custom | Thin `invoke()` wrapper | Keep. LangChain adds nothing for single-shot aux calls; re-resolution per call is a feature | — | — | — | **Keep, no change** |
| L5 | Vector store via LangChain | LanceDB direct in `LanceStore` | No. Direct LanceDB is fine and dependency-light | — | — | — | **No change** |
| L6 | Callbacks / tracing | `ExecutionTrace` + StepTrace hand-rolled | Optional later `langchain_core.callbacks` for model latency | Metrics parity with existing trace | M | M | **Defer, not required** |

Conclusion: LangChain already lands where it should. Stage 3 work should *use* LangChain types (messages, `StructuredTool`, provider chat models) exactly as today — the opportunity is LangGraph composition, not more LangChain surface.

---

## 3. LangGraph Opportunities

LangGraph is a declared, installed, but unused dependency. The hand-rolled loops (research search loop + coding ReAct) are the natural replacement targets.

### 3.1 Research graph (Stage 3C)

Today's scattered stages map cleanly to a `StateGraph`:

| LangGraph node | Existing building block | Evidence |
|---|---|---|
| understand | `IntentDetector` + `EvidenceDetector` + `ComplexityEstimator` | orchestrator/ |
| plan | `PlannerEngine` research template | planner/planner.py:38-42 |
| search | `EvidenceCollector` + `tools/search_pipeline.py` | runtime/evidence.py |
| evaluate/gaps | `RetrievalQuality` + `RetrievalCoordinator` (budget/dedup) | runtime/evidence.py, retrieval_coordinator.py |
| synthesize | LLM call over `grounding_text` + evidence bundle | inline today → move here |
| validate | **missing** → new node (citation/gap check, `RetrievalQuality` gate) | — |

**Graph state**: `user_input`, `analysis`, `retrieval_plan`, `evidence: EvidenceBundle|MergedRetrievalResult`, `grounding_text`, `gaps`, `messages`, `plan_step_index`, `answer`, `quality`.

**Conditional edges** implement the three recovery hooks (pre-loop / answered-without-tools / post-tool escalation) that are currently inline and rebind the runnable (runtime.py:575, 894, 975). Budget enforcement stays in `RetrievalCoordinator` — the graph reads budget, never owns it.

**Boundary contract (from `test_graph_modules_never_select_models`, Guard 5):** `cozmo/graphs/` must NOT import `ModelService`, `ModelSelector`, `ModelRecommendationEngine`, `recommend`, `apply_selection`, `create_provider`, `configuration.resolver`, or `llm.workloads`. → The graph **receives an already-constructed chat model / `Runnable` injected by the runtime seam**. Resolution stays Cozmo-owned. The guard is currently dormant (no `graphs/` dir); 3C activates it.

**CozmoRuntime stays the plan-step executor.** 3C swaps the *ReAct loop body* for the research intent into the graph execution; plan/step lifecycle events, `resume_from`, event bus, and job checkpoints remain unchanged. LangGraph must not write configuration (Phase 6 contract).

### 3.2 Coding agent (Stage 3D)

| LangGraph node | Existing building block | Evidence |
|---|---|---|
| read/understand | `read_file`, `grep`, `glob`, `ProjectIndex` | tools/file_ops.py, code_ops.py, code_indexer.py |
| implement | `edit_file`, `write_file` | tools/code_ops.py |
| verify | **missing** → new node wrapping `execute_python` / `run_command` / `diagnostics` | code_ops.py:43; diagnostics.py (stub) |
| retry | **missing** → conditional edge back to implement with error feedback | — |
| finalize / commit | **missing** (optional `git_diff`/`git_log`) | — |

The gap is the **verify → retry loop**: today "verify" is a plan step the model self-completes; nothing runs tests or feeds failures back. This is where LangGraph adds real value. Risk gating stays in `ToolExecutor`/`tool_risk.py` — the graph calls the same tool boundary.

### 3.3 Checkpointing decision

**Do not use LangGraph's checkpointer** for plan/job state. Cozmo's existing `Checkpoint`/`Job`/`ContinuationService` owns resume semantics and the contract (`Checkpoint.step == resume_from`, never +1). LangGraph checkpointer would create a second persistence authority and violate the memory/persistence ownership guards. LangGraph state stays in-memory per run, fed from and drained to the existing structures.

### 3.4 What NOT to graph

- `conversation` / `planning` intents: single-pass, low risk — keep unplanned ReAct path (runtime.py:755-767).
- Model selection, recommendation, persistence, frontend: strictly out of scope (immutable contracts).

---

## 4. Shim Inventory

Repo-wide sweep for one-line delegators, duplicate provider construction, and legacy refs.

| Shim | Location | Verdict | Action |
|---|---|---|---|
| `ModelRuntime.create_chat_model` | cozmo/runtime/models/factory.py (untracked) | Real boundary, not a shim | **Keep, extend in 3B** |
| `ModelSelector.resolve` | runtime/model_selector.py | Cozmo-owned resolution contract | **Keep** |
| `SimpleLLM` | services/simple_llm.py | Real thin boundary; used by 4 aux call sites | **Keep** |
| `MCPManager` thin facade | runtime/providers/mcp.py | Delegates to `runtime/mcp/{lifecycle,discovery,status,runtime_client}` | **DEFER to 3E** — public test surface must stay |
| `SearchConfig` / `_get_config` | tools/search_pipeline.py | Config wrapper | **Keep** |
| `knowledge_dir()` helper | memory/ | Path helper | **Keep** |
| `ollama.py` | cozmo/ollama.py | Process management (start/stop/is_running/wait), not a client | **Keep** |
| Provider `parse_model_spec` / `create_provider` | providers/base.py, providers/__init__.py | Real adapter, only construction site | **Keep** |
| Duplicate model construction | none found (grep: ChatOllama/ChatOpenAI only in providers/base.py) | — | **No change** |
| Direct HTTP callers (not shims, external services) | searxng_util.py, tools/web_search.py, tools/search_pipeline.py, tools/diagnostics.py (Sourcegraph), tools/desktop.py (vision), providers/base.py (Ollama /api/tags) | First-party service calls | **Keep** |
| Raw `/api/chat` caller | default_skills/skill-creator/scripts/utils.py:39 | Standalone skill, not runtime path | **Keep (isolated)** |

No legacy `cozmo.config` imports, no `legacy_config`, no `_RouterLLM`, no `models.mode`/`llm.roles`/`force_mode` regressions found — Stage 2 guards (`test_architecture.py` Guards A–K) hold.

---

## 5. Model Runtime Design (Stage 3B)

Target state for the adapter boundary:

```
ModelService.resolve(ctx)                          → ResolvedModel
   (config-owned, verbatim llm.workloads.<w>.model, raises ModelUnavailableError on "")
ModelRuntime.create_chat_model(resolved, temp)     → BaseChatModel
   (rejects empty selection BEFORE LangChain construction; delegates to providers)
cozmo.providers.create_provider(...)               → ChatOllama | ChatOpenAI
   (only construction site in repo)
```

Constraints enforced by existing guards:

- **Guard 1/2**: no hardcoded model IDs, no model-name substring branching anywhere in `cozmo/runtime/` incl. `runtime/models/`.
- **Guard 3**: model-construction imports only in `cozmo/providers/`, `cozmo/runtime/providers/`, `cozmo/runtime/models/`.
- **Guard 6**: no fallback/substitute/backup/auto_select vocabulary in `cozmo/runtime/models/` + `cozmo/models/`.
- **Guard 7**: no "Automatic" vocabulary in `cozmo/runtime/` + `cozmo/models/`.
- **Guard 5 (activates with 3C)**: `cozmo/graphs/` never resolves/recommends/selects a model; receives an injected `Runnable`.

Design decisions for 3B:

1. `ResolvedModel` is the frozen hand-off type; contains `model`, `base_url`, `temperature`, `supports_tools` (from `model_capabilities()`), nothing else that could re-select.
2. `bind_model`/`client_for_model` route exclusively through `ModelRuntime.create_chat_model`. The 3 inline rebinds (runtime.py:901/:982) collapse to `runtime.bind(ctx)` — single seam, testable.
3. `ModelUnavailableError` (models/service.py:19) propagates untouched; SimpleLLM re-resolution semantics unchanged.
4. New tests: `tests/test_model_runtime.py` already untracked; extend for bind-path coverage, guard compliance, and no-construction-on-empty.

---

## 6. CrewAI Decision

**NO — do not add CrewAI.**

- LangGraph 1.2.6 is already declared and installed; adding CrewAI duplicates the orchestration abstraction for zero new capability.
- Cozmo's need is *workflow composition* (research loop, coding verify/retry), which `StateGraph` + conditional edges covers. CrewAI's multi-agent "crew" paradigm solves a different problem (independent specialist agents) that this codebase does not exhibit.
- Adding it means a second agent framework to integrate, a new runtime dependency, and two competing graph/agent metaphors — contrary to the "avoid the whole ecosystem" dependency policy.
- Guard 5's forbidden-import list keeps `cozmo/graphs/` honest; no CrewAI import path should ever appear there.
- Revisit only if a concrete multi-agent need emerges (none today).

---

## 7. Migration Plan

Smallest safe sequence; each stage compiles, passes full regression, and adds tests. Phase 6 model-selection tests stay green throughout.

| Stage | Scope | Deliverable | Gate |
|---|---|---|---|
| **3A** | Audit (this doc) | Contracts + decision record | Approval; **no code yet** |
| **3B** | ModelRuntime adapter | `ModelRuntime.create_chat_model` as sole seam; collapse 3 rebinds to `runtime.bind()`; extend `test_model_runtime.py` | Guards 1/3/6/7 + 1425 suite |
| **3C** | LangGraph research workflow | `cozmo/graphs/research_graph.py` + `state.py`; nodes understand→plan→search→evaluate→synthesize→validate; conditional edges replace recovery hooks; graph receives injected Runnable; activate Guard 5 | Research intent regression + new graph tests; arch guards 31+ |
| **3D** | Coding agent | `cozmo/graphs/coding_graph.py`; add verify node (test/diagnostics) + retry conditional edge; tool/risk gating unchanged | Coding intent regression + new graph tests |
| **3E** | Shim cleanup | Revisit `MCPManager` thin facade; remove dead synthesis hooks; confirm single construction site | Full regression, doc sweep |

Resume/checkpoint contract untouched (LangGraph checkpointer explicitly NOT used — section 3.3). LangGraph never writes configuration.

---

## 8. Files Expected to Change

**New**
- `cozmo/graphs/__init__.py`
- `cozmo/graphs/state.py` — research + coding graph state schemas
- `cozmo/graphs/research_graph.py` (3C)
- `cozmo/graphs/coding_graph.py` (3D)
- `tests/test_research_graph.py` (3C)
- `tests/test_coding_graph.py` (3D)

**Modified**
- `cozmo/runtime/models/factory.py` (untracked; refine to sole seam in 3B)
- `cozmo/models/service.py` — route bind/client through `ModelRuntime` (3B)
- `cozmo/runtime/runtime.py` — collapse 3 rebinds to `runtime.bind()`; swap research-intent loop body for graph (3B/3C); coding-intent loop body for graph (3D)
- `cozmo/runtime/tool_registry.py` / `tool_executor.py` — unchanged or minor (graph calls same boundary)
- `cozmo/runtime/retrieval.py`, `retrieval_coordinator.py`, `evidence.py` — expose node-friendly read APIs if needed (3C)
- `tests/test_architecture.py` — activate Guard 5 (graphs/ now exists), add graph module scans
- `pyproject.toml` — verify langgraph pin; no new deps
- `docs/` — this audit + stage records

**Explicitly unchanged (contracts)**
- `cozmo/configuration/*` — model selection, recommendation, persistence (Phase 6)
- `cozmo/webui/*` — frontend selection logic
- `cozmo/brain/*`, `cozmo/jobs/*`, `cozmo/services/continuation.py` — persistence/resume ownership
- `cozmo/services/simple_llm.py` — aux LLM path (keep as-is)

---

## Appendix — Guard inventory relevant to integration (tests/test_architecture.py)

- Guard 1 `test_no_hardcoded_model_ids_in_runtime`
- Guard 2 `test_runtime_no_model_name_substring_conditionals`
- Guard 3 `test_langchain_model_construction_confined`
- Guard 4 `test_runtime_does_not_import_recommendation`
- Guard 5 `test_graph_modules_never_select_models` (dormant until graphs/ exists)
- Guard 6 `test_no_model_fallback_or_substitution_vocabulary`
- Guard 7 `test_no_automatic_vocabulary_in_runtime_models`
- Guard 8 `test_apply_selection_is_sole_selection_writer`
- `test_provider_boundary`, `test_model_resolution_ownership`
- Stage 2 Guards A–K (legacy config, env shims, TOML writes, PUT /api/config, conversation mode, force_mode, memory fallback, config authority, CLI, knowledge path)