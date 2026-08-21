# Stage 0 — Read-Only Audit: AI Runtime Architecture + LangChain/LangGraph Integration

**Status:** Stage 0 audit complete. No production code modified.
**Scope:** Phase 7 — introduce LangChain runtime boundary + LangGraph orchestration; eliminate shims; preserve Phase 6 contract exactly.
**Baseline:** `pytest tests` → **1394 passed** (5 min). `tests/test_architecture.py` → 13 passed (existing guards green).

---

## 1. Runtime execution map

All six entry surfaces converge on ONE seam — no competing execution paths:

| Entry | Adapter | Seam |
|---|---|---|
| WebUI WS chat (`deep_research` flag) | `webui_server.py` `Session.start_run` :524 | `ExecutionCoordinator.run_stream` |
| WebUI background/scheduler | `services/background.run_background` | same |
| CLI `cozmo run` / `cozmo code` | `cli.py` `CliSessionAdapter` :127 | same |
| Telegram | `services/telegram` :50 | same |
| Headless scheduler / task-queue | `services/context._scheduled_trigger` | same |

Canonical path:

```
user_input
  → ExecutionCoordinator.run_stream (services/execution.py:62)
    → continuation? ContinuationService → JobManager.reopen (no replan)
    → Orchestrator.plan (force_intent/force_capability/force_model)
      → ExecutionPlan (+ Task/Job/Checkpoint)
  → CozmoRuntime.run_stream(plan) (runtime/runtime.py:430+)
    → retrieval pre-loop (RetrievalExecutor)
    → resolve selected model → bind LangChain runnable
    → ReAct loop (_run_agent_loop :828): runnable.stream → extract tool_calls
      → permission gate (tool_executor) → ToolMessage → loop
    → plan steps executed sequentially (PlannerEngine steps)
  → Job COMPLETED + ExecutionHistory
```

Direct LLM invocation sites (all go through `ModelService` → `providers/base.py`):

| file:line | function | calls | workload |
|---|---|---|---|
| `runtime/runtime.py:611-612` | main bind | `ModelService.bind_model` / `client_for_model` → `ChatOllama/ChatOpenAI` | resolved selected model |
| `runtime/runtime.py:907` | recovery re-bind | `bind_model` (same model, +search tools) | resolved selected model |
| `services/simple_llm.py:47` | `SimpleLLM.invoke` | `ms.client(workload)` | `general` (intent/grounding/summary/compact) |
| `orchestrator/intent.py:142` | `classify_intent` | injected `simple_llm` | general |
| `orchestrator/evidence.py:276` | `grounding_reasoner` | injected `simple_llm` | general |
| `runtime/runtime.py:1064` | `_compact` | `simple_llm.invoke` | general |
| `brain/reasoning/extraction.py:315` | `Summarizer` | injected `simple_llm.invoke` | general |
| `memory/manager.py:121` | legacy summarize | `simple_llm.invoke` | general (brain=None fallback only) |
| `evaluation/evidence_ab.py:223` | A/B judge | `client_for_model` | eval only |

Non-LangChain LLM transport (legacy/direct):
- `tools/desktop.py:57` — raw `requests /api/chat` (vision image analysis, `general` model verbatim).
- `default_skills/skill-creator/scripts/utils.py` — raw `/api/chat` (standalone scripts, own CLI args).
- `services/embedding_providers.py:132` — urllib `/api/embeddings` (embeddings; not chat).

---

## 2. Model-instantiation map

| Site | What | Model source | Verdict |
|---|---|---|---|
| `providers/base.py:94-113` | `OllamaProvider.get_chat_model` → `ChatOllama` | `self.model_name` (pre-resolved) | **Keep — canonical** |
| `providers/base.py:138-151` | `OpenAIProvider.get_chat_model` → `ChatOpenAI` | `self.model_name` | **Keep — canonical** |
| `providers/base.py:165-189` | `create_provider` / `parse_model_spec` / `PROVIDER_REGISTRY` | workload spec or dict `{provider, model}` | **Keep** |
| `models/service.py:45-67` | `ModelService.resolve/client/client_for_model/bind_model` | `llm.workloads.<w>.model` verbatim | **Refactor into ModelRuntime boundary** |
| `runtime/model_selector.py:80-94` | `ModelSelector.resolve` | verbatim; **raises ModelUnavailableError when ""** | **Keep — strict resolver** |
| `services/simple_llm.py:33-48` | `SimpleLLM.invoke` | re-resolves `general` every call | Keep, rewire through ModelRuntime |
| `services/context.py:79-119` | composition root builds `ModelService` + `refresh()` + `SimpleLLM` | config | Keep |
| `evaluation/__main__.py:111-117` | eval clients | explicit CLI model args | Keep (dev harness) |

