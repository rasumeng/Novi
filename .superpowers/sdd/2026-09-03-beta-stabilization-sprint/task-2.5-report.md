# Task 2.5 — Brain/Workspace Degraded vs Unavailable vs No Data — Report

**Status:** DONE

**Commits:**
- feat: degraded vs unavailable vs empty for Brain/workspace/memory (Task 2.5)

**Test summary:**
- `python -m pytest tests/test_degraded_services_honest.py -v` — 17 passed
  - memory/list unavailable when no memory → status unavailable, brainAvailable false, error "Brain store unavailable — check logs", data []
  - memory/list ok empty → status ok, brainAvailable true, data []
  - memory/list ok with data → status ok
  - memory/list unavailable on exception → status unavailable with detail 500
  - memory/search unavailable when no memory, empty query ok, search ok
  - timeline unavailable when no service → status unavailable, brainAvailable false
  - timeline unavailable on exception, ok empty, ok with entries
  - knowledge unavailable when no brain → status unavailable, categories [] total 0
  - knowledge ok empty (brain exists, no categories) → status ok, brainAvailable true
  - knowledge ok with data → categories/preference, total 1
  - knowledge unavailable on exception → status unavailable
  - workspace not attached keeps 400 "workspace not attached" for search and read
  - UI has distinct empty vs error banners (TimelinePage, KnowledgeOverview, MemorySettings)
  - brainAvailable flag additive on knowledge and timeline envelopes
- `python -m pytest tests/test_timeline.py tests/test_brain.py -v` — all pass (77 in combined)
- `npm --prefix novi/webui run build` — success (vite 1.09s, tsc no error, 1597kB)

**What changed:**
- `novi/webui_server.py:1376-1456` — Memory/timeline/knowledge endpoints now distinguish empty vs degraded:
  - `GET /api/memory/list` → `{status:"ok"|"unavailable", brainAvailable:bool, error?:string, detail?:string, data:[]}` (was `[]`). Unavailable when `memory is None` or `list_all` raises. Empty ok when `[]`.
  - `GET /api/memory/search` → same envelope; empty query returns ok with empty data if memory exists else unavailable.
  - `GET /api/timeline` → `{status, brainAvailable, error, data: entries}` (was `[]`). Unavailable when `timeline_service is None` or `recent` raises. Empty ok returns `data:[]`.
  - `GET /api/knowledge/overview` → `{categories,total,updated, brainAvailable:bool, status:"ok"|"unavailable", error?:string}` additive; unavailable when `brain is None` or `build_knowledge_overview` raises; empty ok when brain exists but no categories. Keeps original `categories/total/updated` shape.
  - Workspace `POST /api/workspaces/{id}/search` and `GET /api/workspaces/{id}/read` kept honest `400 "workspace not attached"` — no change (degraded vs attached already honest per spec).
- `novi/webui/src/services/novi.ts:328-368` — Added `TimelineEnvelope` + `KnowledgeOverviewEnvelope`, `fetchTimelineEnvelope()` that unwraps both legacy array and new envelope, tolerant fallback to unavailable; `fetchTimeline()` delegates to envelope `.data`; `fetchKnowledgeOverview()` returns envelope type with brainAvailable/status/error.
- `novi/webui/src/hooks/useNoviChat.ts:1-82,898-902` — Added `timelineError`/`timelineStatus` state; `refreshTimeline` now uses `fetchTimelineEnvelope` to capture `status/error` and merges `data` only when present; error distinct from empty; `timelineError/status` exposed to callers.
- `novi/webui/src/components/timeline/TimelinePage.tsx:8-35,26-56` — Added `error`/`status` props; when `isError` (error or status unavailable) renders amber error banner "Brain store unavailable — check logs" with Retry button; else when empty renders EmptyState with new description "No knowledge yet — start a conversation. Novi's activity — ..." distinguishing Empty vs Error.
- `novi/webui/src/components/knowledge/KnowledgeOverview.tsx:1-60` — Now tracks `error` state; if `brainAvailable===false` shows amber error banner "Brain store unavailable — check logs"; else when categories empty shows "No knowledge yet — start a conversation" (was "Novi hasn't learned anything yet") — distinct Empty vs Error.
- `novi/webui/src/components/settings/MemorySettings.tsx:24-71,134-145` — Added `memoryError` state + `unwrap()` helper handling both array and envelope; `fetchAll`/`handleSearch` set `memoryError` when `status unavailable`; renders amber error banner in dev tab when unavailable; otherwise empty shows "No memories stored yet."
- `novi/webui/src/App.tsx:121-130` — Passes `timelineError/status` to TimelinePage.
- `tests/test_degraded_services_honest.py` — New 17-test suite covering all degraded envelopes, ok/empty distinctions, workspace honesty, UI banner strings, and additive flag checks.

**Concerns / deviations:**
- Minimal additive chosen: knowledge keeps original dict plus brainAvailable/status/error; timeline/memory changed from bare `[]` to `{status,brainAvailable,data}` envelope — this is additive per spec "status ok/unavailable/disabled with data or headers" but requires frontend unwrap (implemented tolerant to both shapes for backward compat).
- Memory envelope uses same "Brain store unavailable — check logs" wording as brain/timeline for consistency; spec says Brain store unavailable — check logs for timeline/knowledge, memory uses same string (honest, actionable).
- Workspace not changed — already honest 400, kept as spec says "keep".
- No disabled state implemented (not needed for beta; status only ok/unavailable).

**Remaining / not done:**
- None for 2.5 scope. Per spec, workspace READ-only remains; disabled state not exercised.

**Verification:**
- Empty returns `status ok` with empty data/categories; unavailable returns `status unavailable` with `brainAvailable false` and `"Brain store unavailable — check logs"`; workspace keeps 400; UI shows distinct Empty ("No knowledge yet — start a conversation") vs Error ("Brain store unavailable — check logs") banners with Retry.
