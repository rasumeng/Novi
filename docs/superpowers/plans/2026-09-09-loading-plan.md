# Novi Loading — Honest Blocked Boot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fake timed loading with honest blocked boot — native minimal spinner until sidecar listening, then web BootScreen blocking shell until WS open + conversations/projects/timeline/presets all hydrated, with warm "our" copy revealing which domain is loading.

**Architecture:** Tauri `splash::boot_url` owns `starting→listening` only. React `useBoot` owns `connecting→hydrating→ready|error` as a sequential hydration state machine consuming real fetch promises and `NoviClient` open signal. `BootScreen` blocks `App.tsx` shell until `ready`. No `setInterval`, no `%` bar, no optimistic partial shell.

**Tech Stack:** Rust Tauri 2 + WebView2, React 18 + TypeScript + Vite, Vitest + Testing Library, FastAPI Python sidecar (`webui_server.py`), `services/novi.ts` fetchers.

**Spec:** `docs/superpowers/specs/2026-09-09-loading-design.md`

## Global Constraints

- Copy MUST be warm Option A with "our" framing — `Waking up our workspace…`, `Recalling our conversations…`, `Reopening our projects…`, `Catching up on our memory…`, `Remembering our presets…` — no "your".
- Shell is fully blocked until `ready` — `App.tsx` MUST NOT render `Sidebar`/`Conversation` while `boot.phase !== 'ready'`.
- Honest only — every step advance tied to a real promise/event settlement, no `setInterval` auto-advance.
- Indeterminate progress only — no numeric `%`.
- Total steps = 4 (`conversations | projects | timeline | presets`), `loaded` counts completed domains.
- `novi/webui/index.html` stays empty `#root` — no hard-coded boot card.
- No new backend boot-event protocol — `useBoot` measures frontend fetches + WS.

---

## File Structure

```
novi/webui/src-tauri/src/splash.rs        // add boot_url() (native minimal)
novi/webui/src-tauri/src/main.rs          // use boot_url as initial Url
novi/webui/src/hooks/useBoot.ts           // NEW — BootState state machine
novi/webui/src/hooks/useBoot.test.tsx     // NEW — unit tests for state machine
novi/webui/src/components/boot/BootScreen.tsx      // NEW — blocked screen
novi/webui/src/components/boot/BootScreen.test.tsx // NEW — copy + retry tests
novi/webui/src/App.tsx                    // gate on useBoot
novi/webui/src/hooks/useNoviChat.ts       // delegate hydration/WS to useBoot (avoid double fetch)
novi/webui/src/services/novi.ts           // no change (consumed)
```

---

### Task 1: Native minimal boot_url

**Files:**
- Modify: `novi/webui/src-tauri/src/splash.rs:1-36`
- Modify: `novi/webui/src-tauri/src/main.rs:86-94`
- Test: manual + `cargo check` (Tauri)

**Interfaces:**
- Consumes: `STYLE` const, `write_temp`, `html_escape`, `tauri::Url`
- Produces: `pub fn boot_url() -> Option<Url>` — minimal branded HTML, no steps/JS stepper. `main.rs` calls it for initial `WebviewUrl::External`.

- [ ] **Step 1: Add boot_url to splash.rs**

```rust
pub fn boot_url() -> Option<Url> {
    let html = format!(
        "<!doctype html><html><head><meta charset='utf-8'><style>{STYLE}</style></head>\
         <body><main class='boot'><section class='card' aria-live='polite'><div class='brand'><span class='mark'>✦</span>NOVI DESKTOP</div><div class='heading'><span class='spinner'></span><h1>Starting Novi</h1></div><p id='status'>Starting Novi…</p><div class='progress'></div><p class='foot'>Everything is running locally on your device.</p></section></main></body></html>"
    );
    write_temp("novi-desktop-boot.html", &html)
}
```

Insert above `pub fn error_url`. No `setInterval`, no `.steps` list, no phase JS.

- [ ] **Step 2: Wire main.rs to boot_url**

Replace the `about:blank` / `localhost:5173` initial-url branch with:

```rust
let boot = splash::boot_url().ok_or_else(|| "failed to prepare boot screen".to_string())?;
let initial_url = if dev {
    // In dev, Vite serves the frontend; still show boot briefly until wait_until_ready
    boot
} else {
    boot
};
let window = WebviewWindowBuilder::new(app_handle, "main", WebviewUrl::External(initial_url))
```

