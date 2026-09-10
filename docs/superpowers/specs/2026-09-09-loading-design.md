# Novi Loading — Honest Blocked Boot (C-blocked) Design

**Date:** 2026-09-09
**Status:** Draft — approved in brainstorm chat
**Variant:** C-blocked (hybrid minimal native + blocked web shell, honest phases, warm copy)
**Copy direction:** Option A warm with "our" framing — Novi as participant.

---

## 1. Context

Previous loading collapsed three real boot stages into a fake 1800ms stepper
(`splash.rs:loading_url`, `index.html` hard-coded boot card,
`App.tsx:BackendLoadingScreen`, `useNoviChat:STARTUP_HYDRATION_MAX_WAIT_MS` +
`if (connection!=='open' || !conversationsHydrated)` gate). Steps advanced on a
timer regardless of `BackendLauncher.wait_until_ready()`, HTTP readiness, or
hydration results. Dishonest and froze the shell.

Stripped in clean-slate pass: `loading_url` deleted, `index.html` emptied,
`App.tsx` gate removed, WS connects immediately. App now renders instantly with
no boot UI — intentional blank slate for this design.

Real boot has ~4 observable domains:
1. Sidecar `wait_until_ready()` polling `http://127.0.0.1:8765` (native, ≤60s)
2. HTTP ready → WebView `navigate`
3. WS `NoviClient` `connecting → open`
4. REST hydration: `fetchConversations`, `fetchProjects`, `fetchTimelineEnvelope`,
   `fetchSettings`/config (presets)

Constraint for new design: **user sees nothing interactive until all four are
ready** — blocked shell, not optimistic. Friendly copy must reveal which domain
is actually loading.

---

## 2. Goals

- Single honest state machine — no `setInterval` progression, every step tied to a
  real promise/event.
- Native minimal splash only while sidecar not listening (prevents
  `ERR_CONNECTION_REFUSED` white flash), no duplication of web copy.
- Web `BootScreen` blocks the entire workspace until WS open + all hydration
  settled, showing warm "our" copy and live step detail.
- Per-domain observability: which of convos/projects/memory/presets is loading
  or failed.
- Retry path per failure without silent empty fallback.
- Keep YAGNI: no boot protocol over a new channel, no progress % fabrication.

## 3. Non-Goals

- Optimistic partial shell (explicitly rejected — must block).
- % progress bar or byte counts — indeterminate only, `loaded/total` is step
  count for copy detail, not a % claim.
- New backend boot-event stream — native phase is binary (listening or not);
  hydration steps are frontend-measured.
- Persisted boot metrics/telemetry.
- Theming or animation polish beyond existing `.novi-boot` language.

---

## 4. Architecture

```
Tauri main.rs ──► native boot_url (about:blank-ish + spinner, no steps)
       │ wait_until_ready() polls /api/health
       ├── Ok  → window.navigate("http://127.0.0.1:8765" | "http://localhost:5173" dev)
       └── Err → splash::error_url("backend didn't respond…")
                            │
                            ▼
                    index.html empty #root
                            │
                    React App.tsx ──► useBoot() hook
                                     phase: connecting|hydrating|ready|error
                                     step: conversations|projects|timeline|presets
                                     gated: if (!ready) return <BootScreen>
                            │
                            ├── NoviClient (WS open)
                            ├── fetchConversations ─┐
                            ├── fetchProjects       ├─► Promise chain with step updates
                            ├── fetchTimeline       │
                            └── fetchSettings       ┘
                                     │
                            BootScreen (blocked, warm copy)
                                     │
                            ready → render Sidebar + Conversation + panels
```

Ownership:
- **Native** owns `starting → listening` only.
- **Web `useBoot`** owns `connecting → hydrating → ready|error` and hydration
  step sequencing.
- **Domain stores** (`useNoviChat`) own fetch implementations; `useBoot`
  consumes their promises, does not duplicate fetch logic.

---

## 5. Components

### 5.1 Native — `novi/webui/src-tauri/src/splash.rs`, `main.rs`

- `splash::boot_url() -> Option<Url>` — minimal HTML: brand + spinner +
  "Starting Novi…" + `background_color`, no steps, no JS stepper.
  Replaces deleted `loading_url`. Keeps `style` const shared with `error_url`.
- `main.rs` `setup`: create window with `boot_url` (not `loading_url`). Spawn
  thread `launcher.wait_until_ready()` then `navigate` to `http://127.0.0.1:port`
  or `http://localhost:5173` dev. Error path unchanged.

### 5.2 Web Boot State — `novi/webui/src/hooks/useBoot.ts` (new)

```ts
type BootStep = 'conversations' | 'projects' | 'timeline' | 'presets'
type BootPhase = 'connecting' | 'hydrating' | 'ready' | 'error'

interface BootState {
  phase: BootPhase
  step: BootStep
  loaded: number        // completed hydration domains
  total: number         // 4
  detail?: string       // e.g. "12 conversations"
  error?: string        // per-step error, preserves step
  retry: () => void
}
```

Behavior:
- `connecting` while `NoviClient` not yet `open` (or before hydration starts).
- `hydrating` advances `step` only on real settlement. Sequential chain
  (not `Promise.all`) so copy can surface "Recalling…" vs "Reopening…" one at a
  time. `allSettled` semantics per step — error does not skip remaining steps
  but sets `error` and allows `retry`.
- `ready` iff `wsOpen && loaded === total && no pending error`.
- `retry` re-runs only the failed domain(s), resets `phase` to `hydrating`.

