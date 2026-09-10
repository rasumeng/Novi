# Beta Stabilization Sprint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Novi's existing beta surface trustworthy and coherent — no new features — by fixing capability source-of-truth, settings honesty, persistence safety, project linking, honest failure handling, UX completeness, resource hygiene, and verification gate.

**Architecture:** Five sequential phases (Correctness → Honest Failures → UX/Resource Hygiene → Legacy Removal → Verification). Each phase is independently testable. Changes stay inside existing seams: `model_selector`, `runtime_inventory/discovery/metadata_cache`, `webui_server` persistence, configuration framework (`builtin/registry/manager`), and React settings/UX panels. No new architectural systems; no migrations unless proven required.

**Tech Stack:** Python 3.10+ / FastAPI / `configuration` framework / `runtime_inventory` / `runtime/model_selector` / `webui_server` (sync I/O + threading) / React 18 + Vite + Tailwind + TypeScript / Vitest / Pytest

**Spec:** Audit `2026-09-03` Beta Readiness Audit + Review `Accepted with Priority Adjustments` (5-phase direction) in this prompt.

## Global Constraints
- Do not increase feature count; stabilize and make honest existing behavior only.
- Correctness → Source of Truth → Honest Failures → UX States → Verification ordering; phase N must pass before N+1.
- Prefer simple deterministic fix; avoid broad refactors or new subsystems unless required to fix a documented beta risk.
- Model-name substring heuristics are never authoritative; runtime `/api/show` + cache is authoritative for capabilities.
- Never silently substitute, silently degrade, or claim unknown = unsupported; use tri-state `supported/unsupported/unknown` + `verification_failed` where needed.
- Every USER-visible setting must persist, affect behavior, and reflect current value — or be removed.
- Verification is a release gate: `pytest`, `tsc --noEmit`, `vite build`, persistence/capability tests, manual first-run matrix all green before beta tag.
- Keep existing storage shape where possible; improve safety (atomic writes, locks) rather than migrate unless proven unsafe.

---

## Phase 1 — Core Correctness and Source of Truth

### Task 1.1 — Unknown Model Capability Validation (vision/audio/tools)

**Objective:** Eliminate false-rejection of vision/audio-capable models not in seed and without cached metadata, without replacing it with false-allow.

**Files:**
- Modify: `novi/runtime/model_selector.py:64-97` (`model_capabilities`, `ModelCapabilities`)
- Modify: `novi/runtime/runtime.py:606-634` (image/audio validation)
- Modify: `novi/configuration/discovery.py:47-71,113-128` (`cached_runtime_capabilities`, `show_model`)
- Modify: `novi/configuration/runtime_inventory.py:41-99` (`_RUNTIME_CAPABILITY_TOKENS`, `query_ollama_show`)
- Modify: `novi/configuration/model_records.py:59-81` (`CapabilityEvidence` tri-state stays)
- Modify: `novi/webui_server.py:1215-1233` (`workload_capabilities` + discovery payload `capabilityEvidence`)
- Modify: `novi/webui/src/components/settings/ModelsSettings.tsx:754-962` (`CapabilityEvidenceList`, `StatusBadge` messaging)
- Create/Modify Tests: `tests/test_capability_verification.py` (new), `tests/test_model_selector.py`, `tests/test_runtime_capability_validation.py`

**Interfaces:**
- Consumes: `ModelDiscovery.show_model(name) -> Optional[ModelRecord]`, `ModelMetadataCache.get/set`, `ModelRecord.capability_support(cap) -> Optional[bool]`
- Produces: `CapabilityState = Literal["supported","unsupported","unknown","verification_failed"]`, `model_capabilities(name) -> ModelCapabilities` with `unknown` tracked, `verify_capability(name, cap) -> CapabilityState` (attempts cached → live `/api/show` → unknown/verification_failed)

**Current behavior:**
`model_capabilities()` merges seed + `cached_runtime_capabilities()` only. Cold/new model not in seed + cache miss → empty caps → vision check rejects as unsupported. UI shows provenance but runtime gate disagrees.

**Target behavior:**
Tri-state capability model shared UI+runtime: `supported / unsupported / unknown / verification_failed`. Validation flow: known unsupported → block with `unsupported` message; known supported → allow; unknown → attempt live `query_ollama_show(name)` once (bounded timeout, cached on success); if live succeeds use it authoritative; if live fails/404/timeout → `verification_failed` / `unknown`. Unknown never rendered as "does not support"; runtime either allows with `capability-unverified` trace/notice when safe, or errors "capability could not be verified" when not safe. `model_capabilities` stays pure/deterministic (no network); live verification lives in `ModelDiscovery`/`ModelSelector.verify()` path that runtime validation calls.

