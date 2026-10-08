import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'

const check = vi.fn()
const relaunch = vi.fn()
let tauri = true

vi.mock('@tauri-apps/api/core', () => ({
  isTauri: () => tauri,
}))

vi.mock('@tauri-apps/plugin-updater', () => ({
  check: (...args: unknown[]) => check(...args),
}))

vi.mock('@tauri-apps/plugin-process', () => ({
  relaunch: (...args: unknown[]) => relaunch(...args),
}))

import { useUpdateCheck } from './useUpdateCheck'

const KEY = 'novi_update_last_check'
const DAY = 24 * 60 * 60 * 1000

function fakeUpdate(version: string) {
  return {
    version,
    currentVersion: '0.3.0-beta.1',
    downloadAndInstall: vi.fn(async () => {}),
    close: vi.fn(async () => {}),
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  tauri = true
})

afterEach(() => {
  localStorage.clear()
})

describe('useUpdateCheck', () => {
  it('surfaces an available update and records the check timestamp', async () => {
    check.mockResolvedValue(fakeUpdate('0.4.0'))

    const { result } = renderHook(() => useUpdateCheck())

    await waitFor(() => expect(result.current.update?.version).toBe('0.4.0'))
    // The throttle must be written even on success, or the check repeats on
    // every launch.
    expect(Number(localStorage.getItem(KEY))).toBeGreaterThan(0)
  })

  it('does nothing outside Tauri (npm run dev in a browser)', () => {
    tauri = false

    const { result } = renderHook(() => useUpdateCheck())

    expect(result.current.update).toBeNull()
    expect(check).not.toHaveBeenCalled()
  })

  it('stays silent and throttles when the check throws (offline / 404)', async () => {
    check.mockRejectedValue(new Error('network unreachable'))

    const { result } = renderHook(() => useUpdateCheck())

    await waitFor(() => expect(localStorage.getItem(KEY)).not.toBeNull())
    expect(result.current.update).toBeNull()
  })

  it('skips the check when one ran less than 24h ago', () => {
    localStorage.setItem(KEY, String(Date.now() - 60_000))

    const { result } = renderHook(() => useUpdateCheck())

    expect(check).not.toHaveBeenCalled()
    expect(result.current.update).toBeNull()
  })

  it('checks again once the 24h window has elapsed', async () => {
    localStorage.setItem(KEY, String(Date.now() - DAY - 1000))
    check.mockResolvedValue(fakeUpdate('0.4.0'))

    const { result } = renderHook(() => useUpdateCheck())

    await waitFor(() => expect(check).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(result.current.update?.version).toBe('0.4.0'))
  })

  it('install() downloads, then relaunches', async () => {
    const update = fakeUpdate('0.4.0')
    check.mockResolvedValue(update)

    const { result } = renderHook(() => useUpdateCheck())
    await waitFor(() => expect(result.current.update).not.toBeNull())

    await act(async () => {
      await result.current.install()
    })

    expect(update.downloadAndInstall).toHaveBeenCalledTimes(1)
    expect(relaunch).toHaveBeenCalledTimes(1)
  })

  it('a failed install keeps the app usable and does not relaunch', async () => {
    const update = fakeUpdate('0.4.0')
    update.downloadAndInstall.mockRejectedValue(new Error('disk full'))
    check.mockResolvedValue(update)

    const { result } = renderHook(() => useUpdateCheck())
    await waitFor(() => expect(result.current.update).not.toBeNull())

    await act(async () => {
      await result.current.install()
    })

    expect(relaunch).not.toHaveBeenCalled()
    // Cleared so the user can retry from the same prompt.
    expect(result.current.installing).toBe(false)
    expect(result.current.update).not.toBeNull()
  })

  it('dismiss() hides the prompt for this session', async () => {
    check.mockResolvedValue(fakeUpdate('0.4.0'))

    const { result } = renderHook(() => useUpdateCheck())
    await waitFor(() => expect(result.current.update).not.toBeNull())
    expect(result.current.dismissed).toBe(false)

    act(() => result.current.dismiss())

    expect(result.current.dismissed).toBe(true)
  })
})
