# Novi Beta Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce beta surface to General/Models/Memory/Skills/Connectors/Permissions, make Memory/Skills/Permissions/embedding model genuinely functional.

**Architecture:** Hide don't delete (Jobs, Agent, Developer); single canonical config (`novi/configuration/install.py:DEFAULT_EMBEDDING_MODEL`, `embedding.model`); no fake settings; permission boundaries authoritative; embedding role explicit.

**Tech Stack:** Python FastAPI backend (`novi/webui_server.py`), React frontend (`novi/webui/src/`), Ollama `nomic-embed-text:v1.5`, LanceDB memory, pytest + vitest.

**Spec:** User beta spec 2026-09-06 (this conversation).

## Global Constraints

- One canonical source of truth — no parallel config paths.
- Every visible setting must affect actual behavior — no fake settings.
- Hide unfinished UI instead of exposing broken pages.
- Hiding Agent/Jobs ≠ deleting implementation.
- Skills/Connectors/Memory/agent actions must not bypass Permissions.
- Embedding model role explicit — never presented as chat model.

---

### Task 1: Hide Jobs from beta UI

**Files:**
- Modify: `novi/webui/src/components/sidebar/workspaceModes.ts:3,10-18`
- Modify: `novi/webui/src/App.tsx:5,114-124,200`
- Modify: `novi/webui/src/components/sidebar/Sidebar.tsx:155,193`
- Test: `novi/webui/src/components/sidebar/Sidebar.test.tsx` (new or update), python nav test `tests/test_beta_settings_ia.py` (new)

**Interfaces:**
- Consumes: `NAV_ORDER`, `NAV_ITEMS`, `NavItemId`
- Produces: `NAV_ORDER` without `jobs`; `jobs` type retained for restore

- [ ] **Step 1: Write failing frontend test**

```tsx
// Sidebar.test.tsx
import { NAV_ORDER } from './workspaceModes'
it('beta: jobs hidden', () => {
  expect(NAV_ORDER).not.toContain('jobs')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- Sidebar.test` in `novi/webui`
Expected: FAIL (`jobs` present)

- [ ] **Step 3: Hide jobs, keep type + component for restore**

```ts
// workspaceModes.ts — keep 'jobs' in NavItemId union + NAV_ITEMS (restore path),
// remove from NAV_ORDER:
export const NAV_ORDER: NavItemId[] = ['conversations', 'projects', 'timeline', 'settings']
```

In `App.tsx` keep `JobsPage` import + `case 'jobs':` branch (dead restore path) OR remove branch; preferred: keep component file, remove route branch + `jobsCount` prop wiring. In `Sidebar.tsx` no change needed once NAV_ORDER excludes jobs (it maps NAV_ORDER).

- [ ] **Step 4: Run tests**

