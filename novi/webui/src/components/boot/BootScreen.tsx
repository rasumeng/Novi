import { useEffect, useState } from 'react'
import { NoviMascot } from '@/components/brand/NoviMascot'
import { useBoot } from '@/hooks/useBoot'

export const BOOT_REMARKS = [
  "Hey, I'll be right there.",
  "I guess it's time to wake up.",
  'THE ONE PIECE... THE ONE PIECE IS REALLL!',
  'Just finding my other sock.',
  'Warming up the thinking circuits.',
  'One moment—I am assembling the vibes.',
  'Booting up with dramatic flair.',
  'Coffee would help, but electrons will do.',
  'Stretching my virtual legs.',
  'Almost awake. Probably.',
  'Putting the last few thoughts in order.',
  'Hold that thought—I am on my way.',
  'Checking whether the internet is still there.',
  'Summoning the helpful version of me.',
  'A tiny bit of patience, please.',
  'Dusting off the neural pathways.',
  'Loading wit, wisdom, and questionable jokes.',
  'Getting everything nice and Novi-shaped.',
  'I have not forgotten about us.',
  'Making sure all the buttons know their jobs.',
  'The gears are turning. Very stylishly.',
  'Preparing to look much more awake than I feel.',
  'Nearly there—cue the entrance music.',
  'Doing one last systems wiggle.',
  'Ready in a moment. Scout’s honor.',
] as const

function randomRemarkIndex(currentIndex?: number): number {
  if (currentIndex === undefined) {
    return Math.floor(Math.random() * BOOT_REMARKS.length)
  }
  const offset = 1 + Math.floor(Math.random() * (BOOT_REMARKS.length - 1))
  return (currentIndex + offset) % BOOT_REMARKS.length
}

export function BootScreen({ embedded = false }: { embedded?: boolean }) {
  const boot = useBoot()
  const [remarkIndex, setRemarkIndex] = useState(() => randomRemarkIndex())

  const isError = boot.phase === 'error'

  useEffect(() => {
    if (isError) return
    const interval = window.setInterval(() => {
      setRemarkIndex((current) => randomRemarkIndex(current))
    }, 10_000)
    return () => window.clearInterval(interval)
  }, [isError])

  return (
    <main
      className={embedded ? 'novi-boot novi-boot--embedded' : 'novi-boot'}
      aria-live="polite"
    >
      <section className="novi-boot__card">
        <div className="novi-boot__brand">
          <NoviMascot size={96} alt="Novi" />
        </div>

        <p className="novi-boot__status" style={{ minHeight: '1.5em' }}>
          {isError ? 'Failed to start' : BOOT_REMARKS[remarkIndex]}
        </p>

        <div className="novi-boot__progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-busy="true">
          <div className="novi-boot__progress-fill" />
        </div>

        {isError && boot.error && (
          <div className="novi-boot__error">
            <p>{boot.error}</p>
            <button onClick={boot.retry} className="novi-boot__retry">
              Try again
            </button>
          </div>
        )}

        <p className="novi-boot__foot">Everything runs locally on your device.</p>
      </section>
    </main>
  )
}