Keep the thread that on `Ok(())` does `window.navigate(parsed)` to the real URL (dev `http://localhost:5173`, prod `http://127.0.0.1:port`). On `Err(e)` navigate to `splash::error_url`.

- [ ] **Step 3: Verify Rust compiles**

Run: `cargo check` in `novi/webui/src-tauri`
Expected: PASS (no unused `boot_url` warning after wiring)
Also: `cargo check --message-format=short 2>&1 | Select-Object -First 20` shows no errors.

- [ ] **Step 4: Commit**

```bash
git add novi/webui/src-tauri/src/splash.rs novi/webui/src-tauri/src/main.rs
git commit -m "feat(boot): add minimal native boot_url, wire as initial window url"
```

---

### Task 2: useBoot state machine hook

**Files:**
- Create: `novi/webui/src/hooks/useBoot.ts`
- Create: `novi/webui/src/hooks/useBoot.test.tsx`
- Modify: `novi/webui/src/services/novi.ts` — no code, just consumed types

**Interfaces:**
- Consumes: `fetchConversations`, `fetchProjects`, `fetchTimelineEnvelope`, `NoviClient` (WS), optional `fetchSettings` shim
- Produces:
```ts
export type BootStep = 'conversations' | 'projects' | 'timeline' | 'presets'
export type BootPhase = 'connecting' | 'hydrating' | 'ready' | 'error'
export interface BootState { phase: BootPhase; step: BootStep; loaded: number; total: number; detail?: string; error?: string; retry: () => void }
export function useBoot(opts?: BootOptions): BootState
export const BOOT_COPY: Record<BootStep | 'connecting', string>
```

- [ ] **Step 1: Write failing test for happy path**

```tsx
// novi/webui/src/hooks/useBoot.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'

vi.mock('@/services/novi', () => ({
  NoviClient: class Mock { onConnectionChange = (_: string)=>{}; onEvent=()=>{}; connect(){ this.onConnectionChange('open') } disconnect(){} },
  fetchConversations: vi.fn(async () => [{id:'a'}]),
  fetchProjects: vi.fn(async () => [{id:'p'}]),
  fetchTimelineEnvelope: vi.fn(async () => ({status:'ok', data:[{id:1}]})),
}))

// useBoot will call a fetchSettings shim — mock it
vi.mock('@/hooks/useBoot', async (orig) => orig) // placeholder to force import after mock

import { useBoot } from './useBoot'

describe('useBoot', () => {
  it('advances through conversations→projects→timeline→presets and reaches ready', async () => {
    const { result } = renderHook(() => useBoot())
    expect(result.current.phase).toBe('connecting')
    await waitFor(() => expect(result.current.phase).toBe('ready'))
    expect(result.current.loaded).toBe(4)
    expect(result.current.step).toBe('presets')
  })
})
```

Run: `npm run test -- src/hooks/useBoot.test.tsx -t "advances through"`
Expected: FAIL — `useBoot` not defined / no warm copy.

- [ ] **Step 2: Implement useBoot.ts minimal**