**Implementation approach:**
- Add `CapabilityState` enum and helper `capability_state(record, cap) -> CapabilityState` in `configuration/evidence.py` or `model_records.py`.
- Add `ModelDiscovery.verify_capabilities(name, caps: set[str]) -> dict[str, CapabilityState]` that checks cache, then tries `query_ollama_show` (timeout 3s), caches, maps `_RUNTIME_CAPABILITY_TOKENS` including any new `audio`/`speech` alias, returns per-cap tri-state.
- Change `runtime/model_selector.py` to expose `model_capability_state(name, cap) -> CapabilityState` and `validate_with_state()`; keep `model_capabilities()` for back-compat but derive from state.
- Change `runtime/runtime.py:615-634` to call verify path: if vision/audio requested, call selector/discovery verify; branch on tri-state (unsupported=block, supported=allow, unknown/verification_failed=allow-with-notice OR block-with-verifiable-error per safety policy doc: safe to attempt → allow with `trace` event `capability_unverified`; not safe → error `capability could not be verified — connect Ollama and retry`).
- Extend discovery payload to include `capabilityStates: Record<string, CapabilityState>` alongside `capabilityEvidence` (additive, no breaking change).
- Update `ModelsSettings.tsx` evidence list to render `Unknown — not yet verified (tap Rescan)` vs `Verified unsupported`.
- Seed heuristics (`name_inference`) demoted to not influence gate; only UI hint.

- [ ] **Step 1: Write failing test — unknown vision model not falsely rejected (no network)**
```python
def test_unknown_model_not_confirmed_unsupported():
    from novi.runtime.model_selector import model_capability_state
    # no seed, no cached runtime caps
    assert model_capability_state("qwen3-unknown:7b", "vision") in ("unknown","verification_failed")
```
Run: `pytest tests/test_capability_verification.py::test_unknown_model_not_confirmed_unsupported -v` Expected: FAIL (currently returns unsupported)

- [ ] **Step 2: Implement CapabilityState tri-state**
```python
class CapabilityState(str, Enum):
    SUPPORTED="supported"; UNSUPPORTED="unsupported"; UNKNOWN="unknown"; VERIFICATION_FAILED="verification_failed"
def capability_state(record, cap): ...
```

- [ ] **Step 3: Add live verify path in discovery**
```python
def verify_capabilities(self, name, caps): 
    cached = _CACHE.get(...)
    if cached is None:
        payload = query_ollama_show(url, name, timeout=3.0)
        if payload is not None: _CACHE.set(...)
```

- [ ] **Step 4: Wire runtime validation to tri-state**
```python
state = self._model_selector.verify("vision", ctx.model_name)
if state == CapabilityState.UNSUPPORTED: yield error blocked
elif state in (UNKNOWN, VERIFICATION_FAILED): yield status capability_unverified; allow
else: allow
```

- [ ] **Step 5: Extend discovery payload + UI rendering, commit**
```
payload["capabilityStates"] = {cap: capability_state(record, cap).value for cap in ("vision","audio","tools")}
```
Run: `pytest tests/test_capability_verification.py -v`, `pytest tests/test_runtime_capability_validation.py -v`

**Tests/verification:**
- Unit: seed known supported/unsupported, runtime token supported, unknown cold, cache hit, live fetch success/failure, timeout → verification_failed, never `unsupported` for unknown.
- Integration: `NoviRuntime.run_stream` with image attachment for unknown vision model (mocked `query_ollama_show` success → allows; mocked failure → allows-with-notice or verifiable error, never "does not support").
- UI: `ModelsSettings` shows Unknown vs Unsupported correctly.
- Contract: `model_capability_state` shared helper used bothPayload and runtime.
- Manual: install new uncached `qwen2.5vl:7b` variant, attach image → not falsely rejected.

**Dependencies:** None (first). Blocks 2.1 hardware/capability messaging.

---

### Task 1.2 — Fake Settings Audit and Removal/Wiring

**Objective:** Guarantee every USER-visible setting persists, affects behavior, and reflects truth; remove or wire fakes.

**Files:**
- Modify: `novi/configuration/builtin.py:30-476` (registry)
- Modify: `novi/configuration/bootstrap.py:27-79` (DEFAULT_CONFIG)
- Read/Audit: `novi/webui/src/components/settings/SettingsModal.tsx`, `GeneralSettings.tsx`, `ModelsSettings.tsx`, `AgentSettings.tsx`, `MemorySettings.tsx`, `DeveloperPage`, `SettingField.tsx`, `useFrameworkSettings.ts`
- Modify: `novi/webui/src/components/settings/types.ts` (if settings removed)
- Modify: `novi/configuration/migration.py` (if keys removed/renamed)
- Create Tests: `tests/test_settings_consumer_audit.py` (new), `tests/test_configuration_registry_visibility.py` (extend)