langchain imports: `providers/base.py` (ChatOllama, ChatOpenAI), `runtime/runtime.py` (messages), `runtime/tool_registry.py` (StructuredTool), `runtime/tool_executor.py` (AIMessage), `evaluation/evidence_ab.py` (HumanMessage). All inside the correct boundary except `runtime/runtime.py` (message types only — fine).

---

## 3. Provider map

```
providers/base.py
  LLMProvider (ABC): get_chat_model / list_models / bind_tools / invoke / stream
    ├─ OllamaProvider  → ChatOllama (base_url, temperature-cached, reasoning flag try/except)
    └─ OpenAIProvider  → ChatOpenAI (api_key_env, base_url optional)
  PROVIDER_REGISTRY {ollama, openai}
  create_provider(provider, model, cfg)        ← factory
  parse_model_spec(spec, providers_cfg, default="ollama")
```

Separate non-chat providers: `services/embedding_providers.py` (ollama urllib `/api/embeddings`, sentence-transformers legacy). Discovery/install providers: `configuration/runtime_inventory.py` (`/api/tags`,`/api/show`), `configuration/install.py` (`/api/pull`), `configuration/discovery.py`, `ollama.py` (process mgmt only).

---

## 4. Shim / bootstrap inventory

| file:line | what | verdict |
|---|---|---|
| `config.py` (whole, 54 ln) | Legacy dict shim `load()/init()`; ~10 consumers | **Stage 2 delete** after consumers migrate |
| `configuration/bootstrap.py:190` `legacy_config()` | dict snapshot bridge | **Stage 2 retire** |
| `configuration/bootstrap.py:82` `_apply_env_overrides` | `COZMO_OLLAMA_URL` env hack | **Stage 2 replace** (use `ollama.url`) |
| `configuration/bootstrap.py:111-187` `build_configuration`/`get_configuration`/apply-hooks | legit startup wiring | **Keep** |
| `config_cli.py:32-57` | raw `tomli_w.dump` bypassing framework | **Stage 2 replace** (delegate to `Configuration.set`) |
| `webui_server.py:980-1024` `put_config` | legacy bulk-write compat endpoint | **Stage 2 wrap/delete** when frontend migrates |
| `webui_server.py:894-898` `_sync_config_snapshot` | mirrors framework snapshot into legacy `cfg` dict | **Stage 2 delete** |
| `webui_server.py:662-676` `put_conversation` persists `mode:` field | legacy conversation schema | **Stage 2 delete** (conflicts with migrate.py) |
| `runtime/runtime.py:452-456` `force_mode` | deprecated param | **Stage 2 delete** (callers use force_capability) |
| `runtime/runtime.py:762-773` unplanned ReAct path | backward-compat no-plan path | Keep (CLI/headless), note as legacy seam |
| `runtime/runtime.py:1040-1056` `_remember` brain=None `hasattr(memory,add_interaction)` | legacy write fallback | **Stage 2 delete** (Phase G agrees) |
| `tools/search_pipeline.py:88-101,345-367` `rewrite_query`/`synthesize_answer` | dead LLM hooks (tool calls with llm=None) | **Stage 2 delete dead synthesis**; pipeline itself keep |
| `migrate.py`, `brain/storage/migrations.py`, `memory/rebuild.py` | one-time data migrations | Keep (operator tools, documented) |
| module singletons (`memory/manager`, `knowledge_index`, `brain` set/get, `scheduler_task`) | legit global accessors | Keep |

---

## 5. Hardcoded-model inventory

| Occurrence | Classification | Action |
|---|---|---|
| `configuration/model_seeds.py` `SEED_MODEL_FACTS` (gemma4, qwen3:8b, llama3.x, phi3, llava, nomic-embed-text…) | **3. Catalog data** (curated, non-authoritative) | **Keep** |
| `configuration/name_inference.py` (llava, minicpm, qwen2-vl, codegemma, deepseek-coder…) | **1. Evidence/heuristic tokens** (isolated) | **Keep** |
| `configuration/runtime_inventory.py` `llama.context_length` | **3. GGUF protocol metadata keys** | **Keep** |
| `services/embedding_providers.py` `nomic-embed-text` / dim 768 | **3. Default embedding config** | **Keep** (note: dim couples to model; config-driven) |
| `default_skills/skill-creator/scripts/run_eval.py:62` `qwen2.5-coder:7b` | **1. Standalone dev-tool default** | **Keep** (outside runtime) |
| `configuration/builtin.py:25` `Option("ollama", …)` | **4. Legit provider config** | Keep |
| `bootstrap.py:29-40` empty model defaults + `11434` URLs | **4. Legit config defaults** | Keep |
| **Runtime execution (`runtime/`, `models/`, `providers/`, `services/`)**: zero model-name literals | — | Verified clean; guards enforce |