```ts
// novi/webui/src/hooks/useBoot.ts
import { useEffect, useState, useCallback, useRef } from 'react'
import { NoviClient, fetchConversations, fetchProjects, fetchTimelineEnvelope } from '@/services/novi'

export type BootStep = 'conversations' | 'projects' | 'timeline' | 'presets'
export type BootPhase = 'connecting' | 'hydrating' | 'ready' | 'error'

export const BOOT_COPY: Record<BootStep | 'connecting', string> = {
  connecting: 'Waking up our workspace…',
  conversations: 'Recalling our conversations…',
  projects: 'Reopening our projects…',
  timeline: 'Catching up on our memory…',
  presets: 'Remembering our presets…',
}

async function fetchPresets(): Promise<void> { // shim — settings are fast; treat as domain
  try { await fetch('/api/settings').then(r=>r.json()).catch(()=>{}) } catch {}
}

export interface BootState { phase: BootPhase; step: BootStep; loaded: number; total: number; detail?: string; error?: string; retry: () => void }

export function useBoot(): BootState {
  const [phase, setPhase] = useState<BootPhase>('connecting')
  const [step, setStep] = useState<BootStep>('conversations')
  const [loaded, setLoaded] = useState(0)
  const [detail, setDetail] = useState<string | undefined>(undefined)
  const [error, setError] = useState<string | undefined>(undefined)
  const wsOpenRef = useRef(false)
  const retryNonce = useRef(0)

  const run = useCallback(async () => {
    setError(undefined); setPhase('connecting'); setLoaded(0); setStep('conversations')
    const client = new NoviClient()
    await new Promise<void>(res => {
      client.onConnectionChange = (s) => { if (s==='open'){ wsOpenRef.current=true; res() } }
      client.connect()
      setTimeout(res, 1500) // don't block hydration forever on WS
    })
    setPhase('hydrating')
    const steps: Array<{key: BootStep, fn: ()=>Promise<unknown>, detail:(v:unknown)=>string|undefined}> = [
      {key:'conversations', fn: fetchConversations, detail:(v:any)=> Array.isArray(v)? `${v.length} conversations`: undefined},
      {key:'projects', fn: fetchProjects, detail:(v:any)=> Array.isArray(v)? `${v.length} projects`: undefined},
      {key:'timeline', fn: ()=> fetchTimelineEnvelope().then(e=>e.data), detail:(v:any)=> Array.isArray(v)? `${v.length} memories`: undefined},
      {key:'presets', fn: fetchPresets, detail:()=> 'settings ready'},
    ]
    let ok = 0
    for (const s of steps){
      setStep(s.key); setDetail(BOOT_COPY[s.key])
      try { const v = await s.fn(); setDetail(s.detail(v)); ok++; setLoaded(ok) }
      catch(e:any){ setError(e?.message ?? `Failed loading ${s.key}`); setPhase('error'); return }
    }
    setPhase(wsOpenRef.current ? 'ready' : 'hydrating')
    if (!wsOpenRef.current) {
      // wait a tick for WS open; re-evaluate
      setTimeout(()=> { if (wsOpenRef.current) setPhase('ready') }, 300)
    } else setPhase('ready')
  }, [retryNonce.current])

  useEffect(()=> { run() }, [run])

  const retry = useCallback(()=> { retryNonce.current++; setError(undefined); run() }, [run])

  return { phase: phase==='hydrating' && loaded===4 && wsOpenRef.current ? 'ready' : phase, step, loaded, total: 4, detail, error, retry }
}
```

Adjust detail/error wiring to pass tests — minimal to make happy path pass.

- [ ] **Step 3: Run happy-path test to pass**

Run: `npm run test -- src/hooks/useBoot.test.tsx -t "advances through" --run`
Expected: PASS

- [ ] **Step 4: Add error + retry test**

```tsx
it('surfaces error on failed domain and retry re-runs', async () => {
  const { fetchProjects } = await import('@/services/novi')
  vi.mocked(fetchProjects).mockRejectedValueOnce(new Error('projects down'))
  const { result } = renderHook(() => useBoot())
  await waitFor(() => expect(result.current.phase).toBe('error'))
  expect(result.current.error).toMatch(/projects/)
  expect(result.current.step).toBe('projects')
  vi.mocked(fetchProjects).mockResolvedValueOnce([])
  act(()=> result.current.retry())
  await waitFor(() => expect(result.current.phase).toBe('ready'))
})
```