Consumes injected fetchers to avoid coupling to `useNoviChat` internals. Start
as thin wrapper that calls existing `services/novi.ts` fetchers; later `useNoviChat`
can delegate its mount fetches to it.

### 5.3 BootScreen — `novi/webui/src/components/boot/BootScreen.tsx` (new)

- Full-viewport, blocks shell. Aria-live `polite`.
- Reuses `.novi-boot` visual language (card, brand, spinner, progress indeterminate).
- **Copy map (Option A warm, "our" framing):**

| step | copy | detail template |
|------|------|-----------------|
| conversations | "Recalling our conversations…" | `"${n} conversations"` |
| projects | "Reopening our projects…" | `"${n} projects"` |
| timeline | "Catching up on our memory…" | `"${n} memories"` |
| presets | "Remembering our presets…" | `"settings ready"` |

- `connecting` pre-step copy: "Waking up our workspace…"
- Renders `step` label + spinner + indeterminate bar + `loaded/total` subtle.
- Error variant: shows per-step error + "Retry" button wired to `boot.retry()`.
- No fake auto-advance, no % number.

### 5.4 Integration — `App.tsx`, `useNoviChat.ts`

- `App.tsx`: `const boot = useBoot()` at top; `if (boot.phase !== 'ready') return <BootScreen state={boot} />` replaces old `BackendLoadingScreen` gate. `historyReady`/popstate guard simplified — no boot-time pushState dance; the initial push happens after `ready`.
- `useNoviChat.ts`: `conversationsHydrated` retained as compat but no longer a gate. Hydration fetches moved behind `useBoot` or called through it — avoid double fetch. `NoviClient` connect becomes owned by `useBoot` or shared ref; `useNoviChat` subscribes to `onConnectionChange` for runtime `connection` state.
- `index.html`: stays empty `#root` — no hard-coded boot card (native + web own it).

---

## 6. Data Flow & Sequence

```
App mount
  └─ useBoot init { phase: 'connecting', step: 'conversations', loaded: 0, total: 4 }
     ├─ NoviClient.connect() ──► on open → phase may advance to hydrating
     ├─ await fetchConversations → step=conversations done, detail="n conversations", loaded=1, phase=hydrating
     ├─ await fetchProjects      → step=projects done, detail, loaded=2
     ├─ await fetchTimeline      → step=timeline done, detail, loaded=3
     └─ await fetchSettings      → step=presets done, loaded=4 → if wsOpen then phase=ready else stay hydrating
                                         │
                                    App renders shell
```

Parallel WS + sequential REST gives visibility without adding latency
(sequence is copy-driven; total time ≈ max(ws, sum rest) but steps still surface
visibly because chain awaits each). If parallel is preferred for speed, keep
`Promise.allSettled` but surface the last-settled step as current copy — tradeoff
noted, default to sequential for workshop clarity.

---

## 7. Error Handling

- Native: `wait_until_ready` timeout (60s) → `error_url` with port/process hint.
- Web per-step: `allSettled` — any rejection sets `BootState.error` tied to that
  `step`, `BootScreen` shows error + Retry. `retry` re-invokes only failed fetch.
- WS failure during boot: stay `connecting` with "Waking up our workspace…"
  + subtle "still trying…" after 5s, no silent fallback to empty shell.
- No `STARTUP_HYDRATION_MAX_WAIT_MS` fallback that shows empty landing page —
  explicit blocked + retry replaces it.

---

## 8. Testing

- Unit: `useBoot` — mock fetchers + mock `NoviClient`, assert phase/step
  transitions on resolve/reject, `retry` only re-runs failed.
- Component: `BootScreen` — renders correct warm copy per `step`, shows
  `detail`, retry button calls `retry`.
- Integration: `App.tsx` — gate blocks shell until `ready`, shell renders after.
- Tauri: manual — cold start with backend delay, verify `boot_url` shows then
  navigates, no white flash.

---

## 9. Risks & Mitigations

- Sequential hydration adds ~tens of ms vs parallel — acceptable for visibility;
  can switch to parallel-allSettled if measured regression.
- Double fetch if `useNoviChat` and `useBoot` both call `fetchConversations` —
  have `useBoot` own the fetches and `useNoviChat` consume the resolved values
  via shared store or prop injection.
- `about:blank` vs `boot_url` choice for native — `boot_url` preferred to keep
  branded spinner pre-navigate; verify `Url::from_file_path` still works for it.

---

## 10. Open Workshop Items

- Final warm copy wording locked as "our" — confirm no "your" remains.
- Whether `presets` step should be separate or folded into `timeline` — currently 4,
  could be 3+1.
- Detail strings: show counts ("12 conversations") or keep generic.

---

## 11. File Touch List

- `novi/webui/src-tauri/src/splash.rs` — add `boot_url()`
- `novi/webui/src-tauri/src/main.rs` — use `boot_url`
- `novi/webui/src/hooks/useBoot.ts` — new
- `novi/webui/src/components/boot/BootScreen.tsx` — new
- `novi/webui/src/App.tsx` — consume `useBoot`, gate shell
- `novi/webui/src/hooks/useNoviChat.ts` — delegate hydration/WS to `useBoot`
- `novi/webui/index.html` — no change (empty stays)

---

## 12. Success Criteria

- Cold start: native spinner → web BootScreen shows friendly step copy that
  advances only on real data, never on timer.
- Shell is fully blocked until `ready` — no sidebar/conversation interaction early.
- Which domain is loading is visible at all times; per-step error + retry works.
- Build passes (`npm run build`), no `loading_url` or `BackendLoadingScreen` refs.