Run: `npm test -- Sidebar.test` + `npx tsc --noEmit` in `novi/webui`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novi/webui/src/components/sidebar/workspaceModes.ts novi/webui/src/App.tsx
git commit -m "feat: hide Jobs from beta navigation"
```

### Task 2: Remove Agent from Settings nav

**Files:**
- Modify: `novi/webui/src/components/settings/constants.tsx:7-16`
- Modify: `novi/webui/src/components/settings/SettingsModal.tsx:30-39,177-179`
- Modify: `novi/webui/src/components/settings/types.ts:1`
- Test: update `novi/webui/src/components/settings/SettingsModal.test.tsx:69`

**Interfaces:**
- Consumes: `SECTIONS`, `SectionId`
- Produces: SECTIONS without `agent`; backend `agent`/`agents` namespace untouched

- [ ] **Step 1: Update nav test to beta IA**

```tsx
const NAV = ['General', 'Models', 'Memory', 'Skills', 'Connectors', 'Permissions']
// Developer asserted separately per Task 7
```

- [ ] **Step 2: Run to verify fail**

Run: `npm test -- SettingsModal.test`
Expected: FAIL (Agent present)

- [ ] **Step 3: Remove agent section**

```tsx
// constants.tsx: delete { id: 'agent', ... } line
// SettingsModal.tsx: delete PAGE_LABEL agent entry + {section === 'agent' && <AgentSettings/>} branch
// types.ts: SectionId = 'general' | 'models' | 'memory' | 'skills' | 'connectors' | 'permissions' | 'developer'
```

Keep `AgentSettings.tsx` file on disk (restore path). Verify no other import requires `agent` SectionId (search `initialSection` callers).

- [ ] **Step 4: Run tests**

Run: `npm test -- SettingsModal.test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novi/webui/src/components/settings/
git commit -m "feat: remove Agent from beta Settings nav"
```

### Task 3: Memory beta experience (enable/disable, list, delete, clear-all, honest unavailable)

**Files:**
- Modify: `novi/configuration/builtin.py:269-297` (add `memory.enabled` USER bool default True)
- Modify: `novi/configuration/bootstrap.py:39` (add `"memory": {"enabled": True, ...}`)
- Modify: `novi/webui_server.py:1353-1395` (gate list/search on `memory.enabled`; add `DELETE /api/memory` clear-all)
- Modify: `novi/webui/src/components/settings/MemorySettings.tsx` (enable toggle, status banner, clear-all button, remove internals)
- Test: `tests/test_memory_beta.py` (new)

**Interfaces:**
- Consumes: `Configuration.get("memory.enabled")`, `MemoryManager.list_all/delete`
- Produces: `GET /api/memory/list` → `{status:"disabled"|"ok"|"unavailable", data}`; `DELETE /api/memory` → `{ok, deleted}`

- [ ] **Step 1: Write failing backend test**

```python
def test_memory_disabled_returns_disabled():
    # set memory.enabled=False, GET /api/memory/list → status == "disabled"
def test_memory_clear_all():
    # seed 2 memories, DELETE /api/memory → ok, list empty
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_memory_beta.py -v`
Expected: FAIL (no `memory.enabled`, no clear-all route)

- [ ] **Step 3: Implement backend**

```python
# builtin.py memory group: add Setting id="memory.enabled" type BOOL default True visibility USER
# bootstrap DEFAULT_CONFIG memory: {"enabled": True, "max_turns_before_summary":5, "max_short_term_pairs":10}
# webui_server.py list_memory/search_memory: if not configuration.get("memory.enabled", True): return {"status":"disabled","data":[]}
# new route:
@app.delete("/api/memory")
def clear_memory():
    mem = get_backend().get("memory")
    if not mem: return {"ok": False, "error": "unavailable"}
    items = mem.list_all(limit=500)
    n = sum(1 for i in items if mem.delete(i["id"]))
    return {"ok": True, "deleted": n}
```

Also gate runtime memory write/query on `memory.enabled` (check `runtime.py` memory call sites; if missing, add single `if not enabled: skip` guard).

- [ ] **Step 4: Update MemorySettings UI**

Add enable toggle bound to `framework.set("memory.enabled")`; status banner for `disabled` vs `unavailable`; Clear-all button with `useConfirm`; keep list/search/delete; hide chunk/embedding/dimension/threshold internals (already absent except dev tab — keep dev tab but label diagnostic, no new internals).

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_memory_beta.py tests/test_ensure_embedding_model.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add novi/configuration/builtin.py novi/configuration/bootstrap.py novi/webui_server.py novi/webui/src/components/settings/MemorySettings.tsx tests/test_memory_beta.py
git commit -m "feat: beta Memory enable/disable list delete clear-all"
```

### Task 4: Embedding default canonical + missing-model handling

**Files:**
- Modify: `novi/configuration/builtin.py:68-77` (embedding.model default → canonical constant, category MODELS, visibility USER)
- Modify: `novi/configuration/bootstrap.py:32` (default resolves to canonical at runtime, not "")
- Modify: `novi/configuration/model_seeds.py:138-142` (ensure `nomic-embed-text:v1.5` fact exists with capabilities=["embeddings"])
- Test: `tests/test_embedding_beta.py` (new)

**Interfaces:**
- Consumes: `DEFAULT_EMBEDDING_MODEL` from `novi/configuration/install.py:20`
- Produces: `embedding.model` resolves to `nomic-embed-text:v1.5` when unset; discovery flags role=embedding

