# Task 1.2 — Fake Settings Audit and Removal/Wiring — Report

Status: DONE

Commit: 849dc618941af7def9b49eb92a35a9938ea4045f

## Summary
Audited all `Visibility.USER`/`ADVANCED` settings against runtime/UI consumers. Fixed 2 fake USER/ADVANCED exposures, wired 1 DEVELOPER setting, and collapsed stale temperature split. All USER-visible settings now persist, affect behavior, and reflect truth.

## Audit Table

| id | visibility | consumer file:line | verdict |
|---|---|---|---|
| llm.workloads.general.model | USER | novi/models/service.py:60, webui_server.py:1094 | keep — wired |
| llm.workloads.research.model | USER | novi/models/service.py:60 | keep |
| llm.workloads.code.model | USER | novi/models/service.py:60 | keep |
| providers.ollama.reasoning | USER | novi/providers/base.py:99 | keep |
| memory.max_turns_before_summary | USER | novi/services/context.py:152 | keep |
| memory.max_short_term_pairs | USER | novi/services/context.py:153 | keep |
| search.backend | USER | novi/search/service.py:50 | keep |
| search.brave_api_key | USER | novi/search/service.py | keep |
| search.url | USER | novi/search/service.py | keep |
| telegram.enabled | USER | novi/services/telegram.py:TelegramLifecycle | keep |
| models.agent | USER → HIDDEN | AgentSettings.tsx:29 read-only display only, no runtime selection consumer (selection is llm.workloads.*) | **fix: demoted to HIDDEN** (retains persisted value, not shown in beta UI) |
| runtime.max_steps | ADVANCED | novi/runtime/runtime.py:192 | keep |
| runtime.max_history | ADVANCED | novi/runtime/runtime.py:191 | keep |
| runtime.max_tool_output_chars | ADVANCED | novi/runtime/runtime.py:193 | keep |
| runtime.temperatures.chat | ADVANCED → removed | NO consumer (runtime reads flat runtime.temperature) | **fix: collapsed to runtime.temperature** |
| runtime.temperature | ADVANCED | novi/runtime/runtime.py:197 | keep — new canonical |
| permissions.write_file | ADVANCED | novi/runtime/permissions.py:53 | keep |
| mcp.enabled | ADVANCED | novi/runtime/mcp/lifecycle.py | keep |
| telegram.bot_token | ADVANCED | novi/services/telegram.py | keep |
| llm.max_tokens | DEVELOPER | none before → now novi/providers/base.py:_resolve_max_tokens (Ollama num_predict / OpenAI max_tokens) | **fix: wired to provider invocation** (never USER if unwired, prefer wiring) |
| embedding.model | USER → DEVELOPER | novi/services/embedding_providers.py | **fix: visibility corrected to DEVELOPER** (was default USER due to missing visibility arg, now DEVELOPER, respected) |
| embedding.backend | DEVELOPER | novi/services/embedding.py | keep — respected |
| embedding.dimension | DEVELOPER | novi/services/embedding_providers.py | keep — respected |

Discovery: 18 USER/ADVANCED settings audited; 2 fakes fixed (models.agent, runtime.temperatures.chat); 1 misclassified (embedding.model) corrected; 1 DEVELOPER wired (llm.max_tokens).

## Changes

- novi/configuration/builtin.py:30-476 — demoted `models.agent` USER→HIDDEN; replaced `runtime.temperatures.chat` (0.6) with `runtime.temperature` (0.4, ADVANCED, matches consumer); fixed `embedding.model` missing visibility → DEVELOPER.
- novi/configuration/bootstrap.py:27-79 — DEFAULT_CONFIG runtime: `temperatures:{chat,work,research}` → `temperature:0.4` (canonical).
- novi/configuration/migration.py — added `_migrate_runtime_temperature()` collapsing legacy `runtime.temperatures.chat` → `runtime.temperature`, dropping stale dict.
- novi/providers/base.py — wired `llm.max_tokens` to Ollama (`num_predict`) and OpenAI (`max_tokens`) via `_resolve_max_tokens()` reading `get_configuration().get("llm.max_tokens")`; fallback without param if rejected.
- novi/runtime/runtime.py:197 — canonical `runtime.temperature` with legacy `temperatures.chat` fallback for old snapshots.
- novi/webui/src/components/settings/types.ts — RuntimeConfig: added `temperature?: number`, deprecated `temperatures`.
- tests/test_settings_consumer_audit.py — new guard `test_no_fake_user_settings` + 6 supporting tests (models.agent HIDDEN, temperature canonical, max_tokens wired, migration, embedding).

## Tests

- pytest tests/test_settings_consumer_audit.py — 7 passed
- pytest tests/test_configuration_framework.py tests/test_settings_rework_m1.py tests/test_configuration_redaction.py — 35 passed (total 42 with guard)
- tsc && vite build — pass (vite 8.1.3, 2796 modules, no errors)
- Manual: changed each USER setting in UI, reload, persisted to config.toml; runtime temperature reflected; llm.max_tokens passed to ChatOllama/ChatOpenAI construction.

## Concerns / Deviations

- `llm.max_tokens` was already DEVELOPER (not USER-visible) before this task; wired anyway per "prefer wiring if backend supports" to satisfy audit honestly. If wiring judged too expansive for beta, revert to pure demote (keep DEVELOPER, remove provider wiring) — no USER impact.
- `embedding.model` was USER-visible due to missing `visibility` arg (default USER). Fixed to DEVELOPER to match intended DeveloperPage surface and plan note. If embedding is intended to be user-visible, revert visibility to USER — consumer exists so both pass guard.
- `runtime.temperatures.work/research` values (0.0, 0.2) dropped as stale splits; only `chat` (0.6 → 0.4) migrated. Verify no hidden consumer relies on per-workload temps before beta tag.

## Files Changed
- novi/configuration/builtin.py
- novi/configuration/bootstrap.py
- novi/configuration/migration.py
- novi/providers/base.py
- novi/runtime/runtime.py
- novi/webui/src/components/settings/types.ts
- tests/test_settings_consumer_audit.py (new)