**Current behavior:**
Several USER-visible settings register with `Visibility.USER` but have no runtime consumer: `llm.max_tokens` (never read), `models.agent` (registered `Category.MODELS` USER but never selected by router/selector), `runtime.temperatures.chat` (only one used, others hidden). `agent`/`agents` namespaces hidden but flushed via `legacyPatch`.

**Target behavior:**
For every setting with `visibility` USER/ADVANCED (user-visible), prove consumer exists. No user-visible setting silently does nothing. Decision per setting: wire to runtime (add consumer) or remove from schema (hide/deprecate with migration). Specifically:
- `llm.max_tokens`: wire to LLM invocation `max_tokens` (read in `runtime/runtime.py` or provider bind) OR demote to DEVELOPER/HIDDEN with note, OR remove from USER surface. Prefer wiring if backend supports it.
- `models.agent`: remove USER visibility (HIDDEN or delete) — workload model is `llm.workloads.*` only per locked constraint. Migration keeps persisted value but not shown.
- `runtime.temperatures.*`: collapse to `runtime.temperature` or per-workload temps actually consumed (`runtime/runtime.py:197 temperature` vs `temperatures.chat/work/research` split). Make name match consumer.
- `embedding.*` in DeveloperPage: keep but ensure `embedding_providers.py` / `memory` actually respect `embedding.model/dimension/backend`.

**Implementation approach:**
- Audit script: enumerate `reg.all_settings()` where `visibility in (USER, ADVANCED)` and grep repo for `configuration.get("id")` / `cfg.get("...")` consumers. Produce table: id | visibility | consumer file:line | verdict.
- For each fake: open PR decision comment, then either add `self.max_tokens = cfg.get("llm",{}).get("max_tokens")` wiring + bind to `ChatOllama` max_tokens, or delete `Setting` registration and add `migration.py` rule dropping or moving key.
- Remove `models.agent` USER registration (keep `agents` namespace if needed internally but HIDDEN).
- Update `SettingsModal` `DeveloperPage` sections that iterate `schema.settings` — removal automatically hides.
- Add architecture guard test `test_no_fake_user_settings` that fails if new USER setting lacks consumer tag.

- [ ] **Step 1: Write failing audit test**
```python
def test_every_user_setting_has_consumer():
    reg = build_registry()
    fakes = [s.id for s in reg.all_settings() if s.visibility in (Visibility.USER, Visibility.ADVANCED) and not find_consumer(s.id)]
    assert fakes == [], f"Fake user settings: {fakes}"
```
Run: `pytest tests/test_settings_consumer_audit.py -v` Expected: FAIL listing `llm.max_tokens`, `models.agent`

- [ ] **Step 2: Wire or remove each fake (one commit per setting)**
Remove: `reg.register_group(... Setting(id="models.agent", ... visibility=Visibility.HIDDEN) ...)` or delete.
Wire: `runnable = ChatOllama(..., max_tokens=cfg.get("llm",{}).get("max_tokens"))`

- [ ] **Step 3: Add migration for removed keys**
```python
# migration.py: if "models" in raw and "agent" in raw["models"]: raw["models"].pop("agent")
```

- [ ] **Step 4: Verify**
Run: `pytest tests/test_settings_consumer_audit.py tests/test_configuration_registry_visibility.py -v`, `tsc --noEmit`, manual: change each setting in UI, reload, assert effect + persisted `~/.novi/config.toml`.

**Dependencies:** 1.1 capability state (UI evidence display).

---

### Task 1.3 — Conversation Persistence Safety

**Objective:** Prevent corruption/divergence between `index.json` and per-conversation `.md` under concurrent/rapid writes, streaming completion, switch/shutdown.

**Files:**
- Modify: `novi/webui_server.py:688-831` (`_conversations_idx`, `_save_idx`, `_conv_to_file`, `_load_messages`, `PUT /api/conversations`, `DELETE /api/conversations`)
- Modify: `novi/webui_server.py:98-99` (add `asyncio.Lock` or `threading.Lock` for index)
- Modify: `novi/webui/src/hooks/useNoviChat.ts:153-163` (dirtyIdRef effect)
- Create Tests: `tests/test_conversation_persistence_atomic.py` (new), `tests/test_conversation_parse_safety.py` (new)
- Optional: `novi/services/conversation_store.py` (extract if plan prefers)

**Current behavior:**
Index (`index.json` meta) + `.md` (messages) written non-atomically via `write_text`. No lock. `_load_messages` splits on `^## (User|Novi|Cozmo)$` — user content containing that line is misinterpreted. `dirtyIdRef` effect in hook can race with rapid switches.

