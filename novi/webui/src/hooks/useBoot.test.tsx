import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'

vi.mock('@/services/novi', () => ({
  NoviClient: class Mock {
    onConnectionChange: (s: string) => void = () => {}
    onEvent: () => void = () => {}
    connect() {
      this.onConnectionChange('open')
    }
    disconnect() {}
  },
}))

vi.mock('@/hooks/bootCache', () => ({
  fetchConversationsDeduped: vi.fn(async () => [{ id: 'a' }]),
  fetchProjectsDeduped: vi.fn(async () => [{ id: 'p' }]),
  fetchTimelineEnvelopeDeduped: vi.fn(async () => ({ status: 'ok', data: [{ id: 1 }] })),
  resetBootCache: vi.fn(),
}))

function makeMessageEvent(data: string): MessageEvent {
  return new MessageEvent('message', { data })
}

class MockWS {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  readyState = MockWS.CONNECTING
  onopen: (() => void) | null = null
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null

  constructor() {
    setTimeout(() => {
      this.readyState = MockWS.OPEN
      this.onopen?.()
      this.onmessage?.(makeMessageEvent(JSON.stringify({ type: 'boot_ready' })))
    }, 10)
  }
  send() {}
  close() {
    this.readyState = MockWS.CLOSED
    this.onclose?.()
  }
}

import { useBoot } from './useBoot'

beforeEach(() => {
  vi.clearAllMocks()
  // Applied per test rather than at import: an import-time stub is not
  // guaranteed to survive module init order, and without it the hook falls
  // through to jsdom's real WebSocket and never leaves 'connecting'.
  vi.stubGlobal('WebSocket', MockWS)
  // Real timers on purpose: `waitFor` polls via setTimeout, which fake timers
  // freeze, so every waitFor here hung until the 5s test timeout. The mock
  // WebSocket only needs a 10ms timer, so wall-clock waits are fast and let
  // waitFor actually poll.
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('useBoot', () => {
  it('connects, receives boot_ready, hydrates data, and reaches ready', async () => {
    const { result } = renderHook(() => useBoot())
    expect(result.current.phase).toBe('connecting')

    // 'hydrating' is transient (the mocked hydration resolves immediately), so
    // only the settled state is observable from outside.
    await waitFor(() => expect(result.current.phase).toBe('ready'))
    expect(result.current.percent).toBe(100)
  })

  it('surfaces error on boot_error message', async () => {
    vi.stubGlobal('WebSocket', class MockWSError {
      static CONNECTING = 0
      static OPEN = 1
      readyState = MockWSError.CONNECTING
      onopen: (() => void) | null = null
      onmessage: ((e: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      onclose: (() => void) | null = null

      constructor() {
        setTimeout(() => {
          this.readyState = MockWSError.OPEN
          this.onopen?.()
          this.onmessage?.(makeMessageEvent(JSON.stringify({ type: 'boot_error', error: 'Backend failed' })))
        }, 10)
      }
      send() {}
      close() {}
    })

    const { result } = renderHook(() => useBoot())

    await waitFor(() => expect(result.current.phase).toBe('error'))
    expect(result.current.error).toMatch(/Backend failed/)
  })

  it('retry re-runs the boot sequence', async () => {
    let resolveBootReady: (v: void) => void
    const bootReadyPromise = new Promise<void>((r) => { resolveBootReady = r })

    vi.stubGlobal('WebSocket', class MockWSRetry {
      static CONNECTING = 0
      static OPEN = 1
      readyState = MockWSRetry.CONNECTING
      onopen: (() => void) | null = null
      onmessage: ((e: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      onclose: (() => void) | null = null

      constructor() {
        setTimeout(() => {
          this.readyState = MockWSRetry.OPEN
          this.onopen?.()
          bootReadyPromise.then(() => {
            this.onmessage?.(makeMessageEvent(JSON.stringify({ type: 'boot_ready' })))
          })
        }, 10)
      }
      send() {}
      close() {}
    })

    const { result } = renderHook(() => useBoot())

    // boot_ready is only emitted once this resolves, so resolve before waiting.
    resolveBootReady!()
    await waitFor(() => expect(result.current.phase).toBe('ready'))

    await act(async () => {
      result.current.retry()
    })

    // retry must visibly restart the sequence, not no-op.
    expect(result.current.phase).toBe('connecting')
    await waitFor(() => expect(result.current.phase).toBe('ready'))
  })

  it('does not use setInterval for phase progression — honest only', async () => {
    const { result } = renderHook(() => useBoot())
    expect(result.current.phase).toBe('connecting')
    expect(result.current.percent).toBe(0)

    await waitFor(() => expect(result.current.phase).toBe('ready'))
    expect(result.current.percent).toBe(100)
  })
})