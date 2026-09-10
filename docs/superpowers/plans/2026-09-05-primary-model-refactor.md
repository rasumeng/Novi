# Primary Model Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace General/Code/Research workload models with one `llm.primary_model` powering all strategies.

**Architecture:** Router classifies task to execution strategy (chat/code/research); ModelSelector/ModelService resolve single primary model verbatim; strategy prompts additive to base instructions.

**Tech Stack:** Python, FastAPI/bottle webui_server, React TS frontend, pytest.

**Spec:** User request 2026-09-05 (Design A approved). Keys: `llm.primary_model` canonical, `embedding.model` separate, single recommendation, strategy terminology, no migration.

## Global Constraints

- No backward compat / migration for old workload models.
- No hidden workload-specific model fallback.
- No extra LLM call in request path.
- Preserve unified execution architecture.
- `embedding.model` stays separate.

---

### Task 1: Config — single primary_model

**Files:**
- Modify: `novi/configuration/builtin.py`
- Modify: `novi/configuration/bootstrap.py`
- Modify: `novi/configuration/migration.py`
- Modify: `novi/configuration/manager.py`
- Modify: `novi/configuration/resolver.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `PRIMARY_MODEL_KEY="llm.primary_model"`, `get_primary_model(config)->str`, `recommend()->PrimaryRecommendation`, `apply_primary_selection(config, model)->dict`.

- [ ] **Step 1: Update builtin.py defaults**
- [ ] **Step 2: Update bootstrap DEFAULT_CONFIG**
- [ ] **Step 3: Rewrite migration to drop llm.workloads**
- [ ] **Step 4: Update manager RETIRED paths**
- [ ] **Step 5: Rewrite resolver recommend/apply to single model**

### Task 2: Runtime resolution + capabilities

**Files:**
- Modify: `novi/models/service.py`
- Modify: `novi/runtime/model_selector.py`
- Modify: `novi/runtime/runtime.py`
- Modify: `novi/runtime/execution_context.py`
- Modify: `novi/services/simple_llm.py`
- Modify: `novi/tools/desktop.py`
- Create: `novi/runtime/strategies.py`

**Interfaces:**
- Consumes: Task 1 primary key.
- Produces: `ModelService.resolve_primary()`, `ModelSelector.resolve_primary()`, `STRATEGIES` dict, `strategy_for()`.

### Task 3: Router → strategy

**Files:**
- Modify: `novi/orchestrator/router.py`
- Modify: `novi/orchestrator/orchestrator.py`
- Modify: `novi/orchestrator/intent.py`, `evidence.py`
- Modify: `novi/graphs/coding_graph.py`, `research_graph.py`
- Modify: `novi/webui.py`, `novi/evaluation/__main__.py`

### Task 4: API + frontend

**Files:**
- Modify: `novi/webui_server.py`
- Modify: `novi/webui/src/components/settings/api.ts`
- Modify: `novi/webui/src/components/settings/ModelsSettings.tsx`
- Delete: `novi/webui/src/components/settings/workloads.ts`
- Modify: `GeneralSettings.tsx`, `useFrameworkSettings.ts`, `CapabilityChips.tsx`

### Task 5: Tests + docs + audit

**Files:**
- Modify: all tests asserting workloads model behavior.
- Modify: `docs/architecture/*`, `DEVLOG`, `CHANGELOG` as needed.
- Run: `pytest` full suite, `npm test` frontend.