**Target behavior:**
- Atomic writes: write to `index.json.tmp` + `os.replace` (atomic on POSIX/Win), same for `{id}.md.tmp` + replace. Order: `.md` first, then index, so crash never leaves orphan index entry pointing to missing file.
- Locking: single `threading.RLock` (or `filelock`) guarding `_save_idx` + `_conv_to_file` pair; also guard `_projects_idx` if shared.
- Index/content consistency: `GET /api/conversations` rebuilds from index but validates `_safe_child` file exists; missing `.md` entry flagged, not silently dropped. `PUT` validates `id` already.
- Parsing safety: escape `## User` etc. in message bodies on write and unescape on read, or use unambiguous delimiter (`@@NOVI_MSG@@` or JSON frontmatter). Prefer minimal escaping of leading `## ` in body to `\#\# ` or encode markers.
- Concurrent test: rapid sequential PUTs for same `id` from parallel threads/clients — no partial JSON, no lost title, last write wins intact.

**Implementation approach:**
- Add `CONVERSATIONS_LOCK = threading.RLock()` alongside `_shared_backend` lock.
- Wrap `_save_idx` + md write in lock; change `Path.write_text` to `tmp = path.with_suffix(".tmp"); tmp.write_text(...); tmp.replace(path)`.
- Add `_escape_message_body` / `_unescape_message_body` around `re.match(r"^## ...")` or change delimiter to `---novimsg---` unique unlikely string.
- Keep existing `.md` shape for backward compat; add migration that on first read rewrites with escaping if needed.
- Hook change: debounce dirtyIdRef effect already single; server lock is main fix.

- [ ] **Step 1: Write failing concurrent test**
```python
def test_concurrent_conversation_writes_no_corruption(tmp_path):
    # 20 threads each PUT same id with different title/content
    # after join, index.json must be valid JSON and .md must parse to exactly one title/messages set
```

- [ ] **Step 2: Implement atomic writes + lock**
```python
_CONV_LOCK = threading.RLock()
def _atomic_write(path, data): tmp=path.with_name(path.name+".tmp"); tmp.write_text(data); tmp.replace(path)
```

- [ ] **Step 3: Fix parsing with escaping**
```python
def _escape_body(text): return re.sub(r'^## ', r'\#\# ', text, flags=re.MULTILINE)
```

- [ ] **Step 4: Verify**
Run: `pytest tests/test_conversation_persistence_atomic.py tests/test_conversation_parse_safety.py -v` Expected: PASS. Manual: rapid edit + switch + restart → no corruption.

**Dependencies:** None. Can run parallel to 1.1 but avoid touching same `webui_server.py` region concurrently.

---

### Task 1.4 — Canonicalize Project ↔ Conversation Linking

**Objective:** One authoritative representation; no best-effort dual sync.

**Files:**
- Modify: `novi/webui_server.py:1396-1570` (projects CRUD, `update_project`, `GET /api/projects/{id}/conversations`)
- Modify: `novi/webui_server.py:804-831` (conversation PUT/DELETE handling of `projectId`)
- Modify: `novi/webui/src/hooks/useNoviChat.ts:120-144` (backfill effect), `92-107` (projects load), `770-837` (project create/update/delete)
- Modify: `novi/webui/src/App.tsx:72-86` (project conversation routing)
- Create Tests: `tests/test_project_conversation_linking.py` (new)

**Current behavior:**
Two persisted copies: `conversation.projectId` (in `index.json` entry per conversation) and `project.conversationIds[]` (in `projects/index.json`). Sync via hook backfill effect + server delete prune. Can diverge.

**Target behavior:**
Choose canonical single source: `conversation.projectId` authoritative (simpler, conversation is the join). `project.conversationIds` derived on read (scan `conversations_idx` for `projectId == pid`) — never persisted independently, or vice versa with single write transaction under same lock as conversations. For beta, minimal: keep `project.conversationIds` but make it derived. Writes: `PUT /api/conversations` with `projectId` is canonical; `PUT /api/projects/{id}` with `conversationIds` rejected or treated as derived update that actually moves conversations' `projectId`. Reads: `GET /api/projects/{id}/conversations` scans conversations index; `GET /api/projects` returns derived `conversationIds` field computed on the fly.

**Implementation approach:**
- Decide canonical: `conversation.projectId` (migration: on load, if project has `conversationIds` but conversation missing `projectId`, backfill `projectId` once).
- Remove persisted `conversationIds` from `projects/index.json` write path; add `def _derive_project_conversation_ids(project_id): return [c["id"] for c in idx["conversations"] if c.get("projectId")==project_id]`.
- Update `_save_projects_idx` callers to not expect `conversationIds` persisted; webui_server `create_project`/`update_project` no longer store it, instead `PUT /api/conversations` is used to move conversations.
- Keep `conversationIds` in API response derived for backward compat with frontend; frontend hook backfill effect removed (or guarded to no-op).
- Migration: on first `GET /api/projects`, if `projects/index.json` still contains `conversationIds`, run one-time derived recompute and strip persisted field.

- [ ] **Step 1: Write failing consistency test**
```python
def test_linking_single_source():
    # PUT conversation with projectId=X then read project → conversationIds includes it; update project via conversationIds mismatch → conversation wins
```

