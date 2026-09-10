import { useCallback, useEffect, useRef, useState } from 'react'
import { NoviClient } from '@/services/novi'
import { fetchConversationsDeduped, fetchProjectsDeduped, fetchTimelineEnvelopeDeduped } from '@/hooks/bootCache'

export type BootStep = 'conversations' | 'projects' | 'timeline' | 'presets'
export type BootPhase = 'connecting' | 'hydrating' | 'ready' | 'error'

export const BOOT_COPY: Record<BootStep | 'connecting', string> = {
  connecting: 'Waking up our workspace…',
  conversations: 'Recalling our conversations…',
  projects: 'Reopening our projects…',
  timeline: 'Catching up on our memory…',
  presets: 'Remembering our presets…',
}

async function fetchPresets(): Promise<unknown> {
  try {
    const r = await fetch('/api/settings')
    if (r.ok) {
      try {
        return await r.json()
      } catch {
        return {}
      }
    }
    return {}
  } catch {
    return {}
  }
}

export interface BootState {
  phase: BootPhase
  step: BootStep
  loaded: number
  total: number
  detail?: string
  error?: string
  retry: () => void
}

export function useBoot(): BootState {
  const [phase, setPhase] = useState<BootPhase>('connecting')
  const [step, setStep] = useState<BootStep>('conversations')
  const [loaded, setLoaded] = useState(0)
  const [detail, setDetail] = useState<string | undefined>(undefined)
  const [error, setError] = useState<string | undefined>(undefined)
  const wsOpenRef = useRef(false)
  const retryNonce = useRef(0)
  const [retryKey, setRetryKey] = useState(0)

  const retry = useCallback(() => {
    retryNonce.current += 1
    setRetryKey((k) => k + 1)
  }, [])

  useEffect(() => {
    let cancelled = false
    wsOpenRef.current = false
    setPhase('connecting')
    setStep('conversations')
    setLoaded(0)
    setDetail(undefined)
    setError(undefined)

    const client = new NoviClient()

    const wsPromise = new Promise<void>((resolve) => {
      client.onConnectionChange = (s: string) => {
        if (s === 'open') {
          wsOpenRef.current = true
          resolve()
        }
      }
      try {
        client.connect()
      } catch {
        resolve()
      }
      setTimeout(() => resolve(), 1500)
    })

    ;(async () => {
      await wsPromise
      if (cancelled) return
      setPhase('hydrating')

      const steps: Array<{ key: BootStep; fn: () => Promise<unknown>; detail: (v: unknown) => string | undefined }> = [
        {
          key: 'conversations',
          fn: () => fetchConversationsDeduped({ force: true }),
          detail: (v: unknown) => (Array.isArray(v) ? `${(v as unknown[]).length} conversations` : undefined),
        },
        {
          key: 'projects',
          fn: () => fetchProjectsDeduped({ force: true }),
          detail: (v: unknown) => (Array.isArray(v) ? `${(v as unknown[]).length} projects` : undefined),
        },
        {
          key: 'timeline',
          fn: () => fetchTimelineEnvelopeDeduped({ force: true }).then((e) => e.data),
          detail: (v: unknown) => (Array.isArray(v) ? `${(v as unknown[]).length} memories` : undefined),
        },
        {
          key: 'presets',
          fn: fetchPresets,
          detail: () => 'settings ready',
        },
      ]

      let ok = 0
      for (const s of steps) {
        if (cancelled) return
        setStep(s.key)
        setDetail(BOOT_COPY[s.key])
        try {
          const v = await s.fn()
          if (cancelled) return
          const d = s.detail(v)
          if (d) setDetail(d)
          ok += 1
          setLoaded(ok)
        } catch (e: unknown) {
          if (cancelled) return
          const msg = e instanceof Error ? e.message : `Failed loading ${s.key}`
          setError(msg)
          setPhase('error')
          return
        }
      }

      if (cancelled) return
      if (wsOpenRef.current) {
        setPhase('ready')
      } else {
        setTimeout(() => {
          if (!cancelled && wsOpenRef.current) setPhase('ready')
        }, 300)
      }
    })()

    return () => {
      cancelled = true
      try {
        client.disconnect()
      } catch {
        /* ignore */
      }
    }
  }, [retryKey])

  const effectivePhase: BootPhase = phase === 'hydrating' && loaded === 4 && wsOpenRef.current ? 'ready' : phase

  return { phase: effectivePhase, step, loaded, total: 4, detail, error, retry }
}