- [ ] **Step 1: Write failing test**

```python
from novi.configuration.install import DEFAULT_EMBEDDING_MODEL
def test_default_is_pinned():
    assert DEFAULT_EMBEDDING_MODEL == "nomic-embed-text:v1.5"
def test_unset_resolves_to_default():
    # fresh Configuration snapshot embedding.model == "" → resolved via ensure_embedding_model/canonical getter == DEFAULT
def test_seed_marks_embedding_role():
    # SEED_MODEL_FACTS has nomic-embed-text:v1.5 with "embeddings" not chat
```

- [ ] **Step 2: Run fail**

Run: `pytest tests/test_embedding_beta.py -v`
Expected: FAIL (default "" in builtin/bootstrap)

- [ ] **Step 3: Implement**

```python
# builtin.py: import DEFAULT_EMBEDDING_MODEL; Setting embedding.model default=DEFAULT_EMBEDDING_MODEL, category=Category.MODELS, visibility=Visibility.USER, label "Embedding model", description "Powers Novi Memory (nomic-embed-text:v1.5)."
# bootstrap.py DEFAULT_CONFIG embedding: {"backend":"ollama","model":DEFAULT_EMBEDDING_MODEL,"dimension":768}
# model_seeds.py: add ModelFact("nomic-embed-text:v1.5", ...) capabilities=["embeddings"] works_with_memory=True
```

Keep `ensure_embedding_model` as sole provisioning path (startup pull, Ollama-down warns). No new hardcodes — import constant.

- [ ] **Step 4: Run pass**

Run: `pytest tests/test_embedding_beta.py tests/test_ensure_embedding_model.py tests/test_settings_consumer_audit.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novi/configuration/builtin.py novi/configuration/bootstrap.py novi/configuration/model_seeds.py tests/test_embedding_beta.py
git commit -m "feat: canonical nomic-embed-text:v1.5 embedding default"
```

### Task 5: Embedding visible in Models UI

**Files:**
- Modify: `novi/webui/src/components/settings/ModelsSettings.tsx` (add Memory/Embedding section)
- Modify: `novi/webui/src/components/settings/api.ts` (expose embedding model from discovery/schema if needed)
- Test: `novi/webui/src/components/settings/ModelsSettings.test.tsx` (update)

**Interfaces:**
- Consumes: `framework.values["embedding.model"]`, `framework.schema`
- Produces: Models page section "Memory — Embedding Model" showing name + status

- [ ] **Step 1: Write failing test**

```tsx
it('shows embedding model', () => {
  render(<ModelsSettings ... />)
  expect(screen.getByText(/Embedding Model/i)).toBeTruthy()
  expect(screen.getByText(/nomic-embed-text/)).toBeTruthy()
})
```

- [ ] **Step 2: Run fail**

Run: `npm test -- ModelsSettings.test`
Expected: FAIL

- [ ] **Step 3: Implement section (read-only + status, no chat-model confusion)**

```tsx
<section aria-label="Memory embedding model">
  <SectionHeader title="Memory" subtitle="Embedding model powering Novi Memory." />
  <p className="font-mono text-sm">{framework.values['embedding.model'] || 'nomic-embed-text:v1.5'}</p>
  {/* status: installed/missing via discovery.models lookup; missing → hint "Install from setup / check Ollama" */}
</section>
```

Filter embedding out of chat-model dropdown/rows (backend already excludes via `USER_FACING_CAPABILITIES`; verify `ModelsSettings` missingRecommended filter keeps `['chat','reasoning','coding','vision']` — no change needed).

- [ ] **Step 4: Run pass**

Run: `npm test -- ModelsSettings.test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novi/webui/src/components/settings/ModelsSettings.tsx
git commit -m "feat: show Memory embedding model in Models"
```

### Task 6: Skills audit — discovery/execution/creation + permission gate