- [ ] **Step 2: Implement derived conversationIds, deprecate persisted**
- [ ] **Step 3: Remove hook backfill sync, update App routing**
- [ ] **Step 4: Verify**
Run: `pytest tests/test_project_conversation_linking.py -v`. Manual: create project, move conversation, reassign, restart → consistent.

**Dependencies:** 1.3 (shared `conversations_idx` lock/file).

---

## Phase 2 — Honest Failure Handling

### Task 2.1 — Ollama Honest Discovery Failure

**Objective:** Empty discovery never looks successful; stale cache clearly marked; actionable UI guidance.

**Files:**
- Modify: `novi/configuration/discovery.py:81-96` (`installed()` return + stale flag)
- Modify: `novi/webui_server.py:1178-1233` (`GET /api/models/discovery` payload)
- Modify: `novi/webui/src/components/settings/api.ts:162-178` (`fetchDiscovery` error contract)
- Modify: `novi/webui/src/components/settings/ModelsSettings.tsx:122-237` (library + missing-count banner), `GeneralSettings.tsx:90-173` (warnings), `webui/src/hooks/useFrameworkSettings.ts` (discovery state)
- Create Tests: `tests/test_discovery_honest_errors.py` (new)

**Current:** Daemon down → `[]` with no error, `fetchDiscovery` returns empty provisional without cause.

**Target:**
`GET /api/models/discovery` includes `status: "ok"|"degraded"|"error"`, `ollamaReachable: bool`, `ollamaUrl`, `ollamaError?: string`, `modelsStale: bool`. When unreachable and cached stale exists, still return stale models with `modelsStale:true` + error. Frontend shows banner: "Ollama not reachable at {url} — showing cached inventory from {timestamp}. [Retry]" and `ModelsSettings` empty state CTA "Start Ollama or check Providers → Ollama URL".

**Implementation:**
- Change `ModelDiscovery.installed()` to return `(records, reachable: bool, error: Optional[str])` or keep records but set per-record `stale` and module-level `last_error`.
- Payload adds fields additive (no breaking). Frontend `useFrameworkSettings` stores `discoveryError` and surfaces via banner.
- Guard: never treat `stale` as authoritative for runtime validation (already correct via `ModelService` live check).

- [ ] **Step 1: Failing test — daemon down returns error field**
- [ ] **Step 2: Implement payload fields**
- [ ] **Step 3: UI banner + retry**
- [ ] **Step 4: Verify** `pytest ...`, manual: stop Ollama, refresh Models → banner.

**Dependencies:** 1.1 (discovery payload shape).

---

### Task 2.2 — Search Honest State (disabled vs no results vs failure)

**Objective:** User distinguishes search not configured / failed / empty / grounded success.

**Files:**
- Modify: `novi/search/service.py` (or `novi/runtime/retrieval.py: retrieve_knowledge` + `RetrievalPolicy.resolve`)
- Modify: `novi/webui_server.py:1365-1394` search status endpoint already exists `POST /api/search/test`
- Modify: `novi/runtime/runtime.py:693-722` (system prompt grounding injection, recoveryDecision)
- Modify: `novi/webui/src/hooks/useNoviChat.ts:372-424` (phase/status handling)
- Create Tests: `tests/test_search_honest_state.py` (new)

**Current:** `search.backend=""` silently yields no web evidence; answer still produced appearing grounded.

**Target:**
Retrieval result carries `grounding_status: "grounded"|"not_configured"|"no_results"|"failed"` + `searchError?: string`. When `needs_grounding` but backend `""` → emit `status: "Search not configured — set Brave API key or SearXNG URL in Settings → Connectors."` + system prompt appends `[Search disabled]` not fake grounding. When search fails → `grounding_error` surfaced as `status` trace. UI shows distinct empty state per case.

**Dependencies:** None.

---

### Task 2.3 — Permission Timeout/Expiration Honest Outcome

**Objective:** Pending + expiry visible; timeout produces understandable trace explaining why tool didn't run.

**Files:**
- Modify: `novi/webui_server.py:502-512` (`_ask_permission` timeout emit), `Session._ask_permission` bus event
- Modify: `novi/runtime/tool_executor.py` (permission deny trace)
- Modify: `novi/runtime/trace.py` / `runtime/tracer.py` (trace event for permission timeout)
- Modify: `novi/webui/src/hooks/useNoviChat.ts:531-533, 482-495` (permission state, countdown)
- Modify: `novi/webui/src/components/common/PermissionPrompt.tsx` (countdown UI)
- Create Tests: `tests/test_permission_timeout_honest.py` (new)

**Current:** 120s `wait` silently returns False, no countdown, generic deny.

