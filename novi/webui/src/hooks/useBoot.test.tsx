import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'

vi.mock('@/services/novi', () => ({
  NoviClient: class Mock {
    onConnectionChange: (s: string) => void = () => {}
    onEvent: () => void = () => {}
    connect() {
      // honest open signal — resolves WS promise synchronously
      this.onConnectionChange('open')
    }
    disconnect() {}
  },
  fetchConversations: vi.fn(async () => [{ id: 'a' }]),
  fetchProjects: vi.fn(async () => [{ id: 'p' }]),
  fetchTimelineEnvelope: vi.fn(async () => ({ status: 'ok', brainAvailable: true, data: [{ id: 1 }] })),
}))

import { useBoot, BOOT_COPY } from './useBoot'
import { fetchProjects } from '@/services/novi'

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({}) }) as unknown as Response))
})

describe('useBoot', () => {
  it('advances through conversations→projects→timeline→presets and reaches ready', async () => {
    const { result } = renderHook(() => useBoot())
    expect(result.current.phase).toBe('connecting')
    await waitFor(() => expect(result.current.phase).toBe('ready'))
    expect(result.current.loaded).toBe(4)
    expect(result.current.step).toBe('presets')
    expect(result.current.total).toBe(4)
  })

  it('surfaces error on failed domain and retry re-runs', async () => {
    vi.mocked(fetchProjects).mockRejectedValueOnce(new Error('projects down'))
    const { result } = renderHook(() => useBoot())
    await waitFor(() => expect(result.current.phase).toBe('error'))
    expect(result.current.error).toMatch(/projects/)
    expect(result.current.step).toBe('projects')
    vi.mocked(fetchProjects).mockResolvedValueOnce([] as never)
    act(() => result.current.retry())
    await waitFor(() => expect(result.current.phase).toBe('ready'))
  })

  it('warm copy uses our not your', () => {
    const all = Object.values(BOOT_COPY).join(' ')
    expect(all).toMatch(/our/)
    expect(all.toLowerCase()).not.toMatch(/your/)
    expect(BOOT_COPY.connecting).toBe('Waking up our workspace…')
    expect(BOOT_COPY.conversations).toBe('Recalling our conversations…')
    expect(BOOT_COPY.projects).toBe('Reopening our projects…')
    expect(BOOT_COPY.timeline).toBe('Catching up on our memory…')
    expect(BOOT_COPY.presets).toBe('Remembering our presets…')
  })

  it('does not use setInterval for phase progression — honest only', async () => {
    // Indirect: ensure loaded only advances after promises, not on timers
    const { result } = renderHook(() => useBoot())
    // initially not ready, not auto-advancing via interval before fetches resolve
    expect(result.current.loaded).toBe(0)
    await waitFor(() => expect(result.current.phase).toBe('ready'))
    expect(result.current.loaded).toBe(4)
  })
})