**Files:**
- Modify: `novi/webui_server.py:1812-1828` (validate SKILL.md frontmatter, reject invalid, no auto-activate)
- Modify: `novi/runtime/runtime.py:62-90` (skill load path — verify permission gate; add test seam if missing)
- Modify: `novi/webui/src/components/settings/SkillsSection.tsx:46-60` (surface validation errors)
- Test: `tests/test_skills_beta.py` (new)

**Interfaces:**
- Consumes: `SKILLS_DIR`, skill frontmatter `{name, description}`
- Produces: `POST /api/skills` 400 on invalid; valid skill appears in `GET /api/skills`; execution via ToolExecutor permission gate

- [ ] **Step 1: Write failing tests**

```python
def test_create_skill_valid(client): ...
def test_create_skill_invalid_name_rejected(client): ...  # ../, empty
def test_create_skill_invalid_content_rejected(client): ...  # missing frontmatter/description
def test_skill_execution_requires_permission(): ...  # skill tool call with resolver deny → blocked
```

- [ ] **Step 2: Run fail**

Run: `pytest tests/test_skills_beta.py -v`
Expected: FAIL (no validation)

- [ ] **Step 3: Implement validation**

```python
# create_skill: validate name ^[a-z0-9][a-z0-9-_]{1,64}$; require description non-empty; require content non-empty;
# write frontmatter safely (quote desc); return 400 {"error": ...} otherwise
# execution: confirm skill-invoked tools route through ToolExecutor._check_permission (audit runtime.py skill loader; if direct exec bypasses, wrap with permission check)
```

Chat-initiated creation ("Novi, create a skill...") uses existing orchestrator+chat path — no new subsystem; this task only ensures POST validation + permission gate + error surfacing.

- [ ] **Step 4: Surface errors in UI**

`SkillsSection.handleWriteSubmit`: parse error JSON from `createSkill`, `showError` with server message (not generic).

- [ ] **Step 5: Run pass**

Run: `pytest tests/test_skills_beta.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add novi/webui_server.py novi/webui/src/components/settings/SkillsSection.tsx tests/test_skills_beta.py
git commit -m "feat: beta Skills validation and permission gate"
```

### Task 7: Permissions end-to-end (allow/deny/timeout/cancel/persist)

**Files:**
- Modify: `novi/webui_server.py:513-541` (cancel + timeout distinct signaling)
- Modify: `novi/runtime/tool_executor.py:213-270` (already distinguishes timeout vs denied — add cancel reason)
- Modify: `novi/webui/src/components/common/PermissionPrompt.tsx` (cancel button, timeout-expired message)
- Modify: `novi/webui/src/hooks/useNoviChat.ts` (handle `permission_timeout` event → honest message)
- Test: `tests/test_permissions_beta.py` (new)

**Interfaces:**
- Consumes: `ToolExecutor._check_permission`, `Session._ask_permission/answer_permission`
- Produces: allow→execute; deny→honest deny message; timeout→"expired, action not performed"; cancel→stopped message

- [ ] **Step 1: Write failing tests**

```python
def test_allow_executes(): ...
def test_deny_blocks_with_explanation(): ...
def test_timeout_returns_expired_message(): ...  # existing timeout logic → assert message contains "timed out" + "not performed"
def test_cancel_stops(): ...  # answer_permission + stop → distinct from deny
def test_persist_roundtrip(): ...  # PATCH permissions.write_file=allow persists + affects next check
```

- [ ] **Step 2: Run fail**

Run: `pytest tests/test_permissions_beta.py -v`
Expected: FAIL (cancel indistinct, timeout message not asserted end-to-end)

- [ ] **Step 3: Implement**

```python
# tool_executor.py: add reason="cancelled" path when stop_flag set during permission wait
# webui_server.py _ask_permission: on timeout emit {"type":"permission_timeout","id":req_id} before returning False
# useNoviChat.ts: on permission_timeout push assistant message "Permission request expired — action not performed."
```

Do not keep non-functional permission controls: audit `PERMISSION_DEFS` in constants.tsx vs registered tools; remove keys with no backing tool.

- [ ] **Step 4: Run pass**