**Target:**
`permission_request` payload includes `expiresAt` (ISO), `timeoutMs`. Frontend shows `Deny in 1:58` countdown, `Notification` on pending. On timeout → runtime emits `trace` `permission_denied` with `reason: timeout` and tool result explains `Permission for {tool} timed out — approve within 2 min or set Permissions → {tool} to Allow`. Timeout distinct from explicit deny.

**Dependencies:** None.

---

### Task 2.4 — Transcription Honest Failure + Privacy Disclosure

**Objective:** Failures explicit with cause; external service use disclosed; no silent empty text.

**Files:**
- Modify: `novi/webui_server.py:1365-1394` (`POST /api/transcribe`)
- Modify: `novi/webui/src/components/chat/PromptInput.tsx` (mic error handling)
- Modify: `docs/` privacy docs or `GeneralSettings` note (add banner)
- Create Tests: `tests/test_transcribe_honest.py` (new)

**Current:** All errors `return {"text":""}`.

**Target:**
Success → `{"text": "...", "ok": true}`. Failure → `{"text":"", "ok": false, "error": "speech_recognition missing | ffmpeg missing | network | unknown", "detail": str(e)[:500]}` with HTTP 200 but structured error, or 503 for missing deps. UI toast shows error and not silent empty bubble. Docs/setting note: "Transcription currently uses Google Speech API (sends audio to google.com). Offline alternative planned. Disable mic to avoid."

**Dependencies:** None.

---

### Task 2.5 — Brain/Workspace Degraded vs Unavailable vs No Data

**Objective:** User distinguishes feature unavailable/disabled/failed/no-data per optional service.

**Files:**
- Modify: `novi/webui_server.py:1301-1364` (memory/brain/timeline/knowledge endpoints), `1471-1556` (workspace)
- Modify: `novi/memory/knowledge_index.py`, `novi/brain/brain.py` health checks (read-only)
- Modify: `novi/webui/src/components/timeline/TimelinePage.tsx`, `knowledge/KnowledgeOverview.tsx`, `projects/ProjectDetail.tsx` empty/error states
- Create Tests: `tests/test_degraded_services_honest.py` (new)

**Current:** Empty vectors return `[]` indistinguishable from service down.

**Target:**
Each optional endpoint returns `{"status":"ok"|"unavailable"|"disabled", "error"?: string, "data": ...}` or headers. Workspace `GET /api/workspaces/{id}/search` already returns `workspace not attached` 400 — keep. Brain endpoints add `brainAvailable: bool` flag. UI shows distinct Empty ("No knowledge yet — start a conversation") vs Error ("Brain store unavailable — check logs").

**Dependencies:** 2.1 (error field pattern reuse).

---

## Phase 3 — UX Completion and Resource Hygiene

### Task 3.1 — Consistent Loading/Empty/Error/Retry States

**Objective:** Every existing panel handles loading/empty/error/retry consistently.

**Files:**
- Modify: `novi/webui/src/components/jobs/JobsPage.tsx`
- Modify: `novi/webui/src/components/timeline/TimelinePage.tsx`
- Modify: `novi/webui/src/components/search/SearchModal.tsx`
- Modify: `novi/webui/src/components/projects/ProjectsPanel.tsx` (+ `ProjectDetail.tsx`)
- Modify: `novi/webui/src/components/knowledge/KnowledgeOverview.tsx` (already has empty guide → ensure error case too)
- Reuse: `novi/webui/src/components/common/LoadingSkeleton.tsx`, `EmptyState.tsx`, pattern from `ModelsSettings.tsx`/`GeneralSettings.tsx`

**Current:** Jobs/timeline/search/projects: fetch failures ignored, no retry, bare empty lists.

**Target:**
- Loading: `LoadingSkeleton rows={5} compact`
- Empty: `EmptyState` with illustration + CTA (e.g., Jobs empty → "No background jobs — start one from chat /schedule")
- Error: banner with `error.message` + Retry button calling `refresh`/`fetch` prop
- Reuse props: `loading`, `error`, `onRetry` threading from `useNoviChat.fetchTimeline` etc. No new design system.

- [ ] Steps per panel: add `error` state in hook → propagate → render banner → retry calls existing fetch → verify loading→empty→error→retry flows via Vitest + manual.

**Tests:** Vitest interaction tests per panel (mock fetch error → error banner → click Retry → loading).

**Dependencies:** Phase 2 (error shapes defined).

---

### Task 3.2 — Attachment Lifecycle Hygiene

**Objective:** Orphan cleanup, deleted-conversation cleanup, logged thumbnails, documented limits.

**Files:**
- Modify: `novi/webui_server.py:1784-1861` (attachments upload/serve/delete)
- Modify: `novi/webui_server.py:857-882` (conversation delete → prune attachments)
- Create: `novi/services/attachment_gc.py` (simple sweep) or inline in `webui_server.py`
- Create Tests: `tests/test_attachment_gc.py` (new)