**No illegal runtime hardcoding exists.** `test_no_hardcoded_model_names` + `test_no_model_name_substring_conditionals` already enforce this.

---

## 6. Existing agent / orchestration inventory

- **Orchestrator** (`orchestrator/`): intent classification (keyword + SimpleLLM), complexity estimator (heuristics), evidence grounding (LLM judge), `ExecutionPlan` (model_spec carries `force_model or ""` only — **never bakes a recommended model**, compliant), Task/Job/Checkpoint durable layer.
- **Planner** (`planner/`): `PlannerEngine` deterministic sequential steps; runtime executes them one at a time.
- **Capabilities** (`capabilities/builtin.py`): conversation / research / coding / planning / vision → tool sets + intent keywords.
- **Deep Research today**: NOT a module. WebUI `deep_research` flag → `force_intent="research"` → capability `research` (tools: `web_search`, `web_search_pipeline`, `web_fetch`, `calculator`, `search_knowledge`) + forced grounding + `research` workload model → same single ReAct loop. Search/refetch decisions driven by `RetrievalCoordinator`, not the LLM. `search_pipeline.py` = SearXNG search + fetch + rerank (its LLM synthesis is dead code).
- **No LangGraph anywhere.** `langgraph 1.2.6` + `langgraph-checkpoint` are in the venv but absent from `pyproject.toml`.
- **No CrewAI.**
- Retired-and-gone (verified): legacy `runtime/engine.py`, `ModelRouter`, `_RouterLLM`, `PolicyEngine`, `models.mode`, `llm.roles`.

---

## 7. Proposed runtime architecture

The existing layout is already mostly correct. **Do not restructure wholesale.** Add a thin, explicit `ModelRuntime` seam that formalizes the boundary and removes the implicit scatter:

```
Configuration  (llm.workloads.<w>.model)          ← Cozmo configuration system (untouched)
   ↓
ModelSelector.resolve(workload)                    ← strict; raises ModelUnavailableError when ""
   ↓
ModelService.resolve → (provider, model, cfg)      ← identity + availability validation
   ↓
ModelRuntime.create_chat_model(selected)           ← NEW thin layer (cozmo/runtime/models/)
   |      • takes an ALREADY-RESOLVED selection
   |      • never resolves/recommends/persists/substitutes
   |      • refuses "" (raises ModelUnavailableError before any LangChain construction)
   ↓
providers/base.py get_chat_model() → ChatOllama / ChatOpenAI
   ↓
Provider backend
```

Graph workloads (Stage 3):

```
Configuration → ModelSelector → ModelService → ModelRuntime ─┐
                                                            ↓
                                                  LangGraph workflow
                                                  (nodes receive model via
                                                   graph config — never self-select)
                                                            ↓
                                                 LangChain runnable (ModelRuntime)
```

Minimal new files (preferred):

```
cozmo/runtime/models/
    __init__.py
    factory.py     # ModelRuntime: create_chat_model / bind_tools from resolved selection
cozmo/graphs/
    __init__.py
    research/      # Stage 3 only
        graph.py  state.py  nodes.py  edges.py
```

Placement notes:
- Keep `providers/base.py` where it is — moving it breaks `ALLOWED_PROVIDER_DIRS` in `test_architecture.py`.
- `ModelUnavailableError` must keep its import path (`cozmo.models.ModelUnavailableError`).
- `ModelService` keeps resolve/refresh/validate; its client-construction duties migrate behind `ModelRuntime`.

---

## 8. Proposed migration order