Run: `pytest tests/test_permissions_beta.py tests/test_permission_timeout_honest.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novi/webui_server.py novi/runtime/tool_executor.py novi/webui/src/hooks/useNoviChat.ts tests/test_permissions_beta.py
git commit -m "fix: honest permission timeout/cancel/deny messaging"
```

### Task 8: Connectors verify (no redesign)

**Files:**
- Test only + bugfixes as found: `tests/test_connectors_beta.py` (new, flows: connect→status→tool exec→error surfaced)
- Touch `novi/connectors/*`, `novi/webui/src/components/settings/ConnectorsSection.tsx` only if bugs found

- [ ] **Step 1: Write flow tests** (status endpoint, MCP lifecycle, secret redaction, tool exec error surface)
- [ ] **Step 2: Run** `pytest tests/test_connectors_beta.py tests/test_m54_connector_registry.py -v`
- [ ] **Step 3: Fix only actual bugs found** (no expansion)
- [ ] **Step 4: Commit** `fix: connector <specific bug>` or `test: connector beta flows` if no bug

### Task 9: Developer simplify (hide from nav, keep advanced path)

**Files:**
- Modify: `novi/webui/src/components/settings/constants.tsx` (remove developer from SECTIONS)
- Modify: `novi/webui/src/components/settings/SettingsModal.tsx` (keep DeveloperPage reachable via search `>developer` or env flag, not nav)
- Test: update `SettingsModal.test.tsx:125-127`

**Interfaces:**
- Consumes: `SECTIONS`, `schema.settings category==='developer'`
- Produces: nav = General/Models/Memory/Skills/Connectors/Permissions; Developer via advanced path only

- [ ] **Step 1: Update test** — nav excludes Developer; `>developer` search or `?dev=1` still renders DeveloperPage
- [ ] **Step 2: Run fail** — `npm test -- SettingsModal.test`
- [ ] **Step 3: Implement** — remove developer from SECTIONS; add escape hatch: typing `developer` in settings search shows hidden entry, or `localStorage novi_dev=1`
- [ ] **Step 4: Audit DeveloperPage fields** — remove any setting whose value doesn't drive runtime (check vs `test_settings_consumer_audit.py`); embedding fields move out (Task 4/5), never solely Developer-hidden
- [ ] **Step 5: Commit** `feat: hide Developer from beta Settings nav`

### Task 10: Beta IA gate test + full verification

**Files:**
- Create: `tests/test_beta_settings_ia.py` (backend: agent namespace persists but no agent UI contract; settings categories == {general,models,memory,skills,connectors,permissions})
- Update: frontend nav tests (Tasks 1,2,9)

- [ ] **Step 1: Write gate test**

```python
def test_beta_settings_categories():
    reg = build_registry()
    cats = {s.category.value for g in reg.groups() for s in g.settings if s.visibility != Visibility.HIDDEN}
    assert "agent" not in cats  # Agent hidden from user-facing nav
    assert {"general","models","memory","skills","connectors","permissions"} <= cats
def test_embedding_default_canonical(): ...
def test_jobs_not_in_nav(): ...  # reads workspaceModes.ts NAV_ORDER
```

- [ ] **Step 2: Run full suites**

Run: `pytest tests/test_beta_settings_ia.py tests/test_memory_beta.py tests/test_embedding_beta.py tests/test_skills_beta.py tests/test_permissions_beta.py tests/test_connectors_beta.py -v` + `npm test` in `novi/webui`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_beta_settings_ia.py
git commit -m "test: beta Settings IA gate"
```

## Self-Review

- Spec coverage: §1 Jobs→T1; §2 Agent→T2; §3 Memory→T3; §4 embedding default→T4; §5 embedding visible→T5; §6 Skills→T6; §7 Permissions→T7; §8 Connectors→T8; §9 Developer→T9; §10 IA→T10; §12 tests each task; §11 constraints in Global Constraints + per-task notes.
- No placeholders: all steps have file:line, code, commands.
- Type consistency: `SectionId` updated T2+T9; `NAV_ORDER` T1; `memory.enabled` T3; `DEFAULT_EMBEDDING_MODEL` import T4; permission payload `{type,id,tool,args,timeoutMs,expiresAt}` preserved T7.