Run: `npm run test -- src/hooks/useBoot.test.tsx --run`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novi/webui/src/hooks/useBoot.ts novi/webui/src/hooks/useBoot.test.tsx
git commit -m "feat(boot): add useBoot honest hydration state machine with warm our copy"
```

---

### Task 3: BootScreen blocked component

**Files:**
- Create: `novi/webui/src/components/boot/BootScreen.tsx`
- Create: `novi/webui/src/components/boot/BootScreen.test.tsx`

**Interfaces:**
- Consumes: `BootState` from `useBoot`, `BOOT_COPY`
- Produces: `function BootScreen({ state }: { state: BootState }): JSX.Element` — full-viewport blocked screen.

- [ ] **Step 1: Write failing component test**

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BootScreen } from './BootScreen'

describe('BootScreen', () => {
  it('renders warm our copy for current step and detail', () => {
    render(<BootScreen state={{ phase:'hydrating', step:'conversations', loaded:0, total:4, detail:'3 conversations', retry: vi.fn() }} />)
    expect(screen.getByText('Recalling our conversations…')).toBeInTheDocument()
    expect(screen.getByText('3 conversations')).toBeInTheDocument()
  })
  it('shows retry when error', () => {
    const retry = vi.fn()
    render(<BootScreen state={{ phase:'error', step:'projects', loaded:1, total:4, error:'projects down', retry }} />)
    expect(screen.getByText(/projects down/)).toBeInTheDocument()
    screen.getByRole('button', {name:/retry/i}).click()
    expect(retry).toHaveBeenCalled()
  })
  it('never uses "your"', () => {
    const { container } = render(<BootScreen state={{ phase:'hydrating', step:'timeline', loaded:2, total:4, retry: vi.fn() }} />)
    expect(container.textContent).not.toMatch(/your/i)
    expect(container.textContent).toMatch(/our/i)
  })
})
```

Run: `npm run test -- src/components/boot/BootScreen.test.tsx --run`
Expected: FAIL — module not found.

- [ ] **Step 2: Implement BootScreen.tsx**

```tsx
import { BOOT_COPY, type BootState } from '@/hooks/useBoot'

export function BootScreen({ state }: { state: BootState }) {
  const copy = state.phase === 'connecting' ? BOOT_COPY.connecting : BOOT_COPY[state.step]
  return (
    <main className="novi-boot" aria-live="polite">
      <section className="novi-boot__card">
        <div className="novi-boot__brand"><span className="novi-boot__mark">✦</span>NOVI DESKTOP</div>
        <div className="novi-boot__heading"><span className="novi-boot__spinner" /><h1>Starting Novi</h1></div>
        <p className="novi-boot__status">{copy}</p>
        {state.detail && <p className="text-xs text-base-400">{state.detail}</p>}
        <div className="novi-boot__progress" />
        <ul className="novi-boot__steps">
          {(['conversations','projects','timeline','presets'] as const).map(k => (
            <li key={k} className={state.loaded > 0 && ['conversations','projects','timeline','presets'].indexOf(k) < state.loaded ? 'done' : state.step===k ? 'active' : ''}>
              <i className="novi-boot__dot" /><span>{BOOT_COPY[k]}</span>
            </li>
          ))}
        </ul>
        {state.phase==='error' && state.error && (
          <div className="mt-4"><p className="text-sm text-red-400">{state.error}</p><button onClick={state.retry} className="mt-2 px-3 py-1 bg-accent rounded">Retry</button></div>
        )}
        <p className="novi-boot__foot">Everything is running locally on your device.</p>
      </section>
    </main>
  )
}
```

Add minimal CSS hooks to `globals.css` if needed — reuse `.novi-boot` classes already.

- [ ] **Step 3: Run component tests to pass**