**Current:** Attachments in `~/.novi/attachments/` forever; `delete_conversation` does not prune; `PIL` thumb `ImportError: pass` silent.

**Target:**
- On `DELETE /api/conversations/{id}` and `DELETE /api/attachments/{id}` already, also sweep attachments no longer referenced by any `.md` (`@attachments` markers). Add startup/shutdown sweep: `list ReferencedIds` from all `.md` → delete unreferenced files + thumbs older than grace (e.g., 7 days or immediate for dereferenced). Cap not complex: keep 100MB limit already, add doc "Attachments stored locally in ~/.novi/attachments — pruned when conversations deleted or on next restart".
- Log thumbnail failure: `log.warning("thumb failed for %s: %s", att_id, e)`.
- Document in `docs/desktop.md` or tooltip.

- [ ] **Step 1: Failing test — delete conversation prunes its attachments**
- [ ] **Step 2: Implement referenced scan + delete**
```python
def referenced_attachment_ids():
    ids=set()
    for md in CHATS_DIR.glob("*.md"):
        for m in re.finditer(r'"id"\s*:\s*"([^"]+)"', md.read_text()):
            ids.add(m.group(1))
    return ids
```

- [ ] **Step 3: Log thumb failures, add startup sweep**
- [ ] **Step 4: Verify** `pytest tests/test_attachment_gc.py -v`

**Dependencies:** 1.3 (conversation file shape).

---

## Phase 4 — Remove Legacy Conflicts

### Task 4.1 — Eliminate Legacy Dual Configuration Write Path

**Objective:** Single authoritative config path; no conflicting validation-bypass merge.

**Files:**
- Modify: `novi/webui_server.py:1025-1028` (legacy `GET /api/config`), `989-1003` (`_sanitize_config`, `_strip_none`), `novi/webui/src/components/settings/api.ts:9-20` (`fetchConfig`, `saveConfig`)
- Modify: `novi/webui/src/components/settings/SettingsModal.tsx:58-78` (`flushLegacy`, `collectLeafPaths`, `readLeaf`, `legacyPatch`), `81-87` (`updateToolPermission`), `115-118` (`close` flush)
- Modify: `novi/configuration/bootstrap.py:138-147` (legacy `config_roots` group if purely for flushLegacy)
- Create Tests: `tests/test_config_single_path.py` (new) — asserts `PUT /api/config` removed or delegates to framework

**Current:** `PUT /api/config` legacy + `PATCH /api/configuration` framework both write; `SettingsModal` diffs `legacyConfig` vs `initialLegacyRef` and bulk PATCHes `permissions/runtime/agent/mcp/personality/memory/embedding` roots outside framework validation per setting.

**Target:**
- All settings mutated via framework `PATCH /api/configuration` per-setting or `POST /api/configuration/{id}` only. `SettingsModal` removes `flushLegacy`/`saveConfig`/`collectLeafPaths`/`readLeaf`/`legacyPatch`; `MemorySettings`/`AgentSettings`/`PermissionsSettings` already use `framework.set` where appropriate — ensure 100% coverage so legacy page writes disappear.
- Deprecate `GET /api/config` → return 308 to `GET /api/configuration` or keep as read-only compat alias that proxies `snapshot()` (no write). Remove `PUT /api/config` entirely.
- Remove `config_roots` dynamic namespace registration if it existed solely for legacy bulk writes; keep only if other code still writes whole roots intentionally (audit first).

**Implementation:**
- Grep for `"/api/config"` consumers: `webui/src/components/settings/api.ts:9`. Change `fetchConfig` to call `/api/configuration` and map snapshot shape, or keep read alias but remove `saveConfig` usage.
- Delete `saveConfig` export, delete `flushLegacy` + helpers, delete `updateToolPermission` direct `legacyConfig` path → replace with `framework.set("permissions.write_file", mode)` etc.
- Gate removal: run `grep -r "saveConfig\|flushLegacy\|/api/config"` → zero hits outside tests/docs before deleting route.

- [ ] **Step 1: Write test asserting no legacy PUT exists**
- [ ] **Step 2: Migrate each legacy page to framework.set**
- [ ] **Step 3: Remove route + helpers, verify `pytest` + `tsc --noEmit` green**

**Dependencies:** Phase 1 settings audit (which settings stay determines which `legacyPatch` keys matter).

---

### Task 4.2 — Remove Dead/Inert Configuration (not user-visible)

**Objective:** Reduce conf surface without destabilizing.

**Files:**
- Modify: `novi/configuration/builtin.py` (remove any HIDDEN namespace keys proven inert, e.g., stray `llm.*` not read)
- Audit only: `novi/webui/src/services/novi.ts` legacy `agent_config`/`agent_memory`/`agent_tasks` event types — **defer removal** per review (document as post-beta)
- Audit only: `novi/tools/search_pipeline.py` vs `novi/search/*` duplicate — **defer** unless bug proven