**Stage 1 — Runtime foundation**
- Add `langgraph` to `pyproject.toml` (already in venv; `langgraph-checkpoint` only if Stage 3 needs it — prefer existing `jobs/Checkpoint`).
- Implement `ModelRuntime` (thin).
- Rewire: `runtime.py:610-612/907` bind, `simple_llm.py`, `ModelService.client*` delegation.
- Migrate one simple workload (`general`) end-to-end.
- Add regression tests: `selected X → runtime → LangChain → X` and `selected "" → ModelUnavailableError BEFORE any LangChain client construction`.
- Add architecture guards (#3, #5 below). Full suite stays green.
- STOP and report.

**Stage 2 — Shim cleanup**
- Delete `config.py` shim (migrate ~10 consumers first), `legacy_config()`, `COZMO_OLLAMA_URL` env override, `force_mode`, brain=None `_remember` fallback, `put_config` legacy endpoint + `mode:` conversation field, `config_cli` raw write, dead `search_pipeline` synthesis.
- Update tests. STOP and report.

**Stage 3 — LangGraph Deep Research**
- Only after Stage 1 stable. Research state, nodes (understand → plan → research → evaluate → gaps? → synthesize → verify), edges, tool integration (wrap existing `web_search`/`web_fetch`/`web_search_pipeline`), error/retry.
- Graph gets model via resolver/runtime; must never select another.
- Reuse `jobs/Checkpoint` for resumption unless `langgraph-checkpoint` clearly better. STOP and report.

**Stage 4 — Agentic Coding:** future, not now.

---

## 9. Risks / regressions

1. `runtime/runtime.py` (1071 lines) is the most-tested file (lifecycle/checkpoint/resume/stream-contract tests). Any refactor must preserve the exact stream event contract (`token/reasoning/thinking/tool_call/tool_result/_LOOP_DONE`).
2. `force_model` / `execution_plan.model_spec` override semantics must survive: explicit config override only, never a default, never recommendation-fed.
3. `ModelUnavailableError` import surface is wide — keep path stable.
4. Moving `providers/` breaks existing architecture guard — don't move.
5. `webui_server.py` selection endpoints + frontend (`useFrameworkSettings`, `ModelsSettings`) must remain API-stable.
6. `config.py` has ~10 consumers — Stage 2 deletion is the riskiest step; migrate one-by-one.
7. Embedding dimension/model coupling (Ollama 768-dim) — out of scope, don't touch.
8. Full suite is slow (~5 min) — budget CI/test time accordingly.
9. LangGraph must not change Deep Research behavior before Stage 3 approval; keep `force_intent="research"` path untouched until the graph replaces it.

---

## 10. Files that should NOT be touched (Phase 7)

- `cozmo/configuration/` — the Phase 6 selection system is authoritative: `resolver.py`, `recommendation.py`, `catalog.py`, `model_seeds.py`, `state.py`, `store.py`, `manager.py`, `migration.py`, `eligibility.py`, `qualification.py`, `discovery.py`, `hardware.py`, `evidence.py`, `name_inference.py`, `install.py`, `metadata_cache.py`, `runtime_inventory.py`, `schema.py`, `registry.py`, `events.py`, `builtin.py` (config registration). (Exception: `bootstrap.py` shim bits and `config.py` handled only in Stage 2.)
- `cozmo/brain/**`, `cozmo/memory/**`, `cozmo/evidence/**`, `cozmo/timeline/**`, `cozmo/connectors/**`, `cozmo/jobs/**`, `cozmo/task_queue.py`, `cozmo/scheduler.py` — outside AI-runtime scope.
- `cozmo/ollama.py`, `cozmo/searxng_util.py`, `cozmo/code_indexer.py`, `cozmo/webui.py`, `cozmo/webui_server.py` (routes) — keep as-is (Stage 2 touches only its legacy compat seams).
- `cozmo/default_skills/**`, `cozmo/evaluation/**` — standalone / dev harness.
- `tests/test_architecture.py` and the other 90+ test files — extend guards, don't weaken.

---

## Appendix — Architecture guards to add (per testing requirements)

1. Runtime dirs (incl. new `cozmo/runtime/models/`) contain no hardcoded model IDs (extend existing pattern).
2. No model-name branching in runtime (already enforced; extend to new files).
3. **NEW:** No LangChain model instantiation outside `cozmo/providers/` and `ModelRuntime` — `ChatOllama`/`ChatOpenAI` imports confined (extend `PROVIDER_ONLY_IMPORTS`).
4. **NEW:** LangGraph workflow modules never import `recommend` / `ModelRecommendationEngine` / resolve their own model; model must enter via graph config.
5. **NEW:** `runtime/` must not import `configuration.recommendation` / `configuration.catalog` (no recommendation→execution coupling).
6. No fallback/substitution (already enforced; keep).
7. No Automatic vocabulary (already enforced; keep).
8. **NEW:** `apply_selection` remains sole selection writer — guard that only `configuration/resolver.py` + selection endpoints reference it.