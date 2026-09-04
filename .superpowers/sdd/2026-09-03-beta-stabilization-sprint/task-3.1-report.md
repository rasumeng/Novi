# Task 3.1 — Consistent Loading/Empty/Error/Retry States — Report

**Status:** DONE

**Commits:**
- feat: consistent loading/empty/error/retry states for Jobs/Timeline/Search/Projects (Task 3.1)

**Test summary:**
- `npm --prefix novi/webui run build` — success (tsc no error, vite 1.10s, 1599kB)
- `npm --prefix novi/webui run test -- src/components/jobs/JobsPage.test.tsx src/components/projects/ProjectsPanel.test.tsx src/components/timeline/TimelinePage.test.tsx src/components/knowledge/KnowledgeOverview.test.tsx src/components/search/SearchModal.test.tsx src/hooks/useNoviChat.test.tsx` — 30 passed
  - JobsPage: loading renders LoadingSkeleton rows=5 compact, error banner with error.message + Retry calls onRefresh, empty shows EmptyState CTA, error→retry→loading transition
  - ProjectsPanel: loading skeleton, error banner with Retry, empty EmptyState, error→loading transition
  - TimelinePage: loading skeleton, error amber banner with Retry, empty, error→loading transition (updated existing tests plus 3 new)
  - SearchModal: mock fetch error → error tone EmptyState with Retry → retries fetch and shows result, empty "No matches"
  - KnowledgeOverview: loading skeleton, error amber banner with Retry, empty EmptyState (Brain icon), retry re-enters loading then empty
  - useNoviChat hook: now exposes fetchTimelineEnvelope mock — all 11 existing ownership/notification/research/phase/reasoning tests pass after adding fetchTimelineEnvelope to mock
- Pre-existing `src/components/settings/ModelsSettings.test.tsx` 2 failures remain (Vision-capable badge expects "Vision-capable" string but component renders "Vision" chip; Unknown expects "Unknown" exact but component renders "Unknown — not yet verified") — not introduced by Task 3.1 (verified via git stash that same 2 fail on base main).

**What changed:**
- `novi/webui/src/hooks/useNoviChat.ts:1-82,88-150,738-749,920-932` — Added `timelineLoading`, `projectsLoading`, `projectsError`, `jobsLoading`, `jobsError`, `refreshProjects` callback; `refreshTimeline` now sets loading true/false with finally; `refreshProjects` wraps fetchProjects with loading/error and showError; `handleRefreshBackgroundRuns` now sets jobsLoading/jobsError and clears on background_run_list; `background_run_list` clears jobsLoading/error; exposed via return object; hook now threads loading/error/retry per panel.
- `novi/webui/src/components/jobs/JobsPage.tsx:1-65,78-150` — Added `loading`/`error` props, LoadingSkeleton compact import, error banner (border-err/30 bg-err/5) with error.message + Retry calling onRefresh; render priority loading→error→active/completed→empty; reuse EmptyState with CTA.
- `novi/webui/src/components/timeline/TimelinePage.tsx:1-50` — Added `loading` prop and LoadingSkeleton import; render priority loading→isError (amber banner with Retry)→empty EmptyState→groups; App wires timelineLoading.
- `novi/webui/src/components/search/SearchModal.tsx:1-78` — Added errorMessage state, doSearch helper, Retry button inside error EmptyState action (RefreshCw), queuing via fetch stub; loading uses LoadingSkeleton rows=3 compact already.
- `novi/webui/src/components/projects/ProjectsPanel.tsx:1-180` — Added loading/error/onRetry props, LoadingSkeleton import, error banner (err tone) with Retry, loading guard for search/filter/empty rendering.
- `novi/webui/src/components/knowledge/KnowledgeOverview.tsx:1-70` — Replaced plain loading text with LoadingSkeleton rows=5 compact, replaced raw empty div with EmptyState (Brain icon) + CTA text, added Retry button in error banner that calls load() and shows loading skeleton while retrying.
- `novi/webui/src/App.tsx:91-129` — Wires loading/error/onRetry props from useNoviChat to ProjectsPanel (projectsLoading/projectsError/refreshProjects), JobsPage (jobsLoading/jobsError), TimelinePage (timelineLoading).
- `novi/webui/src/hooks/useNoviChat.test.tsx:48-59` — Added fetchTimelineEnvelope mock to prevent "No fetchTimelineEnvelope export" error after hook change.
- New Vitest tests per panel (4 new files + 3 new cases in TimelinePage.test.tsx) covering distinct states and mock fetch error → error banner → click Retry → loading.

**Concerns / deviations:**
- Jobs via WS: jobsError only surfaces on WS not-connected or future extension; kept minimal as spec allows derived/WS pattern. Loading for jobs is timeout-based (2s fallback) since WS reply is async.
- ModelsSettings pre-existing failures not fixed per scope limit "No scope beyond UX states" — noted as base-branch failures.
- No new design system introduced; reused LoadingSkeleton compact (rows 5) and EmptyState per spec pattern from ModelsSettings/GeneralSettings.

**Remaining / not done:**
- None for 3.1 scope.

**Verification:**
- Every panel now handles loading (LoadingSkeleton compact), empty (EmptyState with CTA), error (banner with error.message + Retry). Threaded via useNoviChat props. build succeeds, targeted vitest 30 passed.
