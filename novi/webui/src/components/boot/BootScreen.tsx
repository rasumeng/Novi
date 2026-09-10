import { BOOT_COPY, type BootState } from '@/hooks/useBoot'

const STEPS = ['conversations', 'projects', 'timeline', 'presets'] as const

export function BootScreen({ state }: { state: BootState }) {
  const copy = state.phase === 'connecting' ? BOOT_COPY.connecting : BOOT_COPY[state.step]

  return (
    <main className="novi-boot" aria-live="polite">
      <section className="novi-boot__card">
        <div className="novi-boot__brand">
          <span className="novi-boot__mark">✦</span>NOVI DESKTOP
        </div>
        <div className="novi-boot__heading">
          <span className="novi-boot__spinner" aria-hidden="true" />
          <h1>Starting Novi</h1>
        </div>
        <p className="novi-boot__status">{copy}</p>
        {state.detail && <p className="text-xs text-base-400">{state.detail}</p>}
        <div className="novi-boot__progress" />
        <ul className="novi-boot__steps">
          {STEPS.map((k) => {
            const idx = STEPS.indexOf(k)
            const isDone = state.loaded > idx
            const isActive = !isDone && state.step === k
            const cls = isDone ? 'done' : isActive ? 'active' : ''
            return (
              <li key={k} className={cls}>
                <i className="novi-boot__dot" aria-hidden="true" />
                <span>{BOOT_COPY[k]}</span>
              </li>
            )
          })}
        </ul>
        {state.phase === 'error' && state.error && (
          <div className="mt-4">
            <p className="text-sm text-red-400">{state.error}</p>
            <button onClick={state.retry} className="mt-2 px-3 py-1 bg-accent rounded">
              Retry
            </button>
          </div>
        )}
        <p className="novi-boot__foot">Everything is running locally on your device.</p>
      </section>
    </main>
  )
}