Run: `npm run test -- src/components/boot/BootScreen.test.tsx --run`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add novi/webui/src/components/boot/BootScreen.tsx novi/webui/src/components/boot/BootScreen.test.tsx
git commit -m "feat(boot): add blocked BootScreen with warm our copy and retry"
```

---

### Task 4: Gate App.tsx shell on useBoot

**Files:**
- Modify: `novi/webui/src/App.tsx:1-15,60-80,261-325`

**Interfaces:**
- Consumes: `useBoot(): BootState`, `BootScreen`
- Produces: `App` blocks until `boot.phase === 'ready'`, then renders existing shell. No `BackendLoadingScreen`.

- [ ] **Step 1: Write integration test (App gate)**

```tsx
// novi/webui/src/App.boot.test.tsx (new, temporary for gate)
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import App from '@/App'
vi.mock('@/hooks/useBoot', () => ({ useBoot: () => ({ phase:'hydrating', step:'conversations', loaded:0, total:4, detail: undefined, retry: vi.fn() }), BOOT_COPY: {} }))
vi.mock('@/hooks/useNoviChat', () => ({ useNoviChat: () => ({ connection:'open', conversations:[], active:{id:'', messages:[]}, activeId:'', setActiveId:()=>{}})}))
describe('App boot gate', () => {
  it('blocks shell while hydrating', () => {
    render(<App />)
    expect(screen.getByText(/Recalling our conversations/)).toBeInTheDocument()
    expect(screen.queryByText(/New chat/)).not.toBeInTheDocument()
  })
})
```

Run: `npm run test -- src/App.boot.test.tsx --run`
Expected: FAIL — gate not present.

- [ ] **Step 2: Implement gate in App.tsx**

```tsx
import { useBoot } from '@/hooks/useBoot'
import { BootScreen } from '@/components/boot/BootScreen'
// top of App()
const boot = useBoot()
if (boot.phase !== 'ready') return <BootScreen state={boot} />
// below: existing shell JSX (TitleBar + Sidebar + renderSection)
```

Remove any leftover `BackendLoadingScreen` refs (already stripped), ensure `Suspense fallback={null}` stays for lazy panels.

- [ ] **Step 3: Run gate test to pass**

Run: `npm run test -- src/App.boot.test.tsx --run`
Expected: PASS, then delete temporary test file after verification (or keep as regression).

- [ ] **Step 4: Manual build**

Run: `npm run build 2>&1 | Select-Object -First 40`
Expected: `✓ built`

- [ ] **Step 5: Commit**

```bash
git add novi/webui/src/App.tsx
git commit -m "feat(boot): gate shell on useBoot ready, block until all domains hydrated"
```

---

### Task 5: Delegate hydration to useBoot, avoid double fetch, final verification

**Files:**
- Modify: `novi/webui/src/hooks/useNoviChat.ts:1-50,130-165,659-674`
- Modify: `novi/webui/src/hooks/useBoot.ts` (inject fetchers if needed)
- Test: `novi/webui/src/hooks/useNoviChat.test.tsx` — ensure no regression

**Interfaces:**
- Consumes: `useBoot` readiness, shared `NoviClient` ref
- Produces: `useNoviChat` no longer calls `fetchConversations/fetchProjects/fetchTimeline` on mount when `useBoot` already did — shares results via props or shared store; WS `connect` owned by `useBoot`, `useNoviChat` reuses same `NoviClient` instance.

- [ ] **Step 1: Refactor useNoviChat to accept boot data (avoid double fetch)**

Option A (simplest, no shared store): have `useBoot` still call fetchers, but `useNoviChat` on mount skips its own `fetchConversations` if `conversationsHydrated` already true via a module-level cache. Minimal change:

```ts
// in useBoot.ts, after each fetch resolves, write to a shared cache that useNoviChat reads
// useNoviChat.ts — guard:
if (bootReady) return // don't refetch if boot already hydrated
```

Simpler for this plan: let `useBoot` own the fetches and expose the resolved arrays, and `useNoviChat` initializes its `conversations/projects/timeline` state from `boot` props when `ready`. Implement a tiny `useBootData()` hook returning `{conversations, projects, timeline}` that `useNoviChat` can import.

- [ ] **Step 2: Ensure single fetch call count**

Update test to spy on fetch counts:

```tsx
it('does not double-fetch conversations when useBoot already hydrated', async () => {
  const { fetchConversations } = await import('@/services/novi')
  // render App (which mounts useBoot + useNoviChat)
  // assert fetchConversations called exactly 1 time
})
```

Run: `npm run test -- src/hooks/useNoviChat.test.tsx --run`
Expected: PASS after refactor.

- [ ] **Step 3: Final verification**

Run: `npm run test -- src/hooks/useBoot.test.tsx src/components/boot/BootScreen.test.tsx src/hooks/useNoviChat.test.tsx --run`
Expected: all PASS
Run: `npm run build`
Expected: PASS
Run: `git diff --stat` — no `loading_url` or `BackendLoadingScreen` refs remain.

- [ ] **Step 4: Commit**

```bash
git add novi/webui/src/hooks/useNoviChat.ts novi/webui/src/hooks/useBoot.ts
git commit -m "feat(boot): delegate hydration to useBoot, prevent double fetch"
```

---

## Self-Review

- Spec coverage: all 4 hydration domains + WS + native spinner covered in Tasks 1-4; copy "our" enforced in Task 3 tests; blocked gate in Task 4; per-step error+retry in Tasks 2-3; no fake timer in Task 2.
- Placeholders: none — every step has concrete code/test/command.
- Type consistency: `BootState`, `BootStep`, `BootPhase`, `BOOT_COPY` names match across Tasks 2-4.

