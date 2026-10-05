import '@testing-library/jest-dom/vitest'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BootScreen, BOOT_REMARKS } from './BootScreen'
import { useBoot } from '@/hooks/useBoot'

vi.mock('@/hooks/useBoot')

describe('BootScreen', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('shows a Novi remark instead of backend progress text', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0)
    vi.mocked(useBoot).mockReturnValue({
      phase: 'hydrating',
      percent: 25,
      message: 'Recalling our conversations…',
      retry: vi.fn(),
    })

    const { container } = render(<BootScreen />)

    expect(container.querySelector('.novi-boot__status')?.textContent).toBe(BOOT_REMARKS[0])
    expect(screen.queryByText('Recalling our conversations…')).not.toBeInTheDocument()
  })

  it('provides 25 unique waiting remarks', () => {
    expect(BOOT_REMARKS).toHaveLength(25)
    expect(new Set(BOOT_REMARKS)).toHaveLength(25)
  })

  it('schedules remark changes every ten seconds', () => {
    const setIntervalSpy = vi.spyOn(window, 'setInterval')
    vi.mocked(useBoot).mockReturnValue({
      phase: 'hydrating',
      percent: 25,
      message: 'Recalling our conversations…',
      retry: vi.fn(),
    })

    render(<BootScreen />)

    expect(setIntervalSpy).toHaveBeenCalledWith(expect.any(Function), 10_000)
  })

  it('shows retry when error', () => {
    const retry = vi.fn()
    vi.mocked(useBoot).mockReturnValue({
      phase: 'error',
      percent: 25,
      message: 'Failed to load projects',
      error: 'projects down',
      retry,
    })

    render(<BootScreen />)

    expect(screen.getByText(/projects down/)).toBeInTheDocument()
    screen.getByRole('button', { name: /try again/i }).click()
    expect(retry).toHaveBeenCalled()
  })

  it('never uses "your" in boot copy', () => {
    vi.mocked(useBoot).mockReturnValue({
      phase: 'hydrating',
      percent: 50,
      message: 'Catching up on our memory…',
      retry: vi.fn(),
    })

    const { container } = render(<BootScreen />)

    const status = container.querySelector('.novi-boot__status')?.textContent ?? ''
    expect(status.toLowerCase()).not.toMatch(/your/)
  })
})
