import '@testing-library/jest-dom/vitest'
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BootScreen } from './BootScreen'

describe('BootScreen', () => {
  it('renders warm our copy for current step and detail', () => {
    const { container } = render(
      <BootScreen
        state={{
          phase: 'hydrating',
          step: 'conversations',
          loaded: 0,
          total: 4,
          detail: '3 conversations',
          retry: vi.fn(),
        }}
      />,
    )
    // status + steps both contain the copy — assert status holds it and detail is present
    expect(container.querySelector('.novi-boot__status')?.textContent).toBe('Recalling our conversations…')
    expect(screen.getAllByText('Recalling our conversations…').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('3 conversations')).toBeInTheDocument()
  })

  it('shows retry when error', () => {
    const retry = vi.fn()
    render(
      <BootScreen
        state={{
          phase: 'error',
          step: 'projects',
          loaded: 1,
          total: 4,
          error: 'projects down',
          retry,
        }}
      />,
    )
    expect(screen.getByText(/projects down/)).toBeInTheDocument()
    screen.getByRole('button', { name: /retry/i }).click()
    expect(retry).toHaveBeenCalled()
  })

  it('never uses "your"', () => {
    const { container } = render(
      <BootScreen
        state={{
          phase: 'hydrating',
          step: 'timeline',
          loaded: 2,
          total: 4,
          retry: vi.fn(),
        }}
      />,
    )
    // boot copy (status + steps) must use "our" framing, never "your" — foot is exempt
    const status = container.querySelector('.novi-boot__status')?.textContent ?? ''
    const steps = container.querySelector('.novi-boot__steps')?.textContent ?? ''
    const bootCopy = `${status} ${steps}`
    expect(bootCopy).not.toMatch(/your/i)
    expect(bootCopy).toMatch(/our/i)
    // also ensure no "your" leaked into status/detail area
    expect(status.toLowerCase()).not.toMatch(/your/)
  })
})