**Current:** Some HIDDEN namespace keys persist for compat but shadow.

**Target:**
Delete only those confirmed zero-consumer after audit in Task 1.2's grep. For WS duplicate search pipeline, add `TODO(post-beta)` comment not deletion.

**Tests:** Guard `test_no_dead_config_without_consumer` already.

**Dependencies:** Task 1.2 audit result.

---

## Phase 5 — Verification as Release Gate

### Task 5.1 — Establish Beta Verification Gate (CI + Manual Matrix)

**Objective:** No beta tag unless all automated + manual checks pass.

**Files:**
- Create: `tests/test_beta_verification_gate.py` (smoke) or extend `tests/conftest.py`
- Create/Modify: `docs/BETA_CHECKLIST.md` (new) — manual matrix tracker
- Modify: `.github/workflows/*.yml` or local `scripts/verify_beta.py` (new) — runner

**Automated gate (must all pass):**
```bash
pytest -q
tsc --noEmit  # in novi/webui
npm --prefix novi/webui run build
python -m pytest tests/test_conversation_persistence_atomic.py tests/test_project_conversation_linking.py tests/test_capability_verification.py tests/test_discovery_honest_errors.py -v
```

**Manual first-run matrix (must all pass with evidence screenshots/notes):**

| Scenario | Expected |
|---|---|
| First launch | Clear coherent setup state (no phantom models, hardware bar shows Unknown not 0) |
| Ollama unavailable | Explicit actionable error (status degraded, stale badge, retry) |
| Ollama available, no models | Clear guidance (no models detected + install CTA) |
| New uncached vision model | Not falsely rejected (image succeeds after verify) |
| Known unsupported model | Correctly blocked (unsupported message) |
| Capability verification failure | Never reported as confirmed incompatibility (shows verification_failed + retry) |
| Image attachment | Correct validation and execution |
| Search disabled | Clear configuration guidance (not_configured) |
| Permission ignored (timeout) | Explicit timeout/denial trace |
| App restart | Conversations/projects persist correctly |
| Rapid conversation updates | No corruption or lost state (concurrent test + manual rapid edits) |
| Project reassignment | Relationship remains consistent (canonical check) |
| Attachment deletion | Files eventually cleaned (GC sweep) |
| Runtime failure | User receives actionable explanation (ModelUnavailableError etc.) |

**Implementation:**
- Add `scripts/verify_beta.py` that runs the four commands and prints manual matrix reminder.
- Add `docs/BETA_CHECKLIST.md` with checkboxes per scenario, reviewer sign-off.
- CI: block merge to `main`/`beta` if `pytest` or `tsc` fails; require `verify_beta.py` artifact.

**Dependencies:** All Phase 1–4 done.

---

## Explicitly Deferred (Not in Beta Sprint)

- Remote model registry discovery (HF/Ollama library Explore), model variants/quant picker, per-conversation model pin, background-run persistence hydration beyond Task 3.2, offline transcription provider, Tauri sidecar bundling (`src-tauri` build), multi-user isolation, real-time sync, full storage migration from `.md` to JSON/SQLite (only done if 1.3 proves impossible to harden).
- WebSocket legacy `agent_config`/`agent_memory`/`agent_tasks` removal and duplicate SearXNG search path dedup — documented as post-beta cleanup unless they cause a bug.

## Migration / Backward Compatibility

- Removed settings (`models.agent`, `llm.max_tokens` if deleted): `configuration/migration.py` drops old key silently on next load; no error for users with old `config.toml`.
- Conversation escaping: old `.md` files without escaping remain readable; new writer escapes, reader unescapes — idempotent. No migration script required but add one-time log on first load mismatch.
- Project linking canonicalization: first `GET /api/projects` after upgrade runs one-time reconciliation: for any persisted `conversationIds` without matching `projectId`, backfill `projectId`; then strip `conversationIds` from persisted `projects/index.json` on next save. Old frontend still sees derived `conversationIds` in API response.
- Discovery payload fields (`status`, `ollamaReachable`, `capabilityStates`) additive; old frontend ignores unknown fields.
- `PUT /api/config` removal: no known external consumers; internal `saveConfig` removed; if external tool calls it, returns `410 Gone` with pointer to `PATCH /api/configuration`.

## Execution Order (Recommended)

1 → 1.1 + 1.2 (parallel, disjoint files) → 1.3 → 1.4 (needs 1.3 lock) → 2.1 → 2.2/2.3/2.4/2.5 (parallel) → 3.1/3.2 → 4.1 → 4.2 → 5.1 gate. Each task commits independently; phase gate review after each phase.

---

**Plan complete and saved to `docs/superpowers/plans/2026-09-03-beta-stabilization-sprint.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
