import { useCallback, useEffect, useRef, useState } from 'react'
import { NoviClient } from '@/services/novi'
import { fetchConversationsDeduped, fetchProjectsDeduped, fetchTimelineEnvelopeDeduped } from '@/hooks/bootCache'

export type BootPhase = 'connecting' | 'hydrating' | 'ready' | 'error'

export interface BootState {
  phase: BootPhase
  percent: number
  message: string
  error?: string
  retry: () => void
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

export function useBoot(): BootState {
  const [phase, setPhase] = useState<BootPhase>('connecting')
  const [percent, setPercent] = useState(0)
  const [message, setMessage] = useState('Connecting to Novi…')
  const [error, setError] = useState<string | undefined>(undefined)
  const wsRef = useRef<WebSocket | null>(null)
  const retryNonce = useRef(0)
  const [retryKey, setRetryKey] = useState(0)
  const backendReadyRef = useRef(false)
  const dataLoadedRef = useRef(false)

  const retry = useCallback(() => {
    retryNonce.current += 1
    setRetryKey((k) => k + 1)
  }, [])

  useEffect(() => {
    let cancelled = false
    backendReadyRef.current = false
    dataLoadedRef.current = false
    setPhase('connecting')
    setPercent(0)
    setMessage('Connecting to Novi…')
    setError(undefined)

    // Connect to WebSocket for boot progress
    const proto = import.meta.env.DEV ? 'ws' : 'wss'
    const host = import.meta.env.DEV ? 'localhost:8765' : window.location.host
    const ws = new WebSocket(`${proto}://${host}/ws/chat`)
    wsRef.current = ws

    ws.onopen = () => {
      if (cancelled) return
      setMessage('Connected, waiting for backend…')
    }

    ws.onmessage = (e) => {
      if (cancelled) return
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'boot_progress') {
          setPercent(Math.min(100, Math.max(0, msg.percent ?? 0)))
          setMessage(msg.message ?? '')
        } else if (msg.type === 'boot_ready') {
          backendReadyRef.current = true
          setPercent(100)
          setMessage('Ready')
          setPhase('hydrating')
          // Start background data hydration
          hydrateData()
        } else if (msg.type === 'boot_error') {
          setError(msg.error ?? 'Backend failed to start')
          setPhase('error')
        }
      } catch {
        // ignore malformed
      }
    }

    ws.onerror = () => {
      if (cancelled) return
      // WebSocket failed - fall back to polling health endpoint
      pollHealth()
    }

    ws.onclose = () => {
      if (cancelled) return
      if (!backendReadyRef.current) {
        // Connection closed before ready - retry
        setTimeout(() => {
          if (!cancelled) setRetryKey((k) => k + 1)
        }, 2000)
      }
    }

    // Fallback: poll health endpoint if WebSocket doesn't give boot events
    async function pollHealth() {
      let attempts = 0
      while (!cancelled && !backendReadyRef.current && attempts < 60) {
        await new Promise((r) => setTimeout(r, 1000))
        attempts++
        setPercent(Math.min(90, attempts * 1.5))
        setMessage(`Waiting for backend… (${attempts}s)`)
        try {
          const r = await fetch(`${import.meta.env.DEV ? 'http://localhost:8765' : ''}/api/health`, {
            signal: AbortSignal.timeout(2000)
          })
          if (r.ok) {
            const health = await r.json()
            if (health.ready) {
              backendReadyRef.current = true
              setPercent(100)
              setMessage('Ready')
              setPhase('hydrating')
              hydrateData()
              break
            }
          }
        } catch {
          // keep polling
        }
      }
      if (cancelled) return
      if (!backendReadyRef.current) {
        setError('Backend did not become ready in time')
        setPhase('error')
      }
    }

    async function hydrateData() {
      if (cancelled) return
      setMessage('Loading your data…')
      try {
        const [convs, projs, timeline] = await Promise.all([
          fetchConversationsDeduped({ force: true }),
          fetchProjectsDeduped({ force: true }),
          fetchTimelineEnvelopeDeduped({ force: true }).then((e) => e.data),
        ])
        if (cancelled) return
        dataLoadedRef.current = true
        setPhase('ready')
      } catch (e) {
        if (cancelled) return
        console.warn('Boot hydration failed:', e)
        // Non-fatal - app can still work
        dataLoadedRef.current = true
        setPhase('ready')
      }
    }

    // Start polling as backup in case WebSocket doesn't deliver boot events
    const pollBackup = setTimeout(pollHealth, 3000)

    return () => {
      cancelled = true
      clearTimeout(pollBackup)
      ws.close()
    }
  }, [retryKey])

  return { phase, percent, message, error, retry }
}